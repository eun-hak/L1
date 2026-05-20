#!/usr/bin/env python3
"""
Generate topics_l3.csv and questions.csv (~10,000 unique SEO questions).

원칙:
- L2 하나 아래 여러 L3 가능, 단 focus_keyword(실제 주제)가 모두 달라야 함
- question_text = SEO 블로그 제목, L3당 1개
"""

from __future__ import annotations

import csv
import hashlib
import math
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
from focus_phrases import FOCUS_PHRASES, MEGA_PAIR, PAIR_FOCUS  # noqa: E402
DATA = ROOT / "data"

TARGET_QUESTIONS = 10_000

TopicSlot = tuple[str, str]

TOPIC_SLOTS: dict[str, list[TopicSlot]] = {
    "라이프": [
        ("first-step", "start"),
        ("life-change", "benefit"),
        ("one-month", "challenge"),
        ("common-mistake", "pitfall"),
        ("time-slot", "routine"),
        ("compare-style", "choose"),
        ("with-friend", "social"),
        ("light-habit", "habit"),
        ("seasonal", "season"),
        ("solo", "solo"),
    ],
    "머니": [
        ("beginner-map", "start"),
        ("checklist", "checklist"),
        ("cost-range", "cost"),
        ("trap", "risk"),
        ("compare-option", "compare"),
        ("twenty-salary", "budget"),
        ("family-case", "context"),
        ("next-action", "next"),
        ("timeline", "timeline"),
        ("document", "document"),
    ],
    "홈": [
        ("small-space", "space"),
        ("budget", "budget"),
        ("diy-vs-pro", "choose"),
        ("before-after", "prep"),
        ("season", "season"),
        ("mistake", "pitfall"),
        ("daily-routine", "routine"),
        ("with-kids", "family"),
        ("repair", "fix"),
        ("energy", "life"),
    ],
    "푸드": [
        ("home-try", "home"),
        ("pairing", "pairing"),
        ("allergy-diet", "diet"),
        ("price-value", "value"),
        ("solo", "solo"),
        ("region", "local"),
        ("beginner-order", "start"),
        ("gift", "gift"),
        ("night", "solo"),
        ("meal-prep", "routine"),
    ],
    "스타일": [
        ("tone-match", "match"),
        ("daily-vs-event", "context"),
        ("minimal-routine", "routine"),
        ("ingredient", "ingredient"),
        ("budget-brand", "budget"),
        ("mistake", "pitfall"),
        ("men-women", "demographic"),
        ("storage-care", "care"),
        ("season", "season"),
        ("review", "value"),
    ],
    "바디": [
        ("beginner-safe", "safe"),
        ("pain-care", "recovery"),
        ("meal-with", "diet"),
        ("checkup", "medical"),
        ("home-vs-gym", "place"),
        ("plateau", "plateau"),
        ("gear", "gear"),
        ("sleep-stress", "lifestyle"),
        ("injury", "recovery"),
        ("age", "demographic"),
    ],
    "패밀리": [
        ("by-age", "age"),
        ("couple-parent", "couple"),
        ("cost-plan", "budget"),
        ("first-time", "first"),
        ("pet-safety", "safety"),
        ("school-hospital", "institution"),
        ("burnout-parent", "burnout"),
        ("grandparent", "extended"),
        ("twin", "family"),
        ("prep", "prep"),
    ],
    "트립": [
        ("itinerary-1day", "itinerary"),
        ("budget-trip", "budget"),
        ("with-kids", "family"),
        ("weather-season", "season"),
        ("local-tip", "local"),
        ("photo-spot", "photo"),
        ("booking", "booking"),
        ("solo-travel", "solo"),
        ("food-trip", "pairing"),
        ("stay", "prep"),
    ],
    "플레이": [
        ("beginner-entry", "start"),
        ("genre-pick", "choose"),
        ("time-budget", "budget"),
        ("with-friends", "social"),
        ("gear-space", "gear"),
        ("series-order", "order"),
        ("avoid-spoiler", "spoiler"),
        ("burnout-hobby", "refresh"),
        ("collect", "value"),
        ("platform", "choose"),
    ],
    "테크": [
        ("which-model", "choose"),
        ("spec-read", "spec"),
        ("setup-first", "setup"),
        ("troubleshoot", "fix"),
        ("battery-life", "life"),
        ("privacy", "privacy"),
        ("old-vs-new", "compare"),
        ("workflow", "workflow"),
        ("buy-tip", "value"),
        ("accessory", "gear"),
    ],
    "인포": [
        ("timeline", "timeline"),
        ("document", "document"),
        ("region-diff", "region"),
        ("fail-reason", "pitfall"),
        ("alternative", "alt"),
        ("first-timer", "checklist"),
        ("cost-hidden", "cost"),
        ("after-result", "after"),
        ("online", "setup"),
        ("deadline", "timeline"),
    ],
}

