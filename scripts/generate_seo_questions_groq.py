#!/usr/bin/env python3
"""
entities_all.csv에서 N개 키워드를 골라 Groq로 SEO 블로그 질문 생성.

Usage:
  python scripts/generate_seo_questions_groq.py
  python scripts/generate_seo_questions_groq.py --count 100 --input keyword/es/entities/entities_all.csv
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
from groq_client import MODEL_FAST, generate_seo_questions_batch  # noqa: E402

DEFAULT_INPUT = ROOT / "keyword" / "es" / "entities" / "entities_all.csv"
OUT_DIR = ROOT / "keyword" / "es" / "questions"


def load_candidates(
    path: Path,
    *,
    count: int,
    min_pop: float,
    max_pop: float,
) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    with path.open(encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            if row.get("new_is_sensitive", "").lower() == "true":
                continue
            ko = (row.get("ko_title") or "").strip()
            if not ko or len(ko) < 2:
                continue
            try:
                pop = float(row.get("popularity") or 0)
            except ValueError:
                pop = 0.0
            if pop < min_pop or pop > max_pop:
                continue
            # desc 있는 키워드 우선 (제목 품질)
            row["_has_desc"] = "1" if (row.get("desc_ko") or "").strip() else "0"
            rows.append(row)

    rows.sort(key=lambda r: (r["_has_desc"], float(r.get("popularity") or 0)), reverse=True)
    if len(rows) <= count:
        return rows

    step = len(rows) / count
    picked: list[dict[str, str]] = []
    used_qid: set[str] = set()
    i = 0.0
    while len(picked) < count and int(i) < len(rows):
        row = rows[int(i)]
        qid = row.get("qid", "")
        if qid not in used_qid:
            picked.append(row)
            used_qid.add(qid)
        i += step
    return picked[:count]


def main() -> None:
    parser = argparse.ArgumentParser(description="Groq SEO 블로그 질문 생성")
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--count", type=int, default=100)
    parser.add_argument("--batch", type=int, default=1, help="API 1회당 키워드 수")
    parser.add_argument("--min-pop", type=float, default=5.0)
    parser.add_argument("--max-pop", type=float, default=120.0)
    parser.add_argument("--model", default=MODEL_FAST)
    args = parser.parse_args()

    candidates = load_candidates(
        args.input,
        count=args.count,
        min_pop=args.min_pop,
        max_pop=args.max_pop,
    )
    if not candidates:
        raise SystemExit("조건에 맞는 키워드가 없습니다.")

    print(f"Keywords: {len(candidates)} | model: {args.model}")
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    results: list[dict[str, str]] = []
    for start in range(0, len(candidates), args.batch):
        batch = candidates[start : start + args.batch]
        items = [
            {
                "qid": r["qid"],
                "keyword_ko": r.get("ko_title", ""),
                "keyword_en": r.get("en_title", ""),
                "desc_ko": r.get("desc_ko", ""),
                "popularity": r.get("popularity", ""),
            }
            for r in batch
        ]
        print(f"  [{start + 1}-{start + len(items)}/{len(candidates)}] ...", flush=True)
        generated = generate_seo_questions_batch(items, model=args.model)
        results.extend(generated)

        # 중간 저장
        partial = OUT_DIR / "seo_questions_100.partial.csv"
        fields = [
            "question_id", "qid", "keyword_ko", "keyword_en", "popularity",
            "seo_question", "search_intent", "seo_format", "model",
        ]
        with partial.open("w", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, fieldnames=fields)
            w.writeheader()
            w.writerows(results)

    csv_path = OUT_DIR / "seo_questions_100.csv"
    json_path = OUT_DIR / "seo_questions_100.json"

    fields = [
        "question_id",
        "qid",
        "keyword_ko",
        "keyword_en",
        "popularity",
        "seo_question",
        "search_intent",
        "seo_format",
        "model",
    ]
    with csv_path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(results)

    json_path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\nDone: {len(results)} questions")
    print(f"CSV:  {csv_path}")
    print(f"JSON: {json_path}")


if __name__ == "__main__":
    main()
