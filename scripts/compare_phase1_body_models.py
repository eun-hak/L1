#!/usr/bin/env python3
"""
Phase1 파일럿 본문 모델 비교 (각 3건).

공통: SEO 제목 = Groq llama-3.1-8b-instant
  A) 본문 = Groq Llama 4 Scout 17B
  B) 본문 = Gemini 3.1 Flash Lite

Usage:
  python scripts/compare_phase1_body_models.py
  python scripts/compare_phase1_body_models.py --limit 2
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
    AIQA_BODY_STYLE_RULES,
    AIQA_BODY_SYSTEM,
    MODEL_FAST as GEMINI_FLASH_LITE,
    generate_blog_body,
    normalize_body_structure,
    strip_model_artifacts,
)
from groq_client import chat_full  # noqa: E402

PILOT_CSV = ROOT / "data2" / "pilot" / "phase1_body_pilot.csv"
OUT_DIR = ROOT / "data2" / "test" / "body_model_compare"
OUT_JSON = OUT_DIR / "compare_manifest.json"

GROQ_8B = "llama-3.1-8b-instant"
GROQ_SCOUT = "meta-llama/llama-4-scout-17b-16e-instruct"

# L1 다양하게 3건
SAMPLE_L3_IDS = [
    "11-travel-essentials-domestic-travel-essentials",
    "10-korean-recipe-bibimbap-recipe",
    "13-dog-health-dog-abdominal-pain-relief",
]

SEO_TITLE_SYSTEM = (
    "너는 네이버 블로그 SEO 제목 편집자다. "
    "검색 의도에 맞는 제목 하나만 출력한다. JSON·번호·설명 없이 제목 문자열만."
)


def load_pilot_rows(limit: int, l3_ids: list[str] | None) -> list[dict[str, str]]:
    rows = list(csv.DictReader(PILOT_CSV.open(encoding="utf-8-sig")))
    if l3_ids:
        by_id = {r["l3_id"]: r for r in rows}
        picked = [by_id[i] for i in l3_ids if i in by_id]
        if len(picked) < limit:
            seen = {r["l3_id"] for r in picked}
            for r in rows:
                if r["l3_id"] not in seen:
                    picked.append(r)
                if len(picked) >= limit:
                    break
        return picked[:limit]
    return rows[:limit]


def generate_seo_title_8b(row: dict[str, str]) -> tuple[str, str]:
    fk = row["focus_keyword"]
    intent = row.get("search_intent", "info")
    angle = row.get("topic_angle", "tip")
    draft = row.get("title_ko", "")
    prompt = f"""다음 블로그 글의 SEO 제목을 하나만 작성해줘.

- focus_keyword: {fk}
- search_intent: {intent}
- topic_angle: {angle}
- 참고 초안: {draft}

규칙:
- 25~55자, 명사형·설명형
- clickbait 금지 (완벽 가이드, 놓치면 후회 등)
- 제목 문자열만 출력 (따옴표·JSON 없음)
"""
    result = chat_full(
        prompt,
        system=SEO_TITLE_SYSTEM,
        model=GROQ_8B,
        temperature=0.5,
        max_tokens=256,
    )
    title = result.text.strip().strip('"').strip("'")
    title = re.sub(r"^#+\s*", "", title).split("\n")[0].strip()
    if len(title) < 10:
        title = draft or fk
    return title, result.model


def generate_body_scout(seo_title: str, focus_keyword: str) -> tuple[str, str]:
    prompt = f"""다음 블로그 주제에 대한 본문을 작성해줘.

- 제목(별도, 본문에 H1 쓰지 말 것): {seo_title}
- 핵심 키워드: {focus_keyword}

[분량]
- 도입 일반 문단 2~3개
- 본문 소제목 4개 (**굵은 한 줄** 형식, 각 4~6문장)
- 결론 1문단

{AIQA_BODY_STYLE_RULES}