DEFAULT_MEGA = "라이프"

ANGLE_PATTERNS: dict[str, list[str]] = {
    "compare": ["vs", "summary", "pipe"],
    "choose": ["vs", "pipe", "summary"],
    "context": ["vs", "pipe"],
    "place": ["vs", "pipe"],
    "cost": ["ask", "summary"],
    "budget": ["ask", "summary", "pipe"],
    "pitfall": ["pipe", "summary"],
    "risk": ["pipe", "summary"],
    "default": ["summary", "pipe", "vs", "ask"],
}

VS_ALT: dict[str, list[str]] = {
    "라이프": ["메모 앱", "기존 습관", "종이 노트"],
    "머니": ["예·적금", "다른 상품"],
    "홈": ["업체 의뢰", "DIY"],
    "푸드": ["배달", "직접 만들기"],
    "스타일": ["저가 제품", "다른 브랜드"],
    "바디": ["센터", "홈트"],
    "패밀리": ["전문가 도움", "온라인 강의"],
    "트립": ["패키지", "자유여행"],
    "플레이": ["유료", "무료"],
    "테크": ["구형", "다른 브랜드"],
    "인포": ["민간", "구버전 절차"],
}

SUB_TAIL: dict[str, list[str]] = {
    "start": ["초보자 총정리", "시작 가이드", "체크리스트"],
    "compare": ["차이와 선택 기준", "비교 총정리"],
    "cost": ["비용·가격 정리", "예산 가이드"],
    "default": ["핵심 총정리", "완벽 가이드", "최신 정리", "꿀팁 모음"],
}

MEGA_ALLOW_PAIR_VS = set(PAIR_FOCUS.keys())
VS_METRICS = ["비용", "효과", "칼로리", "가격", "시간", "난이도", "만족도"]
VS_ADJ = ["높을까", "나을까", "좋을까", "유리할까"]


def load_l1_mega() -> dict[str, str]:
    with (DATA / "topics_l1.csv").open(encoding="utf-8") as f:
        return {row["l1_code"]: row["mega_group"] for row in csv.DictReader(f)}


def load_l2() -> list[dict[str, str]]:
    with (DATA / "topics_l2.csv").open(encoding="utf-8") as f:
        return list(csv.DictReader(f))


def split_pair(name: str) -> tuple[str, str] | None:
    if "·" not in name:
        return None
    parts = [p.strip() for p in name.split("·") if p.strip()]
    if len(parts) >= 2:
        return parts[0], parts[1]
    return None


def slugify(text: str, max_len: int = 40) -> str:
    s = re.sub(r"[^\w\s-]", "", text, flags=re.UNICODE)
    s = re.sub(r"[-\s·]+", "-", s).strip("-").lower()
    return (s[:max_len] or "topic").strip("-")


