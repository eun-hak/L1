#!/usr/bin/env python3
"""
제목+본문 한 번에 생성 비교 (Llama 3.1 미사용).

  A) Groq Llama 4 Scout 17B — title + body 단일 호출
  B) Gemini 3.1 Flash Lite — title + body 단일 호출

Usage:
  python scripts/compare_phase1_unified_models.py
  python scripts/compare_phase1_unified_models.py --limit 2
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
    MODEL_FAST as GEMINI_FLASH_LITE,
    chat as gemini_chat,
    normalize_body_structure,
    strip_model_artifacts,
)
from groq_client import chat_full  # noqa: E402

PILOT_CSV = ROOT / "data2" / "pilot" / "phase1_body_pilot.csv"
OUT_DIR = ROOT / "data2" / "test" / "body_model_compare"
OUT_JSON = OUT_DIR / "compare_unified_manifest.json"  # overwritten; min2k uses separate name in main

GROQ_SCOUT = "meta-llama/llama-4-scout-17b-16e-instruct"

SAMPLE_L3_IDS = [
    "11-travel-essentials-domestic-travel-essentials",
    "10-korean-recipe-bibimbap-recipe",
    "13-dog-health-dog-abdominal-pain-relief",
]

UNIFIED_SYSTEM = (
    "너는 네이버 블로그 SEO 정보글 작가다. "
    "요청에 맞는 JSON 하나만 출력한다. 설명·마크다운 코드블록·추론 과정 금지."
)


def load_pilot_rows(limit: int) -> list[dict[str, str]]:
    rows = list(csv.DictReader(PILOT_CSV.open(encoding="utf-8-sig")))
    by_id = {r["l3_id"]: r for r in rows}
    picked = [by_id[i] for i in SAMPLE_L3_IDS if i in by_id]
    if len(picked) < limit:
        seen = {r["l3_id"] for r in picked}
        for r in rows:
            if r["l3_id"] not in seen:
                picked.append(r)
            if len(picked) >= limit:
                break
    return picked[:limit]


def build_prompt(row: dict[str, str], *, min_body_chars: int, expand: bool = False) -> str:
    expand_note = ""
    if expand:
        expand_note = (
            f"\n[중요] 이전 응답이 너무 짧았습니다. body는 반드시 **{min_body_chars}자 이상** "
            "이 되도록 소제목·문단·예시·체크리스트를 늘려 다시 작성하세요.\n"
        )
    return f"""다음 주제로 네이버 블로그 글의 SEO 제목과 본문을 한 번에 작성해줘.
{expand_note}
- focus_keyword: {row['focus_keyword']}
- search_intent: {row.get('search_intent', 'info')}
- topic_angle: {row.get('topic_angle', 'tip')}
- L2 카테고리: {row.get('l2_name_ko', '')} ({row.get('l1_name_ko', '')})
- 참고 설명: {row.get('description', '')}

[seo_title 규칙]
- 25~55자, 명사형·설명형
- clickbait 금지 (완벽 가이드, 놓치면 후회, 꼭 알아야 할, 완벽하게)