[추가]
- 네이버 블로그 정보글 톤, 존댓말
- 키워드를 도입·본문에 자연스럽게 2~3회
- 마크다운만 출력
"""
    result = chat_full(
        prompt,
        system=AIQA_BODY_SYSTEM,
        model=GROQ_SCOUT,
        temperature=0.65,
        max_tokens=8192,
    )
    body = normalize_body_structure(strip_model_artifacts(result.text))
    return body, result.model


def generate_body_gemini_flash(seo_title: str, focus_keyword: str) -> tuple[str, str]:
    body = generate_blog_body(
        question_text=seo_title,
        focus_keyword=focus_keyword,
        intro_paragraphs=3,
        body_sections=4,
        sentences_per_section=4,
        model=GEMINI_FLASH_LITE,
    )
    return body, GEMINI_FLASH_LITE


def render_md(
    row: dict[str, str],
    seo_title: str,
    body: str,
    *,
    variant: str,
    title_model: str,
    body_model: str,
) -> str:
    meta = {
        "l3_id": row["l3_id"],
        "l2_id": row["l2_id"],
        "l1_code": row["l1_code"],
        "focus_keyword": row["focus_keyword"],
        "seo_title": seo_title,
        "search_intent": row.get("search_intent", ""),
        "topic_angle": row.get("topic_angle", ""),
        "variant": variant,
        "title_model": title_model,
        "body_model": body_model,
        "body_chars": len(body),
    }
    lines = ["---"]
    for k, v in meta.items():
        lines.append(f"{k}: {v}")
    lines.append("---")
    lines.append("")
    lines.append(f"# {seo_title}")
    lines.append("")
    lines.append(body)
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=3)
    parser.add_argument("--sleep", type=float, default=2.0)
    args = parser.parse_args()

    rows = load_pilot_rows(args.limit, SAMPLE_L3_IDS if args.limit <= 3 else None)
    if not rows:
        raise SystemExit(f"no rows in {PILOT_CSV}")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    scout_dir = OUT_DIR / "scout_body"
    gem_dir = OUT_DIR / "gemini_flash_lite_body"
    scout_dir.mkdir(exist_ok=True)
    gem_dir.mkdir(exist_ok=True)

    results: list[dict] = []

    for i, row in enumerate(rows, 1):
        l3_id = row["l3_id"]
        fk = row["focus_keyword"]
        print(f"[{i}/{len(rows)}] title(8b) {l3_id[:40]}...", flush=True)

        seo_title, title_model = generate_seo_title_8b(row)
        time.sleep(args.sleep)

        print(f"  scout body...", flush=True)
        body_scout, scout_model = generate_body_scout(seo_title, fk)
        scout_path = scout_dir / f"{l3_id}.md"
        scout_path.write_text(
            render_md(
                row, seo_title, body_scout,
                variant="scout",
                title_model=title_model,
                body_model=scout_model,
            ),
            encoding="utf-8",
        )
        time.sleep(args.sleep)

        print(f"  gemini flash lite body...", flush=True)
        body_gem, gem_model = generate_body_gemini_flash(seo_title, fk)
        gem_path = gem_dir / f"{l3_id}.md"
        gem_path.write_text(
            render_md(
                row, seo_title, body_gem,
                variant="gemini_flash_lite",
                title_model=title_model,
                body_model=gem_model,
            ),
            encoding="utf-8",
        )
        time.sleep(args.sleep)

        results.append({
            "l3_id": l3_id,
            "l2_name_ko": row.get("l2_name_ko", ""),
            "focus_keyword": fk,
            "seo_title_8b": seo_title,
            "title_model": GROQ_8B,
            "scout_body_chars": len(body_scout),
            "gemini_body_chars": len(body_gem),
            "scout_path": str(scout_path.relative_to(ROOT)),
            "gemini_path": str(gem_path.relative_to(ROOT)),
            "scout_preview": body_scout[:400],
            "gemini_preview": body_gem[:400],
        })
        print(f"  title: {seo_title[:50]}... | scout {len(body_scout)}c | gem {len(body_gem)}c", flush=True)

    manifest = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "samples": len(results),
        "title_model": GROQ_8B,
        "body_models": {
            "A_scout": GROQ_SCOUT,
            "B_gemini": GEMINI_FLASH_LITE,
        },
        "results": results,
    }
    OUT_JSON.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
