#!/usr/bin/env python3
"""
topics_l3.csv 후처리: 중복 병합, L2 cap, 품질 tier 분류 (LLM API 없음).

입력:  data2/topics_l3.csv
출력:
  data2/topics_l3_curated.csv   — publish + edit (발행·수정 큐)
  data2/topics_l3_hold.csv      — hold (보류)
  data2/topics_l3_dropped.csv   — drop + 병합·cap 제거 (사유 포함)
  data2/reports/l3_curate_report.json

Usage:
  python scripts/dedup_l3_topics.py
  python scripts/dedup_l3_topics.py --input data2/topics_l3.csv --per-l2 50
"""

from __future__ import annotations

import argparse
import csv
import json
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_IN = ROOT / "data2" / "topics_l3.csv"
OUT_CURATED = ROOT / "data2" / "topics_l3_curated.csv"
OUT_HOLD = ROOT / "data2" / "topics_l3_hold.csv"
OUT_DROPPED = ROOT / "data2" / "topics_l3_dropped.csv"
OUT_REPORT = ROOT / "data2" / "reports" / "l3_curate_report.json"

SOURCE_FIELDS = [
    "l3_id", "l2_id", "l1_code", "l3_slug", "focus_keyword", "title_ko",
    "search_intent", "topic_angle", "description", "model", "generated_at",
]
EXTRA_FIELDS = ["tier", "quality_score", "drop_reason"]

# L1: 우선 발행 vs 신중 vs 뉴스성
L1_PUBLISH_BOOST = {"09", "10", "11", "13", "14", "06", "08", "07", "04"}
L1_CAUTION = {"02", "03"}
L1_NEWSY = {"01", "05"}

INTENT_SUFFIX = {
    "추천", "레시피", "방법", "비교", "가이드", "일정", "후기", "분석", "정리", "소개",
    "팁", "순위", "가격", "리스트", "안내", "체크리스트", "예측", "근황", "라인업",
    "트렌드", "입문", "초보", "무료", "유튜브", "다운로드", "신청", "예약", "위치",
    "맛집", "카페", "여행", "축제", "음식", "음악", "의상", "문화", "전통", "온라인",
    "인기", "고전", "초보자", "스타일링", "관리", "점검", "운동", "식단", "루틴",
    "사용법", "리뷰", "전략", "법", "확인", "준비물", "체험", "일정표",
}

DROP_PATTERNS = [
    (re.compile(r"베팅|배팅|도박|카지노|승부율"), "betting_gambling"),
    (re.compile(r"\byear\b", re.I), "english_year_token"),
    (re.compile(r"털실\s*핀다"), "awkward_keyword"),
    (re.compile(r"수면\s*중\s*간부장"), "awkward_keyword"),
    (re.compile(r"세레브"), "awkward_keyword"),
    (re.compile(r"가방.*모자|모자.*가방"), "mixed_topic"),
    (re.compile(r"물가.*자녀동아|자녀동아.*물가"), "mixed_topic"),
    (re.compile(r"salon", re.I), "english_in_keyword"),
]

HOLD_YEAR_L1 = L1_NEWSY  # 2024/2025 + 연예/스포츠 → hold

PRACTICAL_HINTS = re.compile(
    r"체크리스트|준비물|점검|주의사항|확인할|방법|레시피|관리|팁|비교 기준|"
    r"신청|예약|일정|코스|루틴|세탁|보관|충전|설치|교체|청소"
)


def stem_keyword(text: str) -> str:
    tokens = text.strip().split()
    while tokens and tokens[-1] in INTENT_SUFFIX:
        tokens.pop()
    return " ".join(tokens) if tokens else text.strip()


def norm_title(text: str) -> str:
    s = text.strip().lower()
    s = re.sub(r"20\d{2}", "", s)
    s = re.sub(r"[^\w가-힣]+", "", s)
    return s


def norm_slug(slug: str) -> str:
    return slug.strip().lower()


def has_year(text: str, years: tuple[str, ...]) -> bool:
    return any(y in text for y in years)


