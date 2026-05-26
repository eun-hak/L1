#!/usr/bin/env python3
"""
Groq로 L1/L2/L3 카테고리를 **처음부터 새로** 창조.

- data/topics_*.csv 는 참조하지 않음
- keyword/brief.txt 의 기획 방향을 반영
- 결과: keyword/taxonomy/

Usage:
  # 소규모 테스트 (L1 2개 × L2 3개 × L3 5개)
  python scripts/generate_taxonomy_groq.py --l1-count 2 --l2-per-l1 3 --l3-per-l2 5

  # 본격 생성
  python scripts/generate_taxonomy_groq.py --l1-count 12 --l2-per-l1 6 --l3-per-l2 12

  # brief 직접 지정
  python scripts/generate_taxonomy_groq.py --brief keyword/brief.txt --l1-count 5
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
KEYWORD_DIR = ROOT / "keyword"
TAXONOMY_DIR = KEYWORD_DIR / "taxonomy"
BRIEF_PATH = KEYWORD_DIR / "brief.txt"
RAW_DIR = TAXONOMY_DIR / "raw"

sys.path.insert(0, str(Path(__file__).resolve().parent))
from groq_client import (  # noqa: E402
    MODEL_FAST,
    generate_l1_categories,
    generate_l2_categories,
    generate_l3_topics,
)

L1_FIELDS = ["l1_code", "l1_slug", "name_ko", "theme", "description", "sort_order"]
L2_FIELDS = ["l2_id", "l1_code", "l2_slug", "name_ko", "description", "sort_order"]
L3_FIELDS = [
    "l3_id",
    "l2_id",
    "l1_code",
    "l3_slug",
    "focus_keyword",
    "seo_title",
    "search_intent",
    "topic_angle",
    "sort_order",
]
QUESTION_FIELDS = [
    "question_id",
    "l3_id",
    "l2_id",
    "l1_code",
    "search_intent",
    "focus_keyword",
    "question_text",
]


def slugify(text: str, max_len: int = 40) -> str:
    s = re.sub(r"[^\w\s-]", "", text, flags=re.UNICODE)
    s = re.sub(r"[-\s·/]+", "-", s).strip("-").lower()
    return (s[:max_len] or "topic").strip("-")


def load_brief(path: Path) -> str:
    if not path.exists():
        return (
            "네이버 블로그 SEO용 니치 카테고리를 새로 만든다. "
            "뻔한 라이프/재테크/자기계발 대분류는 피하고, "
            "검색 수요 있는 구체적 주제 위주로 설계한다."
        )
    text = path.read_text(encoding="utf-8").strip()
    lines = [ln for ln in text.splitlines() if not ln.strip().startswith("#")]
    return "\n".join(lines).strip() or text


def write_csv(path: Path, fieldnames: list[str], rows: list[dict[str, str]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def save_all(
    l1_rows: list[dict[str, str]],
    l2_rows: list[dict[str, str]],
    l3_rows: list[dict[str, str]],
    question_rows: list[dict[str, str]],
    manifest: dict,
) -> None:
    TAXONOMY_DIR.mkdir(parents=True, exist_ok=True)
    write_csv(TAXONOMY_DIR / "topics_l1.csv", L1_FIELDS, l1_rows)
    write_csv(TAXONOMY_DIR / "topics_l2.csv", L2_FIELDS, l2_rows)
    write_csv(TAXONOMY_DIR / "topics_l3.csv", L3_FIELDS, l3_rows)
    write_csv(TAXONOMY_DIR / "questions.csv", QUESTION_FIELDS, question_rows)
    (TAXONOMY_DIR / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Groq로 L1/L2/L3 카테고리 새로 창조")
    parser.add_argument("--brief", type=Path, default=BRIEF_PATH, help="기획 방향 파일")
    parser.add_argument("--l1-count", type=int, default=3, help="생성할 L1 개수")
    parser.add_argument("--l2-per-l1", type=int, default=5, help="L1당 L2 개수")
    parser.add_argument("--l3-per-l2", type=int, default=10, help="L2당 L3 개수")
    parser.add_argument("--model", default=MODEL_FAST, help="Groq 모델")
    parser.add_argument(
        "--resume",
        action="store_true",
        help="keyword/taxonomy/raw 에 저장된 진행 상태 이어하기",
    )
    args = parser.parse_args()

    brief = load_brief(args.brief)
    now = datetime.now(timezone.utc).isoformat()

    l1_rows: list[dict[str, str]] = []
    l2_rows: list[dict[str, str]] = []
    l3_rows: list[dict[str, str]] = []
    question_rows: list[dict[str, str]] = []

    RAW_DIR.mkdir(parents=True, exist_ok=True)
    state_path = RAW_DIR / "state.json"

    if args.resume and state_path.exists():
        state = json.loads(state_path.read_text(encoding="utf-8"))
        l1_rows = state.get("l1_rows", [])
        l2_rows = state.get("l2_rows", [])
        l3_rows = state.get("l3_rows", [])
        question_rows = state.get("question_rows", [])
        print(f"Resume: L1={len(l1_rows)} L2={len(l2_rows)} L3={len(l3_rows)}")

    seen_l1: set[str] = {r["name_ko"] for r in l1_rows}
    seen_l2: set[str] = {r["name_ko"] for r in l2_rows}
    seen_l3: set[str] = {r["focus_keyword"] for r in l3_rows}

    print(f"Model: {args.model}")
    print(f"Brief: {args.brief}")
    print(f"Target: L1={args.l1_count} × L2={args.l2_per_l1} × L3={args.l3_per_l2}")
    print(f"Output: {TAXONOMY_DIR}/")
    print()

    # --- L1 ---
    if len(l1_rows) < args.l1_count:
        need = args.l1_count - len(l1_rows)
        print(f"[L1] generating {need} ...", flush=True)
        items = generate_l1_categories(
            brief=brief,
            count=need,
            avoid_names=list(seen_l1),
            model=args.model,
        )
        start_code = len(l1_rows) + 1
        for i, item in enumerate(items, start=start_code):
            code = f"{i:02d}"
            l1_rows.append(
                {
                    "l1_code": code,
                    "l1_slug": slugify(item["name_ko"]),
                    "name_ko": item["name_ko"],
                    "theme": item["theme"],
                    "description": item["description"],
                    "sort_order": str(i),
                }
            )
            seen_l1.add(item["name_ko"])
            print(f"  L1 {code}: {item['name_ko']}")

    # --- L2 ---
    for l1 in l1_rows:
        existing_l2 = [r for r in l2_rows if r["l1_code"] == l1["l1_code"]]
        if len(existing_l2) >= args.l2_per_l1:
            continue

        need = args.l2_per_l1 - len(existing_l2)
        print(f"\n[L2] {l1['name_ko']} → {need}개 ...", flush=True)
        items = generate_l2_categories(
            brief=brief,
            l1_name=l1["name_ko"],
            l1_theme=l1["theme"],
            l1_description=l1["description"],
            count=need,
            avoid_names=[r["name_ko"] for r in l2_rows],
            model=args.model,
        )
        start_order = len(existing_l2) + 1
        added = 0
        for j, item in enumerate(items, start=start_order):
            if item["name_ko"] in seen_l2:
                continue
            l2_slug = slugify(item["name_ko"])
            l2_id = f"{l1['l1_code']}-{l2_slug}"
            if l2_id in {r["l2_id"] for r in l2_rows}:
                l2_id = f"{l2_id}-{j:02d}"
            l2_rows.append(
                {
                    "l2_id": l2_id,
                    "l1_code": l1["l1_code"],
                    "l2_slug": l2_slug,
                    "name_ko": item["name_ko"],
                    "description": item["description"],
                    "sort_order": str(len(existing_l2) + added + 1),
                }
            )
            seen_l2.add(item["name_ko"])
            added += 1
            print(f"  {l2_id}: {item['name_ko']}")

        if added < need:
            print(f"  (warning: L2 {added}/{need} only — retry with --resume if needed)")

        _persist_state(state_path, l1_rows, l2_rows, l3_rows, question_rows)
        save_all(
            l1_rows,
            l2_rows,
            l3_rows,
            question_rows,
            _manifest(args, brief, now, partial=True),
        )

    # --- L3 ---
    l1_name_map = {r["l1_code"]: r["name_ko"] for r in l1_rows}
    for l2 in l2_rows:
        existing_l3 = [r for r in l3_rows if r["l2_id"] == l2["l2_id"]]
        if len(existing_l3) >= args.l3_per_l2:
            continue

        need = args.l3_per_l2 - len(existing_l3)
        l1_name = l1_name_map[l2["l1_code"]]
        print(f"\n[L3] {l1_name} > {l2['name_ko']} → {need}개 ...", flush=True)
        try:
            items = generate_l3_topics(
                brief=brief,
                l1_name=l1_name,
                l2_name=l2["name_ko"],
                l2_description=l2["description"],
                count=need,
                avoid_keywords=list(seen_l3),
                model=args.model,
            )
        except Exception as exc:
            print(f"  ERROR: {exc} — skip (retry with --resume)")
            continue
        start_order = len(existing_l3) + 1
        for k, item in enumerate(items, start=start_order):
            focus = item["focus_keyword"]
            slug = slugify(focus)[:30]
            l3_id = f"{l2['l2_id']}-{slug}-{k:02d}"
            l3_rows.append(
                {
                    "l3_id": l3_id,
                    "l2_id": l2["l2_id"],
                    "l1_code": l2["l1_code"],
                    "l3_slug": slug,
                    "focus_keyword": focus,
                    "seo_title": item["seo_title"],
                    "search_intent": item["search_intent"],
                    "topic_angle": item["topic_angle"],
                    "sort_order": str(k),
                }
            )
            question_rows.append(
                {
                    "question_id": l3_id,
                    "l3_id": l3_id,
                    "l2_id": l2["l2_id"],
                    "l1_code": l2["l1_code"],
                    "search_intent": item["search_intent"],
                    "focus_keyword": focus,
                    "question_text": item["seo_title"],
                }
            )
            seen_l3.add(focus)

        print(f"  +{len(items)} topics")
        _persist_state(state_path, l1_rows, l2_rows, l3_rows, question_rows)
        save_all(
            l1_rows,
            l2_rows,
            l3_rows,
            question_rows,
            _manifest(args, brief, now, partial=False),
        )

    manifest = _manifest(args, brief, now, partial=False)
    save_all(l1_rows, l2_rows, l3_rows, question_rows, manifest)

    print("\n--- Done ---")
    print(f"L1: {len(l1_rows)} | L2: {len(l2_rows)} | L3: {len(l3_rows)}")
    print(f"Saved: {TAXONOMY_DIR}/topics_l1.csv")
    print(f"       {TAXONOMY_DIR}/topics_l2.csv")
    print(f"       {TAXONOMY_DIR}/topics_l3.csv")
    print(f"       {TAXONOMY_DIR}/questions.csv")


def _persist_state(
    path: Path,
    l1_rows: list[dict[str, str]],
    l2_rows: list[dict[str, str]],
    l3_rows: list[dict[str, str]],
    question_rows: list[dict[str, str]],
) -> None:
    path.write_text(
        json.dumps(
            {
                "l1_rows": l1_rows,
                "l2_rows": l2_rows,
                "l3_rows": l3_rows,
                "question_rows": question_rows,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def _manifest(args: argparse.Namespace, brief: str, now: str, *, partial: bool) -> dict:
    return {
        "generated_at": now,
        "model": args.model,
        "brief_file": str(args.brief),
        "brief_preview": brief[:500],
        "l1_count": args.l1_count,
        "l2_per_l1": args.l2_per_l1,
        "l3_per_l2": args.l3_per_l2,
        "partial": partial,
        "note": "data/topics_*.csv 와 무관하게 새로 창조된 taxonomy",
    }


if __name__ == "__main__":
    main()
