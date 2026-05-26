#!/usr/bin/env python3
"""
questions.csv 기반 블로그 답변 생성.

- template: 로컬 템플릿 (~1500~3000자), API 불필요
- llm: GROQ_API_KEY 필요 (Groq API, scripts/groq_client.py)

Usage:
  python3 scripts/generate_answers.py --limit 20
  python3 scripts/generate_answers.py --all
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
ANSWERS_DIR = DATA / "answers" / "body"
INDEX_PATH = DATA / "answers" / "answers_index.csv"

sys.path.insert(0, str(Path(__file__).resolve().parent))
from answer_templates import build_template_answer  # noqa: E402
from groq_client import generate_blog_body  # noqa: E402

MIN_CHARS = 1500
MAX_CHARS = 3000


def load_l1_names() -> dict[str, tuple[str, str]]:
    path = DATA / "topics_l1.csv"
    with path.open(encoding="utf-8") as f:
        return {
            row["l1_code"]: (row["name_ko"], row["mega_group"])
            for row in csv.DictReader(f)
        }


def load_questions(limit: int | None) -> list[dict[str, str]]:
    with (DATA / "questions.csv").open(encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    return rows if limit is None else rows[:limit]


def seed_for(row: dict[str, str]) -> int:
    return int(hashlib.md5(row["question_id"].encode()).hexdigest(), 16)


def generate_body(row: dict[str, str], l1_info: dict[str, tuple[str, str]], mode: str) -> str:
    l1_code = row["l1_code"]
    l1_name, mega = l1_info.get(l1_code, ("", "default"))
    seed = seed_for(row)

    if mode == "llm":
        return generate_blog_body(
            question_text=row["question_text"],
            focus_keyword=row["focus_keyword"],
            min_chars=MIN_CHARS,
            max_chars=MAX_CHARS,
        )

    return build_template_answer(
        question_text=row["question_text"],
        focus_keyword=row["focus_keyword"],
        l1_name=l1_name,
        mega=mega,
        seed=seed,
        min_chars=MIN_CHARS,
        max_chars=MAX_CHARS,
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None, help="생성 개수 제한")
    parser.add_argument("--all", action="store_true", help="전체 questions 생성")
    parser.add_argument(
        "--mode",
        choices=("template", "llm"),
        default="template",
        help="template=로컬, llm=Groq API",
    )
    parser.add_argument("--dry-run", action="store_true", help="파일 쓰지 않고 통계만")
    args = parser.parse_args()

    limit = None if args.all else (args.limit or 10)
    rows = load_questions(limit)
    l1_info = load_l1_names()

    if not args.dry_run:
        ANSWERS_DIR.mkdir(parents=True, exist_ok=True)

    index_rows: list[dict[str, str]] = []
    char_lengths: list[int] = []
    now = datetime.now(timezone.utc).isoformat()

    for row in rows:
        qid = row["question_id"]
        body = generate_body(row, l1_info, args.mode)
        n = len(body)
        char_lengths.append(n)

        rel_path = f"answers/body/{qid}.md"
        if not args.dry_run:
            out = DATA / rel_path
            out.write_text(body, encoding="utf-8")

        index_rows.append(
            {
                "answer_id": qid,
                "question_id": qid,
                "l3_id": row["l3_id"],
                "l2_id": row["l2_id"],
                "l1_code": row["l1_code"],
                "char_count": str(n),
                "file_path": rel_path,
                "mode": args.mode,
                "status": "draft",
                "generated_at": now,
            }
        )

    if not args.dry_run:
        with INDEX_PATH.open("w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(
                f,
                fieldnames=[
                    "answer_id",
                    "question_id",
                    "l3_id",
                    "l2_id",
                    "l1_code",
                    "char_count",
                    "file_path",
                    "mode",
                    "status",
                    "generated_at",
                ],
            )
            w.writeheader()
            w.writerows(index_rows)

    total_bytes = sum(char_lengths)
    avg = sum(char_lengths) / len(char_lengths) if char_lengths else 0

    print(f"Generated: {len(rows)} answers")
    print(f"Chars: min={min(char_lengths)} avg={avg:.0f} max={max(char_lengths)}")
    print(f"Raw text total: {total_bytes / 1_000_000:.2f} MB")
    print(f"Gzip estimate: {total_bytes * 0.35 / 1_000_000:.2f} MB")
    if args.all or (limit and limit >= 1000):
        full_est = (total_bytes / len(rows)) * 10368 / 1_000_000
        print(f"Full ~10,368 estimate: {full_est:.1f} MB raw, ~{full_est * 0.35:.1f} MB gzip")

    if not args.dry_run:
        print(f"Index: {INDEX_PATH}")
        print(f"Body:  {ANSWERS_DIR}/")


if __name__ == "__main__":
    main()
