#!/usr/bin/env python3
"""
seo_questions_100.json 기반 4단계 Groq 답변 생성.

파이프라인:
  1. gemini-2.5-flash      — 본문 초안 (1,500~3,000자)
  2. gemini-2.5-pro        — popularity 상위 N% 도입부만 다듬기
  3. gemini-3.1-flash-lite — 메타 설명·태그 (옵션)

Usage:
  python scripts/generate_seo_answers_groq.py --limit 5
  python scripts/generate_seo_answers_groq.py --all
  python scripts/generate_seo_answers_groq.py --all --no-meta --resume
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
from gemini_client import (  # noqa: E402
    MODEL_DRAFT,
    MODEL_FAST,
    MODEL_QUALITY,
    generate_blog_body,
    generate_meta_tags,
    polish_intro,
)

DEFAULT_QUESTIONS = ROOT / "keyword" / "es" / "questions" / "seo_questions_100.json"
OUT_DIR = ROOT / "keyword" / "es" / "answers"
BODY_DIR = OUT_DIR / "body"
INDEX_CSV = OUT_DIR / "answers_index.csv"
INDEX_JSON = OUT_DIR / "answers_index.json"

MIN_CHARS = 1500
MAX_CHARS = 3000
DRAFT_SLEEP = 2.0
POLISH_SLEEP = 3.0
META_SLEEP = 1.2


def load_questions(path: Path, limit: int | None) -> list[dict]:
    rows = json.loads(path.read_text(encoding="utf-8"))
    rows.sort(key=lambda r: float(r.get("popularity") or 0), reverse=True)
    return rows if limit is None else rows[:limit]


def polish_cutoff(rows: list[dict], top_pct: float) -> float:
    if not rows:
        return 0.0
    n = max(1, int(len(rows) * top_pct / 100))
    return float(rows[n - 1].get("popularity") or 0)


def main() -> None:
    parser = argparse.ArgumentParser(description="SEO 질문 → Groq 4단계 답변")
    parser.add_argument("--input", type=Path, default=DEFAULT_QUESTIONS)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--polish-pct", type=float, default=30.0, help="70B 도입부 다듬기 상위 비율(%%)")
    parser.add_argument("--no-meta", action="store_true", help="8B 메타 태그 생략")
    parser.add_argument("--resume", action="store_true", help="이미 생성된 파일 건너뛰기")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    limit = None if args.all else (args.limit or 5)
    rows = load_questions(args.input, limit)
    if not rows:
        raise SystemExit("질문이 없습니다.")

    cutoff = polish_cutoff(rows, args.polish_pct)
    polish_count = sum(1 for r in rows if float(r.get("popularity") or 0) >= cutoff)

    print(f"Questions: {len(rows)} | polish top {args.polish_pct}% (~{polish_count}) | cutoff pop={cutoff}")
    print(f"Draft: {MODEL_DRAFT} | Polish: {MODEL_QUALITY} | Meta: {'off' if args.no_meta else MODEL_FAST}")

    if not args.dry_run:
        BODY_DIR.mkdir(parents=True, exist_ok=True)

    index_rows: list[dict[str, str]] = []
    char_lengths: list[int] = []
    now = datetime.now(timezone.utc).isoformat()

    for i, row in enumerate(rows, 1):
        qid = row["question_id"]
        seo_title = row["seo_question"]
        keyword = row["keyword_ko"]
        pop = float(row.get("popularity") or 0)
        out_path = BODY_DIR / f"{qid}.md"

        if args.resume and out_path.exists() and out_path.stat().st_size > 500:
            body = out_path.read_text(encoding="utf-8")
            n = len(body)
            char_lengths.append(n)
            print(f"  [{i}/{len(rows)}] skip {qid} ({n} chars)", flush=True)
            index_rows.append(_index_row(row, body, n, now, polished="skip", meta_skipped=args.no_meta))
            continue

        print(f"  [{i}/{len(rows)}] draft {qid} | {keyword[:20]}...", flush=True)

        if args.dry_run:
            continue

        body = generate_blog_body(
            question_text=seo_title,
            focus_keyword=keyword,
            min_chars=MIN_CHARS,
            max_chars=MAX_CHARS,
        )
        time.sleep(DRAFT_SLEEP)

        polished = "no"
        if pop >= cutoff:
            print(f"    polish intro (pop={pop})", flush=True)
            body = polish_intro(seo_title, keyword, body)
            polished = "yes"
            time.sleep(POLISH_SLEEP)

        meta_desc = ""
        tags = ""
        if not args.no_meta:
            meta = generate_meta_tags(seo_title, keyword)
            meta_desc = meta.get("meta_description", "")
            tags = meta.get("tags", "")
            time.sleep(META_SLEEP)

        final = _render_markdown(seo_title, keyword, body, meta_desc, tags, row)
        final = _ensure_frontmatter_spacing(final)
        out_path.write_text(final, encoding="utf-8")
        n = len(final)
        char_lengths.append(n)
        index_rows.append(_index_row(row, final, n, now, polished=polished, meta_skipped=args.no_meta))

        # 중간 인덱스 저장
        _write_index(index_rows)

    if args.dry_run:
        print(f"Dry-run: would generate {len(rows)} answers")
        return

    _write_index(index_rows)

    if char_lengths:
        avg = sum(char_lengths) / len(char_lengths)
        print(f"\nDone: {len(char_lengths)} answers")
        print(f"Chars: min={min(char_lengths)} avg={avg:.0f} max={max(char_lengths)}")
    print(f"Index: {INDEX_CSV}")
    print(f"Body:  {BODY_DIR}/")


def _render_markdown(
    seo_title: str,
    keyword: str,
    body: str,
    meta_desc: str,
    tags: str,
    row: dict,
) -> str:
    header = f"""---
