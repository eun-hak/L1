#!/usr/bin/env python3
"""
수집 기사 → 핵심 이슈 JSON (Groq)

Usage:
  python3 naver/scripts/analyze_issue.py
  python3 naver/scripts/analyze_issue.py --limit 3
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from _paths import NEWS_ISSUES_DIR, NEWS_RAW_DIR, ensure_dirs, load_yaml  # noqa: E402
from groq_client import MODEL_FAST, chat, parse_json_object  # noqa: E402

ANALYZE_PROMPT = """다음 뉴스 기사를 분석해 JSON 객체 하나만 출력하세요.

## 기사
- 카테고리: {category}
- 제목: {title}
- 출처: {source}
- URL: {url}
- 본문:
{body}

## 출력 JSON 스키마
{{
  "headline": "한 줄 핵심 (속보형, 40자 내외)",
  "entities": ["인물/프로그램/기관명"],
  "facts": ["확인 가능한 사실만, 3~6개"],
  "angles": [
    {{"type": "timeline|person|controversy|summary", "title": "섹션 제목"}}
  ],
  "seo_keywords": ["검색 키워드 3~5개"],
  "search_intent": "info|entertainment|howto",
  "risk_flags": [],
  "suggested_title_patterns": ["quote_shock|question_why|bracket_solo"]
}}

risk_flags 후보: unverified_claim, defamation, sensitive — 해당 시만 포함.
JSON만 출력."""


def slugify(text: str, max_len: int = 24) -> str:
    text = re.sub(r"[^\w가-힣]+", "_", text.strip())
    text = re.sub(r"_+", "_", text).strip("_")
    return text[:max_len] or "issue"


def parse_json_safe(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    match = re.search(r"\{[\s\S]*\}", text)
    if match:
        return json.loads(match.group())
    return parse_json_object(text)


def issue_path_for(raw_id: str, headline: str) -> Path:
    date = datetime.now(timezone.utc).strftime("%Y%m%d")
    slug = slugify(headline)
    return NEWS_ISSUES_DIR / f"issue_{date}_{slug}_{raw_id}.json"


def list_unprocessed_raw() -> list[dict]:
    items: list[dict] = []
    for path in sorted(NEWS_RAW_DIR.glob("*.json")):
        if path.name.startswith("_"):
            continue
        with path.open(encoding="utf-8") as f:
            row = json.load(f)
        if row.get("status") == "analyzed":
            continue
        items.append(row)
    return items


def mark_raw_analyzed(raw_id: str, issue_id: str) -> None:
    path = NEWS_RAW_DIR / f"{raw_id}.json"
    with path.open(encoding="utf-8") as f:
        data = json.load(f)
    data["status"] = "analyzed"
    data["issue_id"] = issue_id
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def analyze_one(raw: dict, min_score: float) -> dict | None:
    if raw.get("score", 0) < min_score:
        print(f"  [skip] score {raw.get('score')} < {min_score}: {raw.get('title', '')[:40]}")
        return None

    body = raw.get("body_text", "")[:6000]
    prompt = ANALYZE_PROMPT.format(
        category=raw.get("category", ""),
        title=raw.get("title", ""),
        source=raw.get("source", ""),
        url=raw.get("url", ""),
        body=body,
    )
    response = chat(
        prompt,
        system="너는 한국 뉴스 팩트 분석가다. JSON만 출력한다.",
        model=MODEL_FAST,
        temperature=0.3,
    )
    analysis = parse_json_safe(response)

    headline = analysis.get("headline") or raw.get("title", "")[:40]
    issue_id = f"issue_{raw['id']}"
    issue = {
        "issue_id": issue_id,
        "raw_id": raw["id"],
        "headline": headline,
        "entities": analysis.get("entities", []),
        "facts": analysis.get("facts", []),
        "angles": analysis.get("angles", []),
        "seo_keywords": analysis.get("seo_keywords", []),
        "search_intent": analysis.get("search_intent", "info"),
        "risk_flags": analysis.get("risk_flags", []),
        "suggested_title_patterns": analysis.get("suggested_title_patterns", []),
        "source_urls": [raw.get("url", "")],
        "source_titles": [raw.get("title", "")],
        "category": raw.get("category", "general"),
        "og_image": raw.get("og_image", ""),
        "score": raw.get("score", 0),
        "analyzed_at": datetime.now(timezone.utc).isoformat(),
        "status": "analyzed",
    }
    return issue


def main() -> int:
    parser = argparse.ArgumentParser(description="뉴스 이슈 분석")
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()

    ensure_dirs()
    cfg = load_yaml("news_feeds.yaml")
    min_score = float(cfg.get("min_score_to_generate", 0.3))

    raw_list = list_unprocessed_raw()
    if not raw_list:
        print("분석할 신규 기사 없음.")
        return 0

    done = 0
    for raw in raw_list:
        if args.limit is not None and done >= args.limit:
            break
        print(f"[analyze] {raw.get('title', '')[:55]}…")
        issue = analyze_one(raw, min_score)
        if not issue:
            continue

        out = issue_path_for(raw["id"], issue["headline"])
        with out.open("w", encoding="utf-8") as f:
            json.dump(issue, f, ensure_ascii=False, indent=2)
        mark_raw_analyzed(raw["id"], issue["issue_id"])
        done += 1
        print(f"  → {out.name}")

    print(f"\n완료: {done}건 → {NEWS_ISSUES_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