def build_focus_pool(l2_name: str, mega: str) -> list[str]:
    """L2에서 쓸 수 있는 서로 다른 focus 문장 후보."""
    pool: list[str] = [p.format(n=l2_name) for p in FOCUS_PHRASES.get(mega, FOCUS_PHRASES[DEFAULT_MEGA])]

    pair = split_pair(l2_name)
    if pair and mega in MEGA_PAIR:
        a, b = pair
        for tpl in PAIR_FOCUS[mega]:
            pool.append(tpl.format(a=a, b=b, n=l2_name))

    # 전역 중복 완화용 미세 변형 (같은 L2 내에서는 인덱스로만 선택)
    return pool


def pick_unique_focuses(
    pool: list[str], l2_id: str, count: int
) -> list[str]:
    if not pool:
        return []
    h = int(hashlib.md5(l2_id.encode()).hexdigest(), 16)
    stride = 7
    chosen: list[str] = []
    used: set[str] = set()
    i = 0
    while len(chosen) < count and i < len(pool) * 3:
        idx = (h + i * stride) % len(pool)
        focus = pool[idx]
        if focus not in used:
            used.add(focus)
            chosen.append(focus)
        i += 1
    # 부족하면 번호 붙여 채움 (최후 수단)
    j = 0
    while len(chosen) < count:
        extra = f"{pool[j % len(pool)]} ({len(chosen) + 1})"
        if extra not in used:
            used.add(extra)
            chosen.append(extra)
        j += 1
    return chosen[:count]


def pick_slot(mega: str, l2_id: str, seq: int) -> TopicSlot:
    bank = TOPIC_SLOTS.get(mega, TOPIC_SLOTS[DEFAULT_MEGA])
    h = int(hashlib.md5(f"{l2_id}-{seq}".encode()).hexdigest(), 16)
    return bank[h % len(bank)]


def pick_sub(angle: str, l2_id: str, seq: int) -> str:
    pool = SUB_TAIL.get(angle) or SUB_TAIL["default"]
    h = int(hashlib.md5(f"sub-{l2_id}-{seq}".encode()).hexdigest(), 16)
    return pool[h % len(pool)]


def pick_alt(mega: str, l2_id: str, seq: int) -> str:
    pool = VS_ALT.get(mega, VS_ALT["라이프"])
    h = int(hashlib.md5(f"alt-{l2_id}-{seq}".encode()).hexdigest(), 16)
    return pool[h % len(pool)]


def wrap_seo_title(
    focus: str,
    l2_name: str,
    mega: str,
    angle: str,
    l2_id: str,
    seq: int,
) -> tuple[str, str]:
    patterns = ANGLE_PATTERNS.get(angle, ANGLE_PATTERNS["default"])
    h = int(hashlib.md5(f"fmt-{l2_id}-{seq}".encode()).hexdigest(), 16)
    fmt = patterns[h % len(patterns)]
    sub = pick_sub(angle, l2_id, seq)

    # focus 자체가 이미 vs 구조면 summary/pipe 위주
    if " vs " in focus.lower() or " vs " in focus:
        fmt = ["summary", "pipe", "ask"][h % 3]

    if fmt == "vs" and " vs " not in focus:
        alt = pick_alt(mega, l2_id, seq)
        metric = VS_METRICS[h % len(VS_METRICS)]
        adj = VS_ADJ[h % len(VS_ADJ)]
        return f"{focus} vs {alt}, 어떤 것이 더 {adj}", "vs"

    if fmt == "pipe":
        return f"{focus} | {sub}", "pipe"

    if fmt == "ask":
        asks = [
            f"{focus}, 알아두면 충분할까?",
            f"{focus}, 초보자도 가능할까?",
            f"{focus}, 얼마나 걸릴까?",
        ]
        return asks[h % len(asks)], "ask"

    return f"{focus}, {sub}", "summary"