def hard_drop_reason(row: dict[str, str]) -> str | None:
    blob = f"{row['focus_keyword']} {row['title_ko']} {row['description']}"
    for pat, reason in DROP_PATTERNS:
        if pat.search(blob):
            return reason
    if re.search(r"[가-힣]{2,}.*\b[A-Za-z]{4,}\b|[A-Za-z]{4,}.*[가-힣]{4,}", row["focus_keyword"]):
        if not re.search(r"K-POP|OTT|Netflix|EV|AI|PC|IT|USB|GPS", row["focus_keyword"], re.I):
            return "korean_english_mix"
    return None


def quality_score(row: dict[str, str]) -> int:
    fk = row["focus_keyword"]
    title = row["title_ko"]
    l1 = row["l1_code"]
    intent = row["search_intent"]
    angle = row["topic_angle"]
    blob = f"{fk} {title}"

    score = 55

    if l1 in L1_PUBLISH_BOOST:
        score += 18
    elif l1 in L1_CAUTION:
        score += 5
    elif l1 in L1_NEWSY:
        score += 2

    if PRACTICAL_HINTS.search(blob):
        score += 12
    if 3 <= len(fk.split()) <= 10:
        score += 5
    if intent in ("howto", "checklist", "compare") and angle in ("howto", "tip", "local", "start"):
        score += 6
    if intent == "news" or angle == "issue":
        score -= 8

    if has_year(blob, ("2023",)):
        score -= 35
    if has_year(blob, ("2024", "2025")):
        score -= 8
        if l1 in L1_NEWSY:
            score -= 12

    if l1 in L1_CAUTION:
        if re.search(r"추천|최고|완벽|무조건|반드시", blob):
            score -= 6
        if re.search(r"치료|약물|약\s|시술|수술|보조제|투자\s*전략|대출", blob):
            score -= 5

    if re.search(r"^(.*)\s+(추천|방법|팁)$", fk) and stem_keyword(fk) != fk:
        score -= 4

    if row.get("_dup_penalty"):
        score -= 100

    return max(0, min(100, score))


def suggest_tier(row: dict[str, str], score: int) -> str:
    if row.get("drop_reason"):
        return "drop"
    fk = row["focus_keyword"]
    title = row["title_ko"]
    l1 = row["l1_code"]
    blob = f"{fk} {title}"

    if has_year(blob, ("2023",)):
        return "hold"
    if has_year(blob, ("2024", "2025")) and l1 in HOLD_YEAR_L1:
        if re.search(r"일정|수상|결과|근황|복귀|열애|이혼|논란|우승|스코어|중계", blob):
            return "hold"
    if l1 in L1_CAUTION and re.search(r"추천|비교|투자|보조금|치료|약", blob):
        return "edit"
    if has_year(blob, ("2024", "2025")):
        return "edit"
    if score >= 72:
        return "publish"
    if score >= 52:
        return "edit"
    if score >= 38:
        return "hold"
    return "hold"


