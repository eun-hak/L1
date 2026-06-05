#!/usr/bin/env python3
"""
data2 Phase1 파일럿 → Gemini 통합(제목+본문) 생성.

모델: gemini-3.1-flash-lite (제목+본문 1콜, 메타 1콜)

Usage:
  python scripts/generate_data2_articles.py --limit 10
  python scripts/generate_data2_articles.py --limit 10 --resume
  python scripts/generate_data2_articles.py --dry-run
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

from compare_phase1_unified_models import (  # noqa: E402
    GEMINI_FLASH_LITE,
    build_prompt,
    generate_gemini_unified,
    parse_unified_response,
)
from gemini_client import (  # noqa: E402
    MODEL_FAST,
    generate_meta_tags,
    normalize_body_structure,
)

PILOT_CSV = ROOT / "data2" / "pilot" / "phase1_body_pilot.csv"
OUT_DIR = ROOT / "data2" / "answers"
BODY_DIR = OUT_DIR / "body"
INDEX_CSV = OUT_DIR / "answers_index.csv"
INDEX_JSON = OUT_DIR / "answers_index.json"
MANIFEST = OUT_DIR / "manifest_articles.json"

INDEX_FIELDS = [
    "l3_id", "l2_id", "l1_code", "focus_keyword", "seo_title",
    "search_intent", "topic_angle", "char_count", "file_path",
    "title_model", "body_model", "meta_model", "status", "generated_at",
]


def load_pilot(limit: int, offset: int) -> list[dict[str, str]]:
    rows = list(csv.DictReader(PILOT_CSV.open(encoding="utf-8-sig")))
    return rows[offset : offset + limit]


def render_md(row: dict[str, str], seo_title: str, body: str, meta: dict[str, str]) -> str:
    header = f"""---
l3_id: {row['l3_id']}
l2_id: {row['l2_id']}
l1_code: {row['l1_code']}
focus_keyword: {row['focus_keyword']}
seo_title: {seo_title}
search_intent: {row.get('search_intent', '')}
topic_angle: {row.get('topic_angle', '')}
meta_description: {meta.get('meta_description', '')}
tags: {meta.get('tags', '')}
body_model: {GEMINI_FLASH_LITE}
generated_at: {datetime.now(timezone.utc).isoformat()}
---

"""
    body = body.strip()
    if not body.startswith("#"):
        body = f"# {seo_title}\n\n{body}"
    text = header + body + "\n"
    return re.sub(r"---\n(?=#)", "---\n\n", text, count=1)


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--input", type=Path, default=PILOT_CSV)
    p.add_argument("--limit", type=int, default=10)
    p.add_argument("--offset", type=int, default=0)
    p.add_argument("--min-chars", type=int, default=2000)
    p.add_argument("--sleep", type=float, default=2.0)
    p.add_argument("--resume", action="store_true")
    p.add_argument("--no-meta", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    rows = load_pilot(args.limit, args.offset)
    if not rows:
        raise SystemExit("입력 행이 없습니다.")

    print(f"Model: {GEMINI_FLASH_LITE} | rows: {len(rows)} | min body: {args.min_chars}")

    if args.dry_run:
        for r in rows:
            print(f"  {r['l3_id']} | {r['focus_keyword']}")
        return

    BODY_DIR.mkdir(parents=True, exist_ok=True)
    index_rows: list[dict[str, str]] = []
    manifest_items: list[dict] = []
    now = datetime.now(timezone.utc).isoformat()

    for i, row in enumerate(rows, 1):
        l3_id = row["l3_id"]
        out_path = BODY_DIR / f"{l3_id}.md"

        if args.resume and out_path.exists() and out_path.stat().st_size > 800:
            text = out_path.read_text(encoding="utf-8")
            n = len(text)
            print(f"[{i}/{len(rows)}] skip {l3_id} ({n} chars)", flush=True)
            index_rows.append(_index_from_file(row, out_path, n, now))
            continue

        print(f"[{i}/{len(rows)}] {row['focus_keyword'][:30]}...", flush=True)
        seo_title, body, _ = generate_gemini_unified(row, min_body_chars=args.min_chars)
        time.sleep(args.sleep)

        meta = {"meta_description": "", "tags": ""}
        if not args.no_meta:
            meta = generate_meta_tags(seo_title, row["focus_keyword"], model=MODEL_FAST)
            time.sleep(1.0)

        final = render_md(row, seo_title, body, meta)
        out_path.write_text(final, encoding="utf-8")
        n = len(body)
        ok = n >= args.min_chars
        print(f"  → {seo_title[:45]}... | {n} chars {'OK' if ok else 'SHORT'}", flush=True)

        index_rows.append({
            "l3_id": l3_id,
            "l2_id": row["l2_id"],
            "l1_code": row["l1_code"],
            "focus_keyword": row["focus_keyword"],
            "seo_title": seo_title,
            "search_intent": row.get("search_intent", ""),
            "topic_angle": row.get("topic_angle", ""),
            "char_count": str(n),
            "file_path": f"body/{l3_id}.md",
            "title_model": GEMINI_FLASH_LITE,
            "body_model": GEMINI_FLASH_LITE,
            "meta_model": "" if args.no_meta else MODEL_FAST,
            "status": "draft",
            "generated_at": now,
        })
        manifest_items.append({
            "l3_id": l3_id,
            "seo_title": seo_title,
            "body_chars": n,
            "meets_min": ok,
            "path": str(out_path.relative_to(ROOT)),
        })

    with INDEX_CSV.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=INDEX_FIELDS, extrasaction="ignore")
        w.writeheader()
        w.writerows(index_rows)
    INDEX_JSON.write_text(json.dumps(index_rows, ensure_ascii=False, indent=2), encoding="utf-8")
    MANIFEST.write_text(
        json.dumps(
            {"generated_at": now, "model": GEMINI_FLASH_LITE, "count": len(manifest_items), "items": manifest_items},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"\nDone: {len(index_rows)} → {BODY_DIR}/")
    print(f"Index: {INDEX_CSV}")


def _index_from_file(row: dict[str, str], path: Path, n: int, now: str) -> dict[str, str]:
    text = path.read_text(encoding="utf-8")
    m = re.search(r"^seo_title:\s*(.+)$", text, re.M)
    title = m.group(1).strip() if m else row.get("title_ko", "")
    return {
        "l3_id": row["l3_id"],
        "l2_id": row["l2_id"],
        "l1_code": row["l1_code"],
        "focus_keyword": row["focus_keyword"],
        "seo_title": title,
        "search_intent": row.get("search_intent", ""),
        "topic_angle": row.get("topic_angle", ""),
        "char_count": str(n),
        "file_path": f"body/{row['l3_id']}.md",
        "title_model": GEMINI_FLASH_LITE,
        "body_model": GEMINI_FLASH_LITE,
        "meta_model": MODEL_FAST,
        "status": "draft",
        "generated_at": now,
    }


if __name__ == "__main__":
    main()
