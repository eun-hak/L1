#!/usr/bin/env python3
"""simsimi_entities_v1 CSV export → keyword/es/entities/"""

from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.config import ENTITY_INDEX, KEYWORD_OUTPUT_DIR  # noqa: E402
from app.es import get_es  # noqa: E402

OUT_DIR = KEYWORD_OUTPUT_DIR / "entities"

CSV_FIELDS = [
    "qid",
    "ko_title",
    "en_title",
    "popularity",
    "lang_count",
    "new_grade",
    "new_is_sensitive",
    "category_ids",
    "desc_ko",
]


def row_from_src(src: dict) -> dict:
    cats = src.get("category_ids") or []
    return {
        "qid": src.get("qid", ""),
        "ko_title": src.get("ko_title", ""),
        "en_title": src.get("en_title", ""),
        "popularity": src.get("popularity", ""),
        "lang_count": src.get("lang_count", ""),
        "new_grade": src.get("new_grade", ""),
        "new_is_sensitive": src.get("new_is_sensitive", False),
        "category_ids": "|".join(cats) if isinstance(cats, list) else cats,
        "desc_ko": (src.get("desc_ko") or "").replace("\n", " "),
    }


def export_via_composite(
    es,
    writer,
    *,
    base_query: dict | None,
    max_docs: int | None,
    count: int,
) -> int:
    """composite agg로 qid 수집 → mget (ES deep pagination 버그 회피)."""
    after_key = None
    while True:
        composite: dict = {
            "size": 2000,
            "sources": [{"qid": {"terms": {"field": "qid"}}}],
        }
        if after_key:
            composite["after"] = after_key

        body: dict = {"size": 0, "aggs": {"qids": {"composite": composite}}}
        if base_query:
            body["query"] = base_query

        res = es.options(request_timeout=120).search(index=ENTITY_INDEX, body=body)
        agg = res["aggregations"]["qids"]
        buckets = agg["buckets"]
        if not buckets:
            break

        qids = [b["key"]["qid"] for b in buckets]
        for i in range(0, len(qids), 200):
            chunk = qids[i : i + 200]
            docs = es.mget(index=ENTITY_INDEX, ids=chunk, _source=CSV_FIELDS)
            for doc in docs["docs"]:
                if not doc.get("found"):
                    continue
                writer.writerow(row_from_src(doc["_source"]))
                count += 1
                if max_docs and count >= max_docs:
                    return count

        after_key = agg.get("after_key")
        if not after_key:
            break
        if count % 10000 < 2000:
            print(f"  ... {count:,} rows", flush=True)

    return count


def export_entities(
    *,
    output: Path,
    safe_mode: bool = False,
    min_popularity: float = 0.0,
    max_docs: int | None = None,
    linked_only: bool = False,
) -> int:
    es = get_es()
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    filters: list[dict] = []
    if safe_mode:
        filters.append({"term": {"new_is_sensitive": False}})
    if min_popularity > 0:
        filters.append({"range": {"popularity": {"gte": min_popularity}}})
    if linked_only:
        filters.append({"script": {"script": "doc['category_ids'].size() > 0"}})

    base_query: dict | None = {"bool": {"filter": filters}} if filters else None

    with output.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        writer.writeheader()
        return export_via_composite(es, writer, base_query=base_query, max_docs=max_docs, count=0)


def main() -> None:
    parser = argparse.ArgumentParser(description="entities CSV export")
    parser.add_argument("--safe", action="store_true", help="민감 주제 제외")
    parser.add_argument("--linked-only", action="store_true", help="category_ids 있는 것만")
    parser.add_argument("--min-pop", type=float, default=0.0)
    parser.add_argument("--max", type=int, default=None, help="최대 행 수")
    parser.add_argument("-o", "--output", type=Path, default=None)
    args = parser.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    if args.output:
        out = args.output
    elif args.linked_only:
        out = OUT_DIR / "entities_linked.csv"
    elif args.safe:
        out = OUT_DIR / "entities_safe.csv"
    elif args.min_pop > 0:
        out = OUT_DIR / f"entities_minpop_{int(args.min_pop)}.csv"
    else:
        out = OUT_DIR / "entities_all.csv"

    print(f"Export → {out}")
    print(f"index: {ENTITY_INDEX} | safe={args.safe} | linked_only={args.linked_only}")
    t0 = time.time()
    n = export_entities(
        output=out,
        safe_mode=args.safe,
        min_popularity=args.min_pop,
        max_docs=args.max,
        linked_only=args.linked_only,
    )
    elapsed = time.time() - t0
    size_mb = out.stat().st_size / 1_000_000
    meta = {
        "index": ENTITY_INDEX,
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "row_count": n,
        "file_mb": round(size_mb, 2),
        "elapsed_sec": round(elapsed, 1),
        "csv": str(out),
        "options": {k: v for k, v in vars(args).items() if k != "output"},
    }
    meta_path = out.with_suffix(".meta.json")
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Done: {n:,} rows | {size_mb:.1f} MB | {elapsed:.0f}s")
    print(f"Meta: {meta_path}")


if __name__ == "__main__":
    main()
