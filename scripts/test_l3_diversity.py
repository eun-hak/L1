#!/usr/bin/env python3
"""
L3 다양성 A/B 테스트 — 기존(50개) vs 개선(15개+슬롯+stem검증).

Usage:
  python scripts/test_l3_diversity.py
  python scripts/test_l3_diversity.py --l2 09-gangnam-food
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))

from groq_client import (  # noqa: E402
    MODEL_FAST as SCOUT_MODEL,
    TAXONOMY_SYSTEM,
    _is_similar_keyword,
    _keyword_seen,
    _valid_focus_keyword,
    chat as scout_chat,
    generate_l3_topics as baseline_generate,
    slugify_en,
)
from gemini_client import MODEL_FAST as GEMINI_MODEL, chat as gemini_chat, parse_json_array  # noqa: E402

L1_CSV = ROOT / "data2" / "seed" / "topics_l1.csv"
L2_CSV = ROOT / "data2" / "topics_l2.csv"
L3_CSV = ROOT / "data2" / "topics_l3.csv"
BRIEF = ROOT / "data2" / "brief.txt"
OUT_DIR = ROOT / "data2" / "test" / "l3_diversity"

DEFAULT_L2 = [
    "11-travel-essentials",
    "09-gangnam-food",
    "10-air-fryer-recipes",
]

INTENT_SUFFIX = {
    "추천", "레시피", "방법", "비교", "가이드", "일정", "후기", "분석", "정리", "소개",
    "팁", "순위", "가격", "리스트", "안내", "체크리스트", "예측", "근황", "라인업",
    "트렌드", "입문", "초보", "무료", "유튜브", "다운로드", "신청", "예약", "위치",
    "맛집", "카페", "여행", "축제", "음식", "음악", "의상", "문화", "전통", "온라인",
    "인기", "고전", "초보자", "스타일링", "관리", "점검", "운동", "식단", "루틴",
    "사용법", "리뷰", "전략", "법", "확인", "준비물", "체험", "일정표", "필수품", "필수",
}

VALID_INTENTS = frozenset({"info", "howto", "compare", "cost", "checklist", "review", "news"})
VALID_ANGLES = frozenset({"start", "howto", "compare", "tip", "review", "issue", "local"})


def stem_keyword(text: str) -> str:
    tokens = text.strip().split()
    while tokens and tokens[-1] in INTENT_SUFFIX:
        tokens.pop()
    return " ".join(tokens) if tokens else text.strip()


def load_l2(l2_id: str) -> dict[str, str]:
    with L2_CSV.open(encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            if row["l2_id"] == l2_id:
                return row
    raise KeyError(l2_id)


def load_l1(l1_code: str) -> dict[str, str]:
    with L1_CSV.open(encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            if row["l1_code"] == l1_code:
                return row
    return {}


def load_existing_l3(l2_id: str, limit: int = 50) -> list[str]:
    if not L3_CSV.exists():
        return []
    out: list[str] = []
    with L3_CSV.open(encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            if row["l2_id"] == l2_id:
                out.append(row["focus_keyword"])
    return out[:limit]


def analyze_keywords(keywords: list[str]) -> dict:
    if not keywords:
        return {"count": 0}
    stems = [stem_keyword(k) for k in keywords]
    stem_counts = Counter(stems)
    dup_stems = {s: c for s, c in stem_counts.items() if c > 1}
    suffix_hits = sum(1 for k in keywords if stem_keyword(k) != k.strip())
    intent_combo = [
        f"{stem_keyword(k)}"
        for k in keywords
    ]
    # crude template: ends with same last token
    last_tokens = Counter(k.split()[-1] if k.split() else k for k in keywords)
    top_suffix = last_tokens.most_common(5)
    recommend_count = sum(1 for k in keywords if k.endswith("추천"))
    checklist_count = sum(1 for k in keywords if "체크리스트" in k or k.endswith("준비물"))
    return {
        "count": len(keywords),
        "stem_unique": len(stem_counts),
        "stem_unique_pct": round(100 * len(stem_counts) / len(keywords), 1),
        "stem_duplicates": dup_stems,
        "suffix_variant_pct": round(100 * suffix_hits / len(keywords), 1),
        "ends_with_추천": recommend_count,
        "prep_checklist_like": checklist_count,
        "top_last_token": top_suffix,
        "keywords": keywords,
        "stems": stems,
    }


def _parse_l3_items(raw: str) -> list[dict[str, str]]:
    items = parse_json_array(raw, raise_on_fail=False)
    out: list[dict[str, str]] = []
    for item in items:
        focus = str(item.get("focus_keyword", "")).strip()
        if not focus:
            continue
        intent = str(item.get("search_intent", "info")).strip().lower()
        angle = str(item.get("topic_angle", "tip")).strip().lower()
        if intent not in VALID_INTENTS:
            intent = "info"
        if angle not in VALID_ANGLES:
            angle = "tip"
        out.append({
            "focus_keyword": focus,
            "title_ko": str(item.get("title_ko", "") or item.get("seo_title", "")).strip() or focus,
            "slug": slugify_en(str(item.get("slug", "")).strip()) or slugify_en(focus),
            "search_intent": intent,
            "topic_angle": angle,
            "description": str(item.get("description", "")).strip(),
        })
    return out


def generate_slots(
    *,
    l1_name: str,
    l1_desc: str,
    l2_name: str,
    l2_desc: str,
    count: int,
    brief: str,
) -> list[dict[str, str]]:
    prompt = f"""L2 "{l2_name}"에서 서로 다른 검색 의도 슬롯 {count}개를 설계해.