def main() -> None:
    DATA.mkdir(parents=True, exist_ok=True)
    l1_mega = load_l1_mega()
    l2_rows = load_l2()

    per_l2 = max(1, math.ceil(TARGET_QUESTIONS / len(l2_rows)))

    l3_rows: list[dict[str, str]] = []
    question_rows: list[dict[str, str]] = []
    seen_titles: set[str] = set()
    seen_questions: set[str] = set()
    seen_focus_global: set[str] = set()

    for l2 in l2_rows:
        l2_id = l2["l2_id"]
        l1_code = l2["l1_code"]
        l2_name = l2["name_ko"]
        mega = l1_mega.get(l1_code, DEFAULT_MEGA)

        pool = build_focus_pool(l2_name, mega)
        focuses = pick_unique_focuses(pool, l2_id, per_l2)

        for seq, focus in enumerate(focuses, start=1):
            tpl_slug, angle = pick_slot(mega, l2_id, seq)
            title_ko, seo_format = wrap_seo_title(
                focus, l2_name, mega, angle, l2_id, seq
            )
            question_text = title_ko

            # 전역 focus 중복 방지 (다른 L2와 완전 동일 주제 제거)
            gfocus = focus
            if gfocus in seen_focus_global:
                gfocus = f"{focus} — {l2_name}"
                title_ko, seo_format = wrap_seo_title(
                    gfocus, l2_name, mega, angle, l2_id, seq + 99
                )
                question_text = title_ko
            seen_focus_global.add(gfocus)

            if title_ko in seen_titles:
                title_ko = f"{title_ko} — {l2_id}-{seq:02d}"
                question_text = title_ko
            seen_titles.add(title_ko)

            if question_text in seen_questions:
                question_text = f"{question_text} ({seq})"
            seen_questions.add(question_text)

            focus_slug = slugify(focus)[:30]
            l3_id = f"{l2_id}-{focus_slug}-{seq:02d}"

            l3_rows.append(
                {
                    "l3_id": l3_id,
                    "l2_id": l2_id,
                    "l1_code": l1_code,
                    "l3_slug": f"{tpl_slug}-{seq:02d}",
                    "focus_keyword": focus,
                    "title_ko": title_ko,
                    "topic_angle": angle,
                    "seo_format": seo_format,
                }
            )

            question_rows.append(
                {
                    "question_id": l3_id,
                    "l3_id": l3_id,
                    "l2_id": l2_id,
                    "l1_code": l1_code,
                    "question_type": angle,
                    "seo_format": seo_format,
                    "focus_keyword": focus,
                    "question_text": question_text,
                }
            )

    l3_path = DATA / "topics_l3.csv"
    q_path = DATA / "questions.csv"

    with l3_path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(
            f,
            fieldnames=[
                "l3_id",
                "l2_id",
                "l1_code",
                "l3_slug",
                "focus_keyword",
                "title_ko",
                "topic_angle",
                "seo_format",
            ],
        )
        w.writeheader()
        w.writerows(l3_rows)

    with q_path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(
            f,
            fieldnames=[
                "question_id",
                "l3_id",
                "l2_id",
                "l1_code",
                "question_type",
                "seo_format",
                "focus_keyword",
                "question_text",
            ],
        )
        w.writeheader()
        w.writerows(question_rows)

    dup_q = len(question_rows) - len({r["question_text"] for r in question_rows})
    dup_f = len(question_rows) - len({r["focus_keyword"] for r in question_rows})
    from collections import Counter

    per_l2_counts = Counter(r["l2_id"] for r in question_rows)
    min_c, max_c = min(per_l2_counts.values()), max(per_l2_counts.values())

    print(f"Target: ~{TARGET_QUESTIONS}")
    print(f"L2: {len(l2_rows)}, per L2: {per_l2}")
    print(f"Wrote {l3_path} ({len(l3_rows)} rows)")
    print(f"Wrote {q_path} ({len(question_rows)} rows)")
    print(f"Duplicate question_text: {dup_q}")
    print(f"Duplicate focus_keyword: {dup_f}")
    print(f"Questions per L2: min={min_c} max={max_c}")
    fmt_counts: dict[str, int] = {}
    for r in question_rows:
        fmt_counts[r["seo_format"]] = fmt_counts.get(r["seo_format"], 0) + 1
    print(f"seo_format: {fmt_counts}")


if __name__ == "__main__":
    main()
