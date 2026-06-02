#!/usr/bin/env python3
"""L3 품질 비교: Scout 17B(기존) vs Groq Llama 3.1 8B (테스트만, DB/CSV 미반영)."""

from __future__ import annotations

import csv
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))

from groq_client import MODEL_FAST as SCOUT_MODEL, generate_l3_topics  # noqa: E402

MODEL_8B = "llama-3.1-8b-instant"
BRIEF = ROOT / "data2" / "brief.txt"
L1_CSV = ROOT / "data2" / "seed" / "topics_l1.csv"
L2_CSV = ROOT / "data2" / "topics_l2.csv"
L3_CSV = ROOT / "data2" / "topics_l3.csv"
OUT_DIR = ROOT / "data2" / "test"
OUT_JSON = OUT_DIR / "l3_scout_vs_8b.json"

# Scout 데이터가 있는 L2 중 대표 3개
TEST_L2 = ["01-drama", "01-celebrity", "01-movie"]
TEST_COUNT = 15


def load_maps() -> tuple[dict, dict, dict, list]:
    l1 = {r["l1_code"]: r for r in csv.DictReader(L1_CSV.open(encoding="utf-8-sig"))}
    l2 = {r["l2_id"]: r for r in csv.DictReader(L2_CSV.open(encoding="utf-8-sig"))}
    scout_by_l2: dict[str, list[dict]] = defaultdict(list)
    for r in csv.DictReader(L3_CSV.open(encoding="utf-8-sig")):
        scout_by_l2[r["l2_id"]].append(r)
    brief = BRIEF.read_text(encoding="utf-8").strip()
    return l1, l2, scout_by_l2, brief


def analyze(items: list[dict], *, label: str) -> dict:
    keywords = [x.get("focus_keyword", "") for x in items]
    titles = [x.get("title_ko", "") for x in items]
    intents = Counter(x.get("search_intent", "") for x in items)
    angles = Counter(x.get("topic_angle", "") for x in items)

    pipe_titles = sum(1 for t in titles if "|" in t)
    clickbait = sum(
        1 for k in keywords + titles
        if re.search(r"완벽|놓치면|꼭 알아야|총정리 필독|충격|대박", k)
    )
    bad_lang = sum(
        1 for k in keywords
        if re.search(r"[\u4e00-\u9fff\u0100-\u024f]", k)
    )
    short_kw = sum(1 for k in keywords if len(k.split()) < 2)
    long_kw = sum(1 for k in keywords if len(k.split()) > 12)

    # 패턴 반복: "~방법", "~가이드" 비율
    method_guide = sum(1 for k in keywords if re.search(r"방법$|가이드$", k))

    # 연도+채널 패턴 (예능/드라마 템플릿 반복)
    channel_year = sum(
        1 for k in keywords
        if re.search(r"20\d{2}|KBS|SBS|MBC|tvN|JTBC|Netflix", k, re.I)
    )

    unique_stems = len({re.sub(r"\d{4}", "", k)[:20] for k in keywords})

    return {
        "label": label,
        "count": len(items),
        "intent_dist": dict(intents),
        "angle_dist": dict(angles),
        "pipe_titles": pipe_titles,
        "clickbait_hits": clickbait,
        "bad_lang": bad_lang,
        "short_kw": short_kw,
        "long_kw": long_kw,
        "method_guide_suffix": method_guide,
        "channel_or_year_kw": channel_year,
        "unique_prefix20": unique_stems,
        "sample_keywords": keywords[:8],
        "sample_titles": titles[:5],
    }


def main() -> None:
    l1_map, l2_map, scout_by_l2, brief = load_maps()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc).isoformat()

    report: dict = {
        "generated_at": now,
        "scout_model": SCOUT_MODEL,
        "test_model": MODEL_8B,
        "test_l2": TEST_L2,
        "test_count_per_l2": TEST_COUNT,
        "note": "기존 topics_l3.csv / taxonomy.db 에 병합하지 않음",
        "comparisons": [],
    }

    print(f"Scout 기존 데이터 vs {MODEL_8B} × {TEST_COUNT}개/L2\n")

    for l2_id in TEST_L2:
        l2 = l2_map[l2_id]
        l1 = l1_map[l2["l1_code"]]
        scout_items = scout_by_l2.get(l2_id, [])
        scout_sample = scout_items[:TEST_COUNT]

        print(f"[{l2_id}] {l2['name_ko']} — 8B 생성 중...", flush=True)
        avoid = [r["focus_keyword"] for r in scout_items]
        eight_b = generate_l3_topics(
            brief=brief,
            l1_name=l1["name_ko"],
            l1_description=l1.get("description", ""),
            l2_name=l2["name_ko"],
            l2_description=l2.get("description", ""),
            count=TEST_COUNT,
            avoid_keywords=avoid,
            model=MODEL_8B,
        )
        print(f"  Scout(기존 {len(scout_items)}개) 샘플 {len(scout_sample)} | 8B 신규 {len(eight_b)}개")

        report["comparisons"].append({
            "l2_id": l2_id,
            "l2_name": l2["name_ko"],
            "scout_total_in_csv": len(scout_items),
            "scout_analysis": analyze(scout_sample, label="scout"),
            "llama8b_analysis": analyze(eight_b, label="8b"),
            "scout_samples": [
                {"focus_keyword": r["focus_keyword"], "title_ko": r["title_ko"]}
                for r in scout_sample[:10]
            ],
            "llama8b_samples": [
                {"focus_keyword": x["focus_keyword"], "title_ko": x["title_ko"]}
                for x in eight_b[:10]
            ],
        })

    OUT_JSON.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n저장: {OUT_JSON} (병합 없음)")


if __name__ == "__main__":
    main()