[L1] {l1_name} — {l1_desc}
[L2] {l2_name} — {l2_desc}

[규칙]
- 각 슬롯은 3~8단어 한국어 명사구 (focus_keyword 줄기 수준)
- 서로 다른 축: 대상(아기/노인), 상황(기내/캠핑), 목적(의약품/보험), 장소, 방법, 비교, 실수, 법규 등
- 기간·계절만 다른 변형 금지 (단기/장기/여름/겨울 나열 X)
- 끝수식어 나열 금지 (~추천 5개, ~방법 5개 X)
- 각 슬롯에 search_intent, topic_angle 지정 (서로 다르게)
- JSON 배열만:
[{{"slot": "아기와 함께 여행 짐 싸기", "search_intent": "howto", "topic_angle": "tip"}}]
"""
    raw = scout_chat(prompt, system=TAXONOMY_SYSTEM, model=SCOUT_MODEL, temperature=0.8)
    items = parse_json_array(raw, raise_on_fail=False)
    slots: list[dict[str, str]] = []
    seen_stems: set[str] = set()
    for item in items:
        slot = str(item.get("slot", "")).strip()
        if not slot:
            continue
        stem = stem_keyword(slot)
        if stem in seen_stems:
            continue
        seen_stems.add(stem)
        intent = str(item.get("search_intent", "info")).strip().lower()
        angle = str(item.get("topic_angle", "tip")).strip().lower()
        if intent not in VALID_INTENTS:
            intent = "info"
        if angle not in VALID_ANGLES:
            angle = "tip"
        slots.append({"slot": slot, "search_intent": intent, "topic_angle": angle})
    return slots[:count]


def generate_diverse_l3(
    *,
    l1_name: str,
    l1_desc: str,
    l2_name: str,
    l2_desc: str,
    count: int,
    brief: str,
    slots: list[dict[str, str]],
) -> list[dict[str, str]]:
    slot_lines = "\n".join(
        f"- {s['slot']} (intent={s['search_intent']}, angle={s['topic_angle']})"
        for s in slots
    )
    prompt = f"""아래 검색 의도 슬롯 각각에 대해 L3 키워드 1개씩만 작성해 (총 {len(slots)}개).

[L1] {l1_name}
[L2] {l2_name} — {l2_desc}

[의도 슬롯 — focus_keyword 줄기는 슬롯과 같은 주제, 수식어만 자연스럽게]
{slot_lines}

