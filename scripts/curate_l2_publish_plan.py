#!/usr/bin/env python3
"""
L2 단위 publish L3 발행·병합 계획 수립.

Usage:
  python scripts/curate_l2_publish_plan.py --l2 11-travel-essentials
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
CURATED = ROOT / "data2" / "topics_l3_curated.csv"
BODY_DIR = ROOT / "data2" / "answers" / "body"
OUT_DIR = ROOT / "data2" / "pilot" / "l2_curation"

# L2별 의미 버킷 규칙 (focus_keyword 기준)
BUCKET_RULES: list[tuple[str, str]] = [
    ("keep_master", r"국내 여행 필수 준비물"),
    ("merge_duration", r"장기 여행|단기 여행"),
    ("merge_season", r"여름 여행 준비물|겨울 여행 준비물"),
    ("merge_overseas_prep", r"해외 여행 필수품"),
    ("keep_insurance", r"보험"),
    ("keep_medicine", r"의약품|상비약"),
    ("merge_electronics", r"충전기|전선|전원|멀티탭|어댑터|전원공급"),
    ("merge_toiletries", r"세면도구|화장품|토일렛"),
    ("merge_food", r"간편 식품|스낵|음식 포장|도시락"),
    ("merge_packing", r"캐리어|짐 싸|팩킹|수하물"),
    ("keep_travel_admin", r"여권|비자|환전|유심|로밍|eSIM"),
    ("keep_family", r"아이 여행|유아|아기|반려견 여행"),
]

# 버킷당 발행 1개만 — 대표 l3_slug (없으면 점수 최고)
BUCKET_WINNERS = {
    "keep_master": "domestic-travel-essentials",
    "keep_insurance": "overseas-travel-insurance",
    "keep_medicine": "travel-medicine-essentials",
    "merge_electronics": "travel-cable-recommend",
    "merge_food": "travel-food-packing-tips",
}


def bucket_for(fk: str) -> str:
    for name, pat in BUCKET_RULES:
        if re.search(pat, fk):
            return name
    return "hold_other"


def action_for(bucket: str, slug: str, score: int) -> str:
    winner = BUCKET_WINNERS.get(bucket)
    if bucket.startswith("keep_"):
        if winner and slug == winner:
            return "publish"
        if winner:
            return "merge"
        return "publish" if score >= 90 else "hold"
    if bucket.startswith("merge_"):
        if winner and slug == winner:
            return "publish_niche"  # 니치 대표 1개
        return "merge"
    return "hold"


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--l2", required=True)
    args = p.parse_args()

    rows = [
        r for r in csv.DictReader(CURATED.open(encoding="utf-8-sig"))
        if r["l2_id"] == args.l2 and r["tier"] == "publish"
    ]
    if not rows:
        raise SystemExit(f"no publish rows for {args.l2}")

    generated = set()
    if BODY_DIR.exists():
        generated = {p.stem for p in BODY_DIR.glob(f"{args.l2}-*.md")}

    out_rows = []
    for r in rows:
        b = bucket_for(r["focus_keyword"])
        score = int(r.get("quality_score") or 0)
        action = action_for(b, r["l3_slug"], score)
        out_rows.append({
            **{k: r[k] for k in r if k not in ("drop_reason",)},
            "semantic_bucket": b,
            "publish_action": action,
            "body_exists": "yes" if r["l3_id"] in generated else "no",
            "merge_target_slug": BUCKET_WINNERS.get(b, ""),
        })

    # publish 최종 5개: publish + publish_niche
    publish_final = [
        x for x in out_rows
        if x["publish_action"] in ("publish", "publish_niche")
    ]
    # tie-break score
    publish_final.sort(key=lambda x: -int(x.get("quality_score") or 0))

    counts = Counter(x["publish_action"] for x in out_rows)
    bucket_counts = Counter(x["semantic_bucket"] for x in out_rows)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out_csv = OUT_DIR / f"{args.l2}_publish_plan.csv"
    fields = list(out_rows[0].keys())
    with out_csv.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(sorted(out_rows, key=lambda x: (x["publish_action"], -int(x["quality_score"]))))

    report = {
        "l2_id": args.l2,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total_publish_l3": len(rows),
        "action_counts": dict(counts),
        "bucket_counts": dict(bucket_counts),
        "recommended_publish": [
            {
                "l3_id": x["l3_id"],
                "focus_keyword": x["focus_keyword"],
                "semantic_bucket": x["semantic_bucket"],
                "body_exists": x["body_exists"],
                "note": _note(x),
            }
            for x in publish_final
        ],
        "merge_into_master": [
            x["focus_keyword"] for x in out_rows if x["publish_action"] == "merge"
        ][:25],
        "already_generated_10": {
            "publish_ok": [x["l3_id"] for x in publish_final if x["l3_id"] in generated],
            "merge_or_drop": [g for g in sorted(generated) if g not in {x["l3_id"] for x in publish_final}],
        },
        "csv": str(out_csv),
    }
    out_json = OUT_DIR / f"{args.l2}_publish_plan.json"
    out_json.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


def _note(x: dict) -> str:
    if x["semantic_bucket"] == "keep_master":
        return "evergreen 허브 — 제목에서 '국내' 제거 권장"
    if x["publish_action"] == "publish_niche":
        return "니치 대표 1건 (같은 버킷 나머지는 병합)"
    return ""


if __name__ == "__main__":
    main()