[body 규칙]
{AIQA_BODY_STYLE_RULES}
- body 필드에는 H1(#) 제목 넣지 말 것
- 도입 3~4문단 + 소제목 5~6개(**굵은 한 줄**, 각 소제목당 5~7문장) + 결론 2문단
- **body 본문만 최소 {min_body_chars}자 이상** (한글 기준, 공백 포함)
- 각 소제목 아래 구체적 예시·숫자·체크 포인트 포함
- 키워드 3~5회 자연스럽게

JSON 형식만 출력 (코드블록·```json 금지, body 안 줄바꿈은 \\n으로 이스케이프):
{{"seo_title": "제목", "body": "마크다운 본문"}}
"""


def parse_unified_response(raw: str, fallback_title: str) -> tuple[str, str]:
    text = strip_model_artifacts(raw)
    fenced = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if fenced:
        text = fenced.group(1).strip()
    else:
        text = text.strip()

    title_m = re.search(r'"seo_title"\s*:\s*"((?:[^"\\]|\\.)*)"', text)
    if title_m:
        title = title_m.group(1).replace("\\n", "\n").replace('\\"', '"')
        body_m = re.search(r'"body"\s*:\s*"\s*([\s\S]*?)"\s*\n?\s*\}', text)
        if body_m:
            body = body_m.group(1).replace("\\n", "\n").replace('\\"', '"')
            return title, normalize_body_structure(body)

    block = re.search(r"\{[\s\S]*\}", text)
    if block:
        try:
            data = json.loads(block.group())
            title = str(data.get("seo_title", "")).strip()
            body = str(data.get("body", "")).strip()
            if title and body:
                return title, normalize_body_structure(body)
        except json.JSONDecodeError:
            pass

    lines = text.strip().splitlines()
    title = lines[0].strip().strip("#").strip('"') if lines else fallback_title
    if title.startswith("```"):
        title = fallback_title
    body = normalize_body_structure("\n".join(lines[1:]).strip())
    return title or fallback_title, body


def generate_scout_unified(
    row: dict[str, str], *, min_body_chars: int,
) -> tuple[str, str, str]:
    fallback = row.get("title_ko", row["focus_keyword"])
    for expand in (False, True):
        result = chat_full(
            build_prompt(row, min_body_chars=min_body_chars, expand=expand),
            system=UNIFIED_SYSTEM,
            model=GROQ_SCOUT,
            temperature=0.65,
            max_tokens=8192,
        )
        title, body = parse_unified_response(result.text, fallback)
        if len(body) >= min_body_chars or expand:
            return title, body, result.model
    return title, body, result.model


def generate_gemini_unified(
    row: dict[str, str], *, min_body_chars: int,
) -> tuple[str, str, str]:
    fallback = row.get("title_ko", row["focus_keyword"])
    for expand in (False, True):
        raw = gemini_chat(
            build_prompt(row, min_body_chars=min_body_chars, expand=expand),
            system=UNIFIED_SYSTEM,
            model=GEMINI_FLASH_LITE,
            temperature=0.65,
            max_tokens=8192,
        )
        title, body = parse_unified_response(raw, fallback)
        if len(body) >= min_body_chars or expand:
            return title, body, GEMINI_FLASH_LITE
    return title, body, GEMINI_FLASH_LITE


def render_md(
    row: dict[str, str],
    seo_title: str,
    body: str,
    *,
    variant: str,
    model: str,
) -> str:
    meta = {
        "l3_id": row["l3_id"],
        "l2_id": row["l2_id"],
        "focus_keyword": row["focus_keyword"],
        "seo_title": seo_title,
        "variant": variant,
        "unified_model": model,
        "mode": "title_and_body_single_call",
        "body_chars": len(body),
        "total_chars": len(seo_title) + len(body),
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
    parser.add_argument("--min-chars", type=int, default=2000, help="body 최소 글자 수")
    parser.add_argument("--out-suffix", default="", help="출력 폴더 접미사 (예: _2k)")
    args = parser.parse_args()

    global OUT_JSON
    suffix = args.out_suffix or (f"_min{args.min_chars}" if args.min_chars != 2000 else "_2k")
    OUT_JSON = OUT_DIR / f"compare_unified{suffix}_manifest.json"

    rows = load_pilot_rows(args.limit)
    scout_dir = OUT_DIR / f"unified_scout{suffix}"
    gem_dir = OUT_DIR / f"unified_gemini_flash_lite{suffix}"
    scout_dir.mkdir(parents=True, exist_ok=True)
    gem_dir.mkdir(parents=True, exist_ok=True)

    results: list[dict] = []

    for i, row in enumerate(rows, 1):
        l3_id = row["l3_id"]
        print(f"[{i}/{len(rows)}] unified scout {l3_id[:42]}...", flush=True)
        title_s, body_s, model_s = generate_scout_unified(row, min_body_chars=args.min_chars)
        (scout_dir / f"{l3_id}.md").write_text(
            render_md(row, title_s, body_s, variant="unified_scout", model=model_s),
            encoding="utf-8",
        )
        time.sleep(args.sleep)

        print(f"  unified gemini flash lite...", flush=True)
        title_g, body_g, model_g = generate_gemini_unified(row, min_body_chars=args.min_chars)
        (gem_dir / f"{l3_id}.md").write_text(
            render_md(row, title_g, body_g, variant="unified_gemini", model=model_g),
            encoding="utf-8",
        )
        time.sleep(args.sleep)

        results.append({
            "l3_id": l3_id,
            "focus_keyword": row["focus_keyword"],
            "min_body_chars": args.min_chars,
            "scout": {
                "seo_title": title_s,
                "body_chars": len(body_s),
                "meets_min": len(body_s) >= args.min_chars,
                "path": str((scout_dir / f"{l3_id}.md").relative_to(ROOT)),
            },
            "gemini": {
                "seo_title": title_g,
                "body_chars": len(body_g),
                "meets_min": len(body_g) >= args.min_chars,
                "path": str((gem_dir / f"{l3_id}.md").relative_to(ROOT)),
            },
            "title_match": title_s == title_g,
        })
        print(
            f"  scout: {title_s[:40]}... ({len(body_s)}c"
            f"{' OK' if len(body_s) >= args.min_chars else ' SHORT'}) | "
            f"gem: {title_g[:40]}... ({len(body_g)}c"
            f"{' OK' if len(body_g) >= args.min_chars else ' SHORT'})",
            flush=True,
        )

    manifest = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mode": "title_and_body_single_call",
        "min_body_chars": args.min_chars,
        "models": {"scout": GROQ_SCOUT, "gemini": GEMINI_FLASH_LITE},
        "samples": len(results),
        "results": results,
    }
    OUT_JSON.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
