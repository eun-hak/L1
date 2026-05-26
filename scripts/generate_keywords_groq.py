#!/usr/bin/env python3
"""
L1/L2/L3 계층을 바탕으로 Groq(llama-3.1-8b-instant)로 블로그 키워드 수집.

결과 저장: keyword/keywords.csv, keyword/raw/{l2_id}.json

Usage:
  # L2 1개 테스트 (키워드 8개)
  python scripts/generate_keywords_groq.py --l2 01-diary --per-l2 8

  # L1 하나 아래 L2 3개만
  python scripts/generate_keywords_groq.py --l1 01 --limit-l2 3 --per-l2 10

  # 전체 L2 순회 (시간·API 소모 큼)
  python scripts/generate_keywords_groq.py --all --per-l2 10
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
KEYWORD_DIR = ROOT / "keyword"
RAW_DIR = KEYWORD_DIR / "raw"
INDEX_PATH = KEYWORD_DIR / "keywords.csv"

sys.path.insert(0, str(Path(__file__).resolve().parent))
from groq_client import MODEL_FAST, generate_l2_keywords  # noqa: E402

INDEX_FIELDS = [
    "keyword_id",
    "l1_code",
    "l1_name",
    "l2_id",
    "l2_name",
    "mega_group",
    "focus_keyword",
    "seo_title",
    "search_intent",
    "model",
    "generated_at",
]


def load_l1() -> dict[str, dict[str, str]]:
    with (DATA / "topics_l1.csv").open(encoding="utf-8") as f:
        return {row["l1_code"]: row for row in csv.DictReader(f)}


def load_l2() -> list[dict[str, str]]:
    with (DATA / "topics_l2.csv").open(encoding="utf-8") as f:
        return list(csv.DictReader(f))


def load_l3_by_l2() -> dict[str, list[str]]:
    grouped: dict[str, list[str]] = defaultdict(list)
    with (DATA / "topics_l3.csv").open(encoding="utf-8") as f:
        for row in csv.DictReader(f):
            grouped[row["l2_id"]].append(row["focus_keyword"])
    return grouped


def load_existing_keywords() -> tuple[list[dict[str, str]], set[str]]:
    if not INDEX_PATH.exists():
        return [], set()

    with INDEX_PATH.open(encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    seen = {row["focus_keyword"] for row in rows}
    return rows, seen


def save_index(rows: list[dict[str, str]]) -> None:
    KEYWORD_DIR.mkdir(parents=True, exist_ok=True)
    with INDEX_PATH.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=INDEX_FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def save_raw(l2_id: str, payload: dict) -> None:
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    path = RAW_DIR / f"{l2_id}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def next_seq_for_l2(rows: list[dict[str, str]], l2_id: str) -> int:
    prefix = f"{l2_id}-kw-"
    nums = []
    for row in rows:
        kid = row.get("keyword_id", "")
        if kid.startswith(prefix):
            try:
                nums.append(int(kid.rsplit("-", 1)[-1]))
            except ValueError:
                pass
    return max(nums, default=0) + 1


def filter_l2_rows(
    l2_rows: list[dict[str, str]],
    *,
    l1: str | None,
    l2: str | None,
    limit_l2: int | None,
    skip_existing: bool,
    processed_l2: set[str],
) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for row in l2_rows:
        if l1 and row["l1_code"] != l1:
            continue
        if l2 and row["l2_id"] != l2:
            continue
        if skip_existing and row["l2_id"] in processed_l2:
            continue
        out.append(row)
        if limit_l2 and len(out) >= limit_l2:
            break
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description="Groq로 L1/L2 기반 블로그 키워드 생성")
    parser.add_argument("--l1", help="L1 코드 필터 (예: 01)")
    parser.add_argument("--l2", help="L2 ID 필터 (예: 01-diary)")
    parser.add_argument("--limit-l2", type=int, help="처리할 L2 개수 제한")
    parser.add_argument("--per-l2", type=int, default=10, help="L2당 생성 키워드 수")
    parser.add_argument("--all", action="store_true", help="전체 L2 처리")
    parser.add_argument(
        "--skip-existing",
        action="store_true",
        help="keyword/raw에 이미 있는 L2는 건너뜀",
    )
    parser.add_argument("--model", default=MODEL_FAST, help="Groq 모델명")
    args = parser.parse_args()

    if not args.all and not args.l1 and not args.l2 and not args.limit_l2:
        parser.error("--l1, --l2, --limit-l2, --all 중 하나는 필요합니다.")

    l1_map = load_l1()
    l2_rows = load_l2()
    l3_by_l2 = load_l3_by_l2()
    index_rows, seen_global = load_existing_keywords()

    processed_l2 = {p.stem for p in RAW_DIR.glob("*.json")} if RAW_DIR.exists() else set()
    targets = filter_l2_rows(
        l2_rows,
        l1=args.l1,
        l2=args.l2,
        limit_l2=None if args.all else args.limit_l2,
        skip_existing=args.skip_existing,
        processed_l2=processed_l2,
    )

    if not targets:
        print("처리할 L2가 없습니다.")
        return

    now = datetime.now(timezone.utc).isoformat()
    created = 0

    print(f"Model: {args.model}")
    print(f"L2 targets: {len(targets)} | per L2: {args.per_l2}")
    print(f"Output: {INDEX_PATH}")

    for row in targets:
        l2_id = row["l2_id"]
        l1_code = row["l1_code"]
        l1 = l1_map.get(l1_code, {})
        l1_name = l1.get("name_ko", "")
        mega = l1.get("mega_group", "")
        l2_name = row["name_ko"]

        existing = list(dict.fromkeys(l3_by_l2.get(l2_id, []) + list(seen_global)))

        print(f"\n[{l2_id}] {l1_name} > {l2_name} ...", flush=True)
        try:
            items = generate_l2_keywords(
                l1_name=l1_name,
                l2_name=l2_name,
                mega_group=mega,
                existing_keywords=existing,
                count=args.per_l2,
                model=args.model,
            )
        except Exception as exc:
            print(f"  ERROR: {exc}")
            continue

        seq = next_seq_for_l2(index_rows, l2_id)
        batch: list[dict[str, str]] = []

        for item in items:
            focus = item["focus_keyword"]
            if focus in seen_global:
                continue

            keyword_id = f"{l2_id}-kw-{seq:03d}"
            seq += 1
            seen_global.add(focus)

            record = {
                "keyword_id": keyword_id,
                "l1_code": l1_code,
                "l1_name": l1_name,
                "l2_id": l2_id,
                "l2_name": l2_name,
                "mega_group": mega,
                "focus_keyword": focus,
                "seo_title": item["seo_title"],
                "search_intent": item["search_intent"],
                "model": args.model,
                "generated_at": now,
            }
            index_rows.append(record)
            batch.append(record)
            created += 1

        save_raw(
            l2_id,
            {
                "l1_code": l1_code,
                "l1_name": l1_name,
                "l2_id": l2_id,
                "l2_name": l2_name,
                "mega_group": mega,
                "model": args.model,
                "generated_at": now,
                "existing_sample": existing[:12],
                "keywords": batch,
            },
        )
        save_index(index_rows)
        print(f"  +{len(batch)} keywords (total index: {len(index_rows)})")

    print(f"\nDone. New keywords: {created} | Index rows: {len(index_rows)}")


if __name__ == "__main__":
    main()