[규칙]
- 슬롯 1개 = JSON 객체 1개 (순서 동일)
- focus_keyword: 슬롯과 동일 주제, 2~10단어
- title_ko: SEO 제목 25~55자
- slug: kebab-case
- search_intent, topic_angle은 슬롯 값 그대로 사용
- description: 1문장
- 슬롯끼리 주제 중복 금지
- JSON 배열만 출력
"""
    raw = scout_chat(prompt, system=TAXONOMY_SYSTEM, model=SCOUT_MODEL, temperature=0.7)
    return _parse_l3_items(raw)


def filter_diverse(items: list[dict[str, str]], *, max_suffix: int = 2) -> tuple[list[dict[str, str]], list[str]]:
    accepted: list[dict[str, str]] = []
    rejected: list[str] = []
    seen_stems: set[str] = set()
    seen_keywords: list[str] = []
    suffix_counter: Counter[str] = Counter()
    intent_angle_counter: Counter[str] = Counter()

    for item in items:
        kw = item["focus_keyword"]
        stem = stem_keyword(kw)
        suffix = kw.split()[-1] if kw.split() else kw
        ia = f"{item['search_intent']}:{item['topic_angle']}"

        reasons: list[str] = []
        if not _valid_focus_keyword(kw):
            reasons.append("invalid_keyword")
        if stem in seen_stems or _keyword_seen(kw, seen_keywords):
            reasons.append("stem_dup")
        if suffix in INTENT_SUFFIX and suffix_counter[suffix] >= max_suffix:
            reasons.append(f"suffix_cap:{suffix}")
        if intent_angle_counter[ia] >= 6:
            reasons.append("intent_angle_cap")

        if reasons:
            rejected.append(f"{kw} ({', '.join(reasons)})")
            continue

        seen_stems.add(stem)
        seen_keywords.append(kw)
        suffix_counter[suffix] += 1
        intent_angle_counter[ia] += 1
        accepted.append(item)

    return accepted, rejected


def gemini_dedup_slots(slots: list[dict[str, str]], l2_name: str) -> list[dict[str, str]]:
    prompt = f"""L2 "{l2_name}" 검색 의도 슬롯 목록에서 의미 중복을 제거해.

슬롯:
{json.dumps(slots, ensure_ascii=False)}

- 기간/계절만 다른 것은 1개만 남김
- 같은 줄기(준비물/추천/체크리스트) 변형은 1개만
- 최대한 서로 다른 주제만 accept

