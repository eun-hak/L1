#!/usr/bin/env python3
"""
data2/seed/topics_l1.csv 기반 L2 중분류 생성.

Usage:
  python scripts/generate_taxonomy_l2.py
  python scripts/generate_taxonomy_l2.py --provider groq --count 15
  python scripts/generate_taxonomy_l2.py --provider nvidia --l1 01
  python scripts/generate_taxonomy_l2.py --dry-run
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))

from nvidia_client import MODEL_FAST as NVIDIA_MODEL, generate_l2_categories as nvidia_l2  # noqa: E402
from groq_client import MODEL_FAST as GROQ_MODEL, generate_l2_categories as groq_l2  # noqa: E402

PROVIDERS = {
    "groq": (GROQ_MODEL, groq_l2),
    "nvidia": (NVIDIA_MODEL, nvidia_l2),
}

L1_CSV = ROOT / "data2" / "seed" / "topics_l1.csv"
BRIEF = ROOT / "data2" / "brief.txt"
OUT_CSV = ROOT / "data2" / "topics_l2.csv"
OUT_JSON = ROOT / "data2" / "topics_l2.json"
MANIFEST = ROOT / "data2" / "manifest_l2.json"

SLEEP_SEC = 2.0


def load_l1(path: Path, l1_filter: str | None) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    with path.open(encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            if l1_filter and row.get("l1_code") != l1_filter:
                continue
            rows.append(row)
    rows.sort(key=lambda r: int(r.get("sort_order") or 0))
    return rows


def load_brief(path: Path) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8").strip()


def load_existing_l2(path: Path, *, exclude_l1: str | None = None) -> list[dict[str, str]]:
    if not path.exists():
        return []
    rows: list[dict[str, str]] = []
    with path.open(encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            if exclude_l1 and row.get("l1_code") == exclude_l1:
                continue
            rows.append(row)
    return rows


def write_l2(rows: list[dict[str, str]], *, l1_count: int, count_target: int, l1_file: Path, model: str) -> None:
    now = datetime.now(timezone.utc).isoformat()
    fieldnames = [
        "l2_id", "l1_code", "l2_slug", "name_ko", "description",
        "sort_order", "model", "generated_at",
    ]
    OUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    with OUT_CSV.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)
    OUT_JSON.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    manifest = {
        "generated_at": now,
        "model": model,
        "l1_count": l1_count,
        "l2_per_l1_target": count_target,
        "l2_total": len(rows),
        "brief_file": str(BRIEF),
        "l1_file": str(l1_file),
    }
    MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="data2 L2 중분류 생성")
    parser.add_argument("--provider", choices=list(PROVIDERS), default="groq")
    parser.add_argument("--input", type=Path, default=L1_CSV)
    parser.add_argument("--count", type=int, default=15, help="L1당 L2 개수")
    parser.add_argument("--l1", type=str, default=None, help="특정 L1 code만 (예: 01)")
    parser.add_argument("--sleep", type=float, default=SLEEP_SEC)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    model_name, generate_l2 = PROVIDERS[args.provider]

    l1_rows = load_l1(args.input, args.l1)
    if not l1_rows:
        raise SystemExit("L1 데이터가 없습니다.")

    brief = load_brief(BRIEF)
    if args.l1 and OUT_CSV.exists():
        all_l2 = load_existing_l2(OUT_CSV, exclude_l1=args.l1)
        global_avoid = [r["name_ko"] for r in all_l2]
    else:
        all_l2 = []
        global_avoid = []
    now = datetime.now(timezone.utc).isoformat()

    print(f"L1 {len(l1_rows)}개 × L2 {args.count}개 목표 (provider={args.provider}, model={model_name})")

    for l1 in l1_rows:
        l1_code = l1["l1_code"]
        l1_name = l1["name_ko"]
        print(f"\n[{l1_code}] {l1_name} ...", flush=True)

        if args.dry_run:
            continue

        batch: list[dict[str, str]] = []
        remaining = args.count
        batch_size = min(15, args.count)
        attempts = 0
        while len(batch) < args.count and attempts < 3:
            need = min(batch_size, args.count - len(batch))
            chunk = generate_l2(
                brief=brief,
                l1_name=l1_name,
                l1_theme=l1.get("theme", ""),
                l1_description=l1.get("description", ""),
                count=need,
                avoid_names=global_avoid + [b["name_ko"] for b in batch],
            )
            for item in chunk:
                if item["name_ko"] not in global_avoid and item["name_ko"] not in {b["name_ko"] for b in batch}:
                    batch.append(item)
            attempts += 1
            if len(batch) < args.count and chunk:
                time.sleep(args.sleep)

        if len(batch) < args.count:
            print(f"  ⚠ {len(batch)}/{args.count}개만 생성됨")

        sort = 0
        seen_slugs: set[str] = set()
        for item in batch:
            slug = item["slug"]
            if slug in seen_slugs:
                continue
            seen_slugs.add(slug)
            sort += 1
            l2_id = f"{l1_code}-{slug}"
            row = {
                "l2_id": l2_id,
                "l1_code": l1_code,
                "l2_slug": slug,
                "name_ko": item["name_ko"],
                "description": item["description"],
                "sort_order": str(sort),
                "model": model_name,
                "generated_at": now,
            }
            all_l2.append(row)
            global_avoid.append(item["name_ko"])
            print(f"  · {item['name_ko']} ({item['slug']})")

        time.sleep(args.sleep)

    if args.dry_run:
        print("dry-run 완료")
        return

    write_l2(all_l2, l1_count=len(l1_rows), count_target=args.count, l1_file=args.input, model=model_name)
    print(f"\n완료: {len(all_l2)}개 L2 → {OUT_CSV}")


if __name__ == "__main__":
    main()
