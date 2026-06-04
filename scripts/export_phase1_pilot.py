#!/usr/bin/env python3
"""
Phase1 SEO 런치 대상 L3 publish 행 추출.

입력:
  data2/seed/blog_launch_priority.csv  (phase=1, l2_id)
  data2/topics_l3_curated.csv        (tier=publish)

출력:
  data2/pilot/phase1_body_pilot.csv
  data2/pilot/phase1_body_pilot.json
  data2/pilot/manifest_phase1_pilot.json

Usage:
  python scripts/export_phase1_pilot.py
"""

from __future__ import annotations

import csv
import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LAUNCH = ROOT / "data2" / "seed" / "blog_launch_priority.csv"
CURATED = ROOT / "data2" / "topics_l3_curated.csv"
OUT_DIR = ROOT / "data2" / "pilot"
OUT_CSV = OUT_DIR / "phase1_body_pilot.csv"
OUT_JSON = OUT_DIR / "phase1_body_pilot.json"
OUT_MANIFEST = OUT_DIR / "manifest_phase1_pilot.json"


def main() -> None:
    launch_rows = []
    with LAUNCH.open(encoding="utf-8-sig", newline="") as f:
        for r in csv.DictReader(f):
            if r.get("phase") != "1" or not r.get("l2_id"):
                continue
            launch_rows.append(r)

    targets = {r["l2_id"]: int(r["target_posts_m1"]) for r in launch_rows}
    l2_meta = {r["l2_id"]: r for r in launch_rows}

    by_l2: dict[str, list[dict]] = defaultdict(list)
    with CURATED.open(encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            if row.get("tier") != "publish":
                continue
            l2 = row["l2_id"]
            if l2 not in targets:
                continue
            by_l2[l2].append(row)

    selected: list[dict] = []
    shortfall: list[dict] = []
    for l2_id, limit in sorted(targets.items()):
        pool = by_l2.get(l2_id, [])
        pool.sort(key=lambda r: int(r.get("quality_score") or 0), reverse=True)
        picked = pool[:limit]
        selected.extend(picked)
        if len(picked) < limit:
            shortfall.append({
                "l2_id": l2_id,
                "target": limit,
                "available": len(picked),
                "gap": limit - len(picked),
            })

    extra_fields = [
        "pilot_phase", "l1_name_ko", "l2_name_ko", "seo_priority", "pick_rank",
    ]
    out_rows = []
    for l2_id, limit in targets.items():
        pool = sorted(
            [r for r in selected if r["l2_id"] == l2_id],
            key=lambda r: int(r.get("quality_score") or 0),
            reverse=True,
        )
        meta = l2_meta[l2_id]
        for rank, r in enumerate(pool, start=1):
            out = dict(r)
            out["pilot_phase"] = "1"
            out["l1_name_ko"] = meta["name_ko"]
            out["l2_name_ko"] = meta["l2_name_ko"]
            out["seo_priority"] = meta["priority"]
            out["pick_rank"] = str(rank)
            out_rows.append(out)

    fieldnames = list(out_rows[0].keys()) if out_rows else []
    for ef in extra_fields:
        if ef not in fieldnames:
            fieldnames.append(ef)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    with OUT_CSV.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        w.writerows(out_rows)

    OUT_JSON.write_text(
        json.dumps(out_rows, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    by_l1 = defaultdict(int)
    for r in out_rows:
        by_l1[r["l1_code"]] += 1

    manifest = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "phase": 1,
        "total_rows": len(out_rows),
        "target_l2_count": len(targets),
        "shortfall_l2": shortfall,
        "by_l1_code": dict(by_l1),
        "paths": {
            "csv": str(OUT_CSV),
            "json": str(OUT_JSON),
            "launch_plan": str(LAUNCH),
        },
        "body_pipeline_note": {
            "draft_model": "gemini-2.5-flash",
            "seo_meta_model": "gemini-3.1-flash-lite",
            "intro_polish_model": "gemini-2.5-pro (optional)",
        },
    }
    OUT_MANIFEST.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