question_id: {row.get('question_id', '')}
qid: {row.get('qid', '')}
seo_title: {seo_title}
keyword_ko: {keyword}
search_intent: {row.get('search_intent', '')}
popularity: {row.get('popularity', '')}
meta_description: {meta_desc}
tags: {tags}
---

"""
    body_stripped = body.strip()
    if not body_stripped.startswith("#"):
        body_stripped = f"# {seo_title}\n\n{body_stripped}"
    return header + body_stripped + "\n"


def _ensure_frontmatter_spacing(text: str) -> str:
    if text.startswith("---"):
        return re.sub(r"---\n(?=#)", "---\n\n", text, count=1)
    return text


def _index_row(
    row: dict,
    body: str,
    n: int,
    now: str,
    *,
    polished: str,
    meta_skipped: bool,
) -> dict[str, str]:
    qid = row["question_id"]
    return {
        "answer_id": qid,
        "question_id": qid,
        "qid": row.get("qid", ""),
        "keyword_ko": row.get("keyword_ko", ""),
        "seo_question": row.get("seo_question", ""),
        "search_intent": row.get("search_intent", ""),
        "popularity": row.get("popularity", ""),
        "char_count": str(n),
        "file_path": f"body/{qid}.md",
        "draft_model": MODEL_DRAFT,
        "polished_intro": polished,
        "meta_generated": "no" if meta_skipped else "yes",
        "status": "draft",
        "generated_at": now,
    }


def _load_existing_index() -> list[dict[str, str]]:
    if INDEX_JSON.exists():
        try:
            return json.loads(INDEX_JSON.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    if not INDEX_CSV.exists():
        return []
    with INDEX_CSV.open(encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def _merge_index_rows(
    existing: list[dict[str, str]],
    new_rows: list[dict[str, str]],
) -> list[dict[str, str]]:
    merged = {row["answer_id"]: row for row in existing if row.get("answer_id")}
    merged.update({row["answer_id"]: row for row in new_rows if row.get("answer_id")})
    return list(merged.values())


def _write_index(rows: list[dict[str, str]], *, merge: bool = True) -> None:
    if merge:
        rows = _merge_index_rows(_load_existing_index(), rows)
    fields = [
        "answer_id",
        "question_id",
        "qid",
        "keyword_ko",
        "seo_question",
        "search_intent",
        "popularity",
        "char_count",
        "file_path",
        "draft_model",
        "polished_intro",
        "meta_generated",
        "status",
        "generated_at",
    ]
    with INDEX_CSV.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)
    INDEX_JSON.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
