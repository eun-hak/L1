#!/usr/bin/env python3
"""L3 품질 비교: Scout 17B(기존) vs NVIDIA Mistral Nemotron (테스트만, DB/CSV 미반영)."""

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

from nvidia_client import MODEL_NEMOTRON, generate_l3_topics  # noqa: E402

SCOUT_MODEL = "meta-llama/llama-4-scout-17b-16e-instruct"
BRIEF = ROOT / "data2" / "brief.txt"
L1_CSV = ROOT / "data2" / "seed" / "topics_l1.csv"
L2_CSV = ROOT / "data2" / "topics_l2.csv"
L3_CSV = ROOT / "data2" / "topics_l3.csv"
OUT_DIR = ROOT / "data2" / "test"
OUT_JSON = OUT_DIR / "l3_scout_vs_nemotron.json"

TEST_L2 = ["01-drama", "01-celebrity", "01-movie"]
TEST_COUNT = 15


def load_maps() -> tuple[dict, dict, dict, str]:
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
    bad_lang = sum(1 for k in keywords if re.search(r"[\u4e00-\u9fff\u0100-\u024f]", k))
    channel_year = sum(
        1 for k in keywords
        if re.search(r"20\d{2}|KBS|SBS|MBC|tvN|JTBC|Netflix|넷플릭스", k, re.I)
    )
    return {
        "label": label,
        "count": len(items),
        "intent_dist": dict(intents),
        "angle_dist": dict(angles),
        "pipe_titles": pipe_titles,
        "clickbait_hits": clickbait,
        "bad_lang": bad_lang,
        "channel_or_year_kw": channel_year,
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
        "test_model": MODEL_NEMOTRON,
        "nvidia_endpoint": "https://integrate.api.nvidia.com/v1",
        "test_l2": TEST_L2,
        "test_count_per_l2": TEST_COUNT,
        "note": "기존 topics_l3.csv / taxonomy.db 에 병합하지 않음",
        "comparisons": [],
    }

    print(f"Scout(기존) vs NVIDIA {MODEL_NEMOTRON} × {TEST_COUNT}개/L2\n")

    for l2_id in TEST_L2:
        l2 = l2_map[l2_id]
        l1 = l1_map[l2["l1_code"]]
        scout_items = scout_by_l2.get(l2_id, [])
        scout_sample = scout_items[:TEST_COUNT]

        print(f"[{l2_id}] {l2['name_ko']} — Nemotron 생성 중...", flush=True)
        avoid = [r["focus_keyword"] for r in scout_items]
        nemotron = generate_l3_topics(
            brief=brief,
            l1_name=l1["name_ko"],
            l1_description=l1.get("description", ""),
            l2_name=l2["name_ko"],
            l2_description=l2.get("description", ""),
            count=TEST_COUNT,
            avoid_keywords=avoid,
            model=MODEL_NEMOTRON,
        )
        print(f"  Scout 샘플 {len(scout_sample)} | Nemotron {len(nemotron)}개")

        scout_a = analyze(scout_sample, label="scout")
        nem_a = analyze(nemotron, label="nemotron")
        if l2_id == "01-movie":
            nem_a["drama_keyword_in_movie_l2"] = sum(1 for x in nemotron if "드라마" in x["focus_keyword"])

        report["comparisons"].append({
            "l2_id": l2_id,
            "l2_name": l2["name_ko"],
            "scout_total_in_csv": len(scout_items),
            "scout_analysis": scout_a,
            "nemotron_analysis": nem_a,
            "scout_samples": [
                {"focus_keyword": r["focus_keyword"], "title_ko": r["title_ko"]}
                for r in scout_sample[:10]
            ],
            "nemotron_samples": [
                {"focus_keyword": x["focus_keyword"], "title_ko": x["title_ko"]}
                for x in nemotron[:10]
            ],
        })

    OUT_JSON.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n저장: {OUT_JSON}")


if __name__ == "__main__":
    main()