JSON: [{{"slot": "...", "search_intent": "...", "topic_angle": "...", "verdict": "accept|reject", "reason": ""}}]
"""
    raw = gemini_chat(prompt, system=TAXONOMY_SYSTEM, model=GEMINI_MODEL, temperature=0.2)
    items = parse_json_array(raw, raise_on_fail=False)
    by_slot = {s["slot"]: s for s in slots}
    out: list[dict[str, str]] = []
    for item in items:
        if str(item.get("verdict", "")).strip() == "accept":
            slot = str(item.get("slot", "")).strip()
            if slot and slot in by_slot:
                out.append(by_slot[slot])
    return out if out else slots


def run_l2_test(l2_id: str, *, count: int, brief: str) -> dict:
    l2 = load_l2(l2_id)
    l1 = load_l1(l2["l1_code"])
    l1_name = l1.get("name_ko", l2["l1_code"])
    l1_desc = l1.get("description", "")
    l2_name = l2["name_ko"]
    l2_desc = l2.get("description", "")

    existing = load_existing_l3(l2_id, limit=50)
    existing_metrics = analyze_keywords(existing)

    # A: baseline prompt (old style), 1 call
    baseline_raw = baseline_generate(
        brief=brief,
        l1_name=l1_name,
        l1_description=l1_desc,
        l2_name=l2_name,
        l2_description=l2_desc,
        count=count + 5,
        avoid_keywords=[],
        model=SCOUT_MODEL,
    )
    baseline_items = baseline_raw[: count + 5]
    baseline_kw = [x["focus_keyword"] for x in baseline_items[:count]]
    baseline_metrics = analyze_keywords(baseline_kw)

    time.sleep(1.0)

    # B: diverse pipeline — slots → gemini dedup → fill → filter
    slots = generate_slots(
        l1_name=l1_name,
        l1_desc=l1_desc,
        l2_name=l2_name,
        l2_desc=l2_desc,
        count=count + 5,
        brief=brief,
    )
    time.sleep(1.0)
    slots = gemini_dedup_slots(slots[: count + 5], l2_name)
    time.sleep(1.0)
    diverse_raw = generate_diverse_l3(
        l1_name=l1_name,
        l1_desc=l1_desc,
        l2_name=l2_name,
        l2_desc=l2_desc,
        count=count,
        brief=brief,
        slots=slots[:count],
    )
    diverse_accepted, diverse_rejected = filter_diverse(diverse_raw)
    diverse_kw = [x["focus_keyword"] for x in diverse_accepted[:count]]
    diverse_metrics = analyze_keywords(diverse_kw)

    return {
        "l2_id": l2_id,
        "l2_name": l2_name,
        "target_count": count,
        "existing_nemotron_50": existing_metrics,
        "baseline_scout_15": baseline_metrics,
        "diverse_pipeline_15": diverse_metrics,
        "diverse_slots": slots[:count],
        "diverse_rejected": diverse_rejected,
        "diverse_items": diverse_accepted[:count],
        "baseline_items": baseline_items[:count],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="L3 diversity A/B test")
    parser.add_argument("--l2", action="append", default=None)
    parser.add_argument("--count", type=int, default=15)
    args = parser.parse_args()

    l2_ids = args.l2 or DEFAULT_L2
    brief = BRIEF.read_text(encoding="utf-8").strip() if BRIEF.exists() else ""
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    results = []
    scout_calls = 0
    gemini_calls = 0

    print(f"L3 다양성 테스트 — L2 {len(l2_ids)}개 × {args.count}개\n")

    for l2_id in l2_ids:
        print(f"[{l2_id}] ...", flush=True)
        result = run_l2_test(l2_id, count=args.count, brief=brief)
        results.append(result)
        scout_calls += 3  # baseline + slots + fill
        gemini_calls += 1

        ex = result["existing_nemotron_50"]
        bl = result["baseline_scout_15"]
        dv = result["diverse_pipeline_15"]
        print(f"  기존(Nemotron 50): stem유일 {ex.get('stem_unique_pct')}% | 추천 {ex.get('ends_with_추천')} | 준비물류 {ex.get('prep_checklist_like')}")
        print(f"  baseline(Scout 15): stem유일 {bl.get('stem_unique_pct')}% | 추천 {bl.get('ends_with_추천')} | 준비물류 {bl.get('prep_checklist_like')}")
        print(f"  diverse(슬롯+검증): stem유일 {dv.get('stem_unique_pct')}% | 추천 {dv.get('ends_with_추천')} | 준비물류 {dv.get('prep_checklist_like')} | 수락 {dv.get('count')}")
        if dv.get("stem_duplicates"):
            print(f"    dup stems: {dv['stem_duplicates']}")
        time.sleep(1.5)

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "scout_calls": scout_calls,
        "gemini_calls": gemini_calls,
        "count_per_l2": args.count,
        "results": results,
    }
    out_json = OUT_DIR / "l3_diversity_report.json"
    out_json.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    # summary CSV
    csv_path = OUT_DIR / "l3_diversity_summary.csv"
    with csv_path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=[
            "l2_id", "method", "count", "stem_unique_pct", "ends_with_추천", "prep_checklist_like",
        ])
        w.writeheader()
        for r in results:
            for method, key in [
                ("existing_nemotron", "existing_nemotron_50"),
                ("baseline_scout", "baseline_scout_15"),
                ("diverse_pipeline", "diverse_pipeline_15"),
            ]:
                m = r[key]
                w.writerow({
                    "l2_id": r["l2_id"],
                    "method": method,
                    "count": m.get("count", 0),
                    "stem_unique_pct": m.get("stem_unique_pct", 0),
                    "ends_with_추천": m.get("ends_with_추천", 0),
                    "prep_checklist_like": m.get("prep_checklist_like", 0),
                })

    print(f"\n완료 → {out_json}")
    print(f"      → {csv_path}")
    print(f"API: Scout ~{scout_calls}회, Gemini ~{gemini_calls}회")


if __name__ == "__main__":
    main()
