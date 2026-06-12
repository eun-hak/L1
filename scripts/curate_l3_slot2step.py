#!/usr/bin/env python3
"""
topics_l3_slot2step.csv 정리: stem/title 중복 제거 + L2당 cap (기본 15).

LLM API 없음. 정리 결과를 마스터 CSV·JSON·SQLite에 반영한다.

Usage:
  python scripts/curate_l3_slot2step.py
  python scripts/curate_l3_slot2step.py --dry-run
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))

from dedup_l3_topics import (  # noqa: E402
    EXTRA_FIELDS,
    SOURCE_FIELDS,
    curate_pipeline,
    load_rows,
    write_csv,
)
from taxonomy_state import (  # noqa: E402
    L3_FIELDNAMES,
    connect,
    export_artifacts,
    upsert_topics,
    _sync_l2_progress,
)

MASTER_CSV = ROOT / "data2" / "topics_l3_slot2step.csv"
MASTER_JSON = ROOT / "data2" / "topics_l3_slot2step.json"
DROPPED_CSV = ROOT / "data2" / "archive" / "topics_l3_slot2step_dropped.csv"
REPORT_JSON = ROOT / "data2" / "reports" / "l3_slot2step_curate_report.json"
MANIFEST = ROOT / "data2" / "manifest_l3_slot2step.json"
STATE_DB = ROOT / "data2" / "state" / "taxonomy_slot2step.db"
L2_CSV = ROOT / "data2" / "topics_l2.csv"


def load_l2_count() -> int:
    if not L2_CSV.exists():
        return 0
    with L2_CSV.open(encoding="utf-8-sig") as f:
        return sum(1 for _ in csv.DictReader(f))


def strip_master_rows(survivors: list[dict[str, str]]) -> list[dict[str, str]]:
    return [{k: r.get(k, "") for k in L3_FIELDNAMES} for r in survivors]


def apply_to_db(master: list[dict[str, str]], *, per_l2: int) -> None:
    keep_ids = {r["l3_id"] for r in master}
    with connect(STATE_DB) as conn:
        if keep_ids:
            placeholders = ", ".join("?" for _ in keep_ids)
            conn.execute(
                f"DELETE FROM l3_topics WHERE l3_id NOT IN ({placeholders})",
                list(keep_ids),
            )
        else:
            conn.execute("DELETE FROM l3_topics")
        upsert_topics(conn, master)
        _sync_l2_progress(conn, target_count=per_l2)
        export_artifacts(
            conn,
            csv_path=MASTER_CSV,
            json_path=MASTER_JSON,
            manifest_path=MANIFEST,
            l2_count=load_l2_count(),
            count_target=per_l2,
            model="slot_2step",
            l2_processed=len({r["l2_id"] for r in master}),
            interrupted=False,
            state_db=str(STATE_DB.relative_to(ROOT)),
        )


def main() -> None:
    p = argparse.ArgumentParser(description="slot2step L3 마스터 정리 (no LLM)")
    p.add_argument("--input", type=Path, default=MASTER_CSV)
    p.add_argument("--per-l2", type=int, default=15)
    p.add_argument("--max-intent-combo", type=int, default=15)
    p.add_argument("--dry-run", action="store_true", help="파일·DB 미반영, 리포트만")
    args = p.parse_args()

    raw = load_rows(args.input)
    survivors, dropped, report = curate_pipeline(
        raw, per_l2=args.per_l2, max_intent_combo=args.max_intent_combo
    )
    master = strip_master_rows(survivors)
    per_l2_counts = Counter(r["l2_id"] for r in master)
    over = [(lid, n) for lid, n in per_l2_counts.items() if n > args.per_l2]
    under = sum(1 for n in per_l2_counts.values() if n < args.per_l2)

    summary = {
        **report,
        "input": str(args.input),
        "master_rows": len(master),
        "dropped_rows": len(dropped),
        "l2_count": len(per_l2_counts),
        "l2_over_cap": over,
        "l2_under_cap": under,
        "dry_run": args.dry_run,
    }
    REPORT_JSON.parent.mkdir(parents=True, exist_ok=True)
    REPORT_JSON.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"입력 {len(raw)} → 유지 {len(master)}, 제거 {len(dropped)}")
    print(f"L2 {len(per_l2_counts)} | cap={args.per_l2} | over {len(over)} | under {under}")
    if over:
        print("  over:", over[:5])

    if args.dry_run:
        print(f"(dry-run) report: {REPORT_JSON}")
        return

    DROPPED_CSV.parent.mkdir(parents=True, exist_ok=True)
    write_csv(DROPPED_CSV, dropped, SOURCE_FIELDS + EXTRA_FIELDS)

    with MASTER_CSV.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=L3_FIELDNAMES)
        w.writeheader()
        w.writerows(master)
    MASTER_JSON.write_text(json.dumps(master, ensure_ascii=False, indent=2), encoding="utf-8")

    apply_to_db(master, per_l2=args.per_l2)
    print(f"마스터: {MASTER_CSV}")
    print(f"제거본: {DROPPED_CSV}")
    print(f"report: {REPORT_JSON}")


if __name__ == "__main__":
    main()