def load_rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def write_csv(path: Path, rows: list[dict[str, str]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def dedupe_global_key(
    rows: list[dict[str, str]],
    key_fn,
    reason: str,
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    """동일 key(전역) — quality_score 최고 1개."""
    by_key: dict[str, list[dict[str, str]]] = defaultdict(list)
    keep: list[dict[str, str]] = []
    dropped: list[dict[str, str]] = []

    for r in rows:
        k = key_fn(r)
        if not k:
            keep.append(r)
        else:
            by_key[k].append(r)

    for bucket in by_key.values():
        if len(bucket) == 1:
            keep.append(bucket[0])
            continue
        best = max(bucket, key=lambda x: x.get("quality_score", 0))
        keep.append(best)
        for r in bucket:
            if r["l3_id"] != best["l3_id"]:
                d = dict(r)
                d["drop_reason"] = reason
                d["tier"] = "drop"
                dropped.append(d)
    return keep, dropped


def dedupe_within_l2(
    rows: list[dict[str, str]],
    key_fn,
    reason: str,
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    """같은 L2·같은 key — quality_score 높은 1개만 유지."""
    by_bucket: dict[tuple[str, str], list[dict[str, str]]] = defaultdict(list)
    keep: list[dict[str, str]] = []
    dropped: list[dict[str, str]] = []

    for r in rows:
        k = key_fn(r)
        if not k:
            keep.append(r)
        else:
            by_bucket[(r["l2_id"], k)].append(r)

    for bucket in by_bucket.values():
        if len(bucket) == 1:
            keep.append(bucket[0])
            continue
        best = max(bucket, key=lambda x: x.get("quality_score", 0))
        keep.append(best)
        for r in bucket:
            if r["l3_id"] != best["l3_id"]:
                d = dict(r)
                d["drop_reason"] = reason
                d["tier"] = "drop"
                dropped.append(d)

    return keep, dropped


def cap_intent_angle(rows: list[dict[str, str]], max_per_combo: int = 18) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    by_l2_combo: dict[tuple[str, str, str], list[dict[str, str]]] = defaultdict(list)
    for r in rows:
        combo = (r["l2_id"], r["search_intent"], r["topic_angle"])
        by_l2_combo[combo].append(r)

    drop_ids: set[str] = set()
    dropped: list[dict[str, str]] = []
    for combo, bucket in by_l2_combo.items():
        if len(bucket) <= max_per_combo:
            continue
        bucket.sort(key=lambda x: x.get("quality_score", 0), reverse=True)
        for r in bucket[max_per_combo:]:
            drop_ids.add(r["l3_id"])
            d = dict(r)
            d["drop_reason"] = f"intent_angle_cap:{combo[1]}+{combo[2]}"
            d["tier"] = "drop"
            dropped.append(d)

    keep = [r for r in rows if r["l3_id"] not in drop_ids]
    return keep, dropped


def cap_per_l2(rows: list[dict[str, str]], limit: int) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    by_l2: dict[str, list[dict[str, str]]] = defaultdict(list)
    for r in rows:
        by_l2[r["l2_id"]].append(r)

    keep_all: list[dict[str, str]] = []
    dropped: list[dict[str, str]] = []
    for l2, bucket in by_l2.items():
        bucket.sort(key=lambda x: x.get("quality_score", 0), reverse=True)
        keep_all.extend(bucket[:limit])
        for r in bucket[limit:]:
            d = dict(r)
            d["drop_reason"] = "per_l2_cap"
            d["tier"] = "drop"
            dropped.append(d)
    return keep_all, dropped


def curate_pipeline(
    raw: list[dict[str, str]],
    *,
    per_l2: int,
    max_intent_combo: int,
) -> tuple[list[dict[str, str]], list[dict[str, str]], dict]:
    """중복 제거·cap 적용. (survivors, dropped, stats) 반환."""
    stats: dict = {"input_rows": len(raw), "phases": {}}

    # Phase 0: hard drop
    survivors: list[dict[str, str]] = []
    hard_dropped: list[dict[str, str]] = []
    for r in raw:
        reason = hard_drop_reason(r)
        if reason:
            d = {**r, "drop_reason": reason, "tier": "drop", "quality_score": 0}
            hard_dropped.append(d)
        else:
            survivors.append(dict(r))

    stats["phases"]["hard_drop"] = len(hard_dropped)

    # Score before dedupe
    for r in survivors:
        r["quality_score"] = quality_score(r)

    all_dropped: list[dict[str, str]] = list(hard_dropped)

    # Phase 1: slug dup (동일 slug가 여러 L2에 있으면 전역 1개만 유지)
    survivors, d1 = dedupe_global_key(
        survivors,
        lambda r: norm_slug(r["l3_slug"]) if r["l3_slug"] else "",
        "duplicate_slug",
    )
    all_dropped.extend(d1)
    stats["phases"]["duplicate_slug"] = len(d1)

    # Phase 2: norm title
    survivors, d2 = dedupe_within_l2(
        survivors,
        lambda r: norm_title(r["title_ko"]) if r["title_ko"] else "",
        "duplicate_title",
    )
    all_dropped.extend(d2)
    stats["phases"]["duplicate_title"] = len(d2)

    # Phase 3: stem within L2
    survivors, d3 = dedupe_within_l2(
        survivors,
        lambda r: stem_keyword(r["focus_keyword"]),
        "duplicate_stem",
    )
    all_dropped.extend(d3)
    stats["phases"]["duplicate_stem"] = len(d3)

    # Re-score
    for r in survivors:
        r["quality_score"] = quality_score(r)

    # Phase 4: intent×angle cap
    survivors, d4 = cap_intent_angle(survivors, max_per_combo=max_intent_combo)
    all_dropped.extend(d4)
    stats["phases"]["intent_angle_cap"] = len(d4)

    # Phase 5: tier assignment
    for r in survivors:
        r["tier"] = suggest_tier(r, r["quality_score"])
        r["drop_reason"] = ""

    # Phase 6: per L2 cap (only among non-drop tiers — include hold/edit/publish)
    cap_pool = [r for r in survivors if r["tier"] != "drop"]
    capped, d5 = cap_per_l2(cap_pool, per_l2)
    cap_ids = {r["l3_id"] for r in capped}
    for r in survivors:
        if r["l3_id"] not in cap_ids and r["tier"] != "drop":
            d = dict(r)
            d["drop_reason"] = "per_l2_cap"
            d["tier"] = "drop"
            all_dropped.append(d)
    survivors = capped
    stats["phases"]["per_l2_cap"] = len(d5)

    tier_counts = Counter(r["tier"] for r in survivors)
    l1_tier = Counter((r["l1_code"], r["tier"]) for r in survivors)
    per_l2_counts = Counter(r["l2_id"] for r in survivors)

    stats.update({
        "output_curated": sum(1 for r in survivors if r["tier"] in ("publish", "edit")),
        "output_hold": sum(1 for r in survivors if r["tier"] == "hold"),
        "output_dropped": len(all_dropped),
        "survivors_total": len(survivors),
        "tier_counts": dict(tier_counts),
        "l1_tier_top": {f"{a}:{t}": c for (a, t), c in l1_tier.most_common(30)},
        "l2_under_target": sum(1 for c in per_l2_counts.values() if c < per_l2),
        "l2_at_target": sum(1 for c in per_l2_counts.values() if c == per_l2),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    })

    report = {
        **stats,
        "curated_tier_split": dict(
            Counter(r["tier"] for r in survivors if r["tier"] in ("publish", "edit"))
        ),
    }
    return survivors, all_dropped, report


def run(input_path: Path, per_l2: int, max_intent_combo: int) -> dict:
    raw = load_rows(input_path)
    survivors, all_dropped, report = curate_pipeline(
        raw, per_l2=per_l2, max_intent_combo=max_intent_combo
    )

    curated = [r for r in survivors if r["tier"] in ("publish", "edit")]
    hold = [r for r in survivors if r["tier"] == "hold"]

    out_fields = SOURCE_FIELDS + EXTRA_FIELDS
    write_csv(OUT_CURATED, curated, out_fields)
    write_csv(OUT_HOLD, hold, out_fields)
    write_csv(OUT_DROPPED, all_dropped, out_fields)

    report = {
        **report,
        "paths": {
            "curated": str(OUT_CURATED),
            "hold": str(OUT_HOLD),
            "dropped": str(OUT_DROPPED),
        },
        "curated_tier_split": dict(Counter(r["tier"] for r in curated)),
    }
    OUT_REPORT.parent.mkdir(parents=True, exist_ok=True)
    OUT_REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def main() -> None:
    p = argparse.ArgumentParser(description="L3 topics post-process (no LLM)")
    p.add_argument("--input", type=Path, default=DEFAULT_IN)
    p.add_argument("--per-l2", type=int, default=50)
    p.add_argument("--max-intent-combo", type=int, default=18)
    args = p.parse_args()
    report = run(args.input, args.per_l2, args.max_intent_combo)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
