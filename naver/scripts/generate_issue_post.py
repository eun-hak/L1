#!/usr/bin/env python3
"""
이슈 JSON → SEO 블로그 body.md + meta.json

Usage:
  python3 naver/scripts/generate_issue_post.py
  python3 naver/scripts/generate_issue_post.py --issue-id issue_abc123
  python3 naver/scripts/generate_issue_post.py --limit 2
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

from _paths import (  # noqa: E402
    DRAFTS_DIR,
    NEWS_ISSUES_DIR,
    TEMPLATES_DIR,
    ensure_dirs,
    load_yaml,
)
from groq_client import MODEL_QUALITY, chat  # noqa: E402


def list_pending_issues() -> list[dict]:
    items: list[dict] = []
    for path in sorted(NEWS_ISSUES_DIR.glob("issue_*.json")):
        with path.open(encoding="utf-8") as f:
            issue = json.load(f)
        if issue.get("status") == "drafted":
            continue
        draft_meta = DRAFTS_DIR / issue["issue_id"] / "meta.json"
        if draft_meta.exists():
            continue
        items.append(issue)
    return items


def load_system_prompt() -> str:
    path = TEMPLATES_DIR / "issue_post_system.txt"
    return path.read_text(encoding="utf-8")


def build_user_prompt(issue: dict, seo_cfg: dict) -> str:
    facts = "\n".join(f"- {f}" for f in issue.get("facts", []))
    angles = "\n".join(
        f"- [{a.get('type', 'summary')}] {a.get('title', '')}" for a in issue.get("angles", [])
    )
    keywords = ", ".join(issue.get("seo_keywords", []))
    risks = ", ".join(issue.get("risk_flags", [])) or "없음"
    sources = "\n".join(f"- {u}" for u in issue.get("source_urls", []))

    min_c = seo_cfg.get("body", {}).get("min_chars", 1500)
    max_c = seo_cfg.get("body", {}).get("max_chars", 3500)
    img_n = seo_cfg.get("image_placeholder_count", 6)

    return f"""다음 이슈를 네이버 블로그 SEO 글(마크다운)로 작성하세요.

## 이슈
- 카테고리: {issue.get('category', '')}
- 헤드라인: {issue.get('headline', '')}
- 핵심 키워드: {keywords}
- search_intent: {issue.get('search_intent', 'info')}
- risk_flags: {risks}

## 확인된 팩트 (이것만 사용, 추측 금지)
{facts}

## 권장 섹션
{angles}

## 출처 (글 하단에 링크로 표기)
{sources}

## 제목
- H1 제목 1개 (35~55자, 클릭 유도: 따옴표·말줄임·물음표 활용)
- suggested patterns: {', '.join(issue.get('suggested_title_patterns', []))}

## 분량·형식
- {min_c}~{max_c}자 (공백 포함)
- ![이미지 N](images/0N.jpg) placeholder {img_n}개
- 문단 1~2문장, > 인용 블록, [섹션] 또는 ◇ 소제목
- "솔직히 저도" 1인칭 1회, 마무리 CTA + 해시태그

마크다운만 출력."""


def extract_title(body: str) -> str:
    for line in body.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return "제목 없음"


def quality_check(body: str, issue: dict, seo_cfg: dict) -> list[str]:
    errors: list[str] = []
    min_c = seo_cfg.get("body", {}).get("min_chars", 1500)
    max_c = seo_cfg.get("body", {}).get("max_chars", 3500)
    length = len(body)
    if length < min_c * 0.7:
        errors.append(f"분량 부족 ({length}자)")
    if length > max_c * 1.3:
        errors.append(f"분량 과다 ({length}자)")

    img_min = max(3, seo_cfg.get("image_placeholder_count", 6) - 2)
    imgs = len(re.findall(r"!\[이미지", body))
    if imgs < img_min:
        errors.append(f"이미지 placeholder 부족 ({imgs}개)")

    flags = issue.get("risk_flags", [])
    if flags and "확인" not in body and "※" not in body:
        errors.append("risk_flags 있는데 면책 문구 없음")

    if "솔직히" not in body and "저도" not in body:
        errors.append("1인칭 감상 없음")

    return errors


def mark_issue_drafted(issue_id: str) -> None:
    for path in NEWS_ISSUES_DIR.glob("issue_*.json"):
        with path.open(encoding="utf-8") as f:
            data = json.load(f)
        if data.get("issue_id") == issue_id:
            data["status"] = "drafted"
            with path.open("w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            break


def generate_one(issue: dict, seo_cfg: dict) -> Path:
    system = load_system_prompt()
    user = build_user_prompt(issue, seo_cfg)
    body = chat(user, system=system, model=MODEL_QUALITY, temperature=0.55)
    body = body.strip()
    if body.startswith("```"):
        body = re.sub(r"^```(?:markdown|md)?\s*", "", body)
        body = re.sub(r"\s*```$", "", body)

    title = extract_title(body)
    errors = quality_check(body, issue, seo_cfg)
    status = "review" if errors else "ready"

    out_dir = DRAFTS_DIR / issue["issue_id"]
    out_dir.mkdir(parents=True, exist_ok=True)
    images_dir = out_dir / "images"
    images_dir.mkdir(exist_ok=True)

    # og:image URL 메타에 보관 (resolve_images 단계에서 다운로드)
    meta = {
        "issue_id": issue["issue_id"],
        "title": title,
        "category": issue.get("category", ""),
        "seo_keywords": issue.get("seo_keywords", []),
        "source_urls": issue.get("source_urls", []),
        "og_image": issue.get("og_image", ""),
        "risk_flags": issue.get("risk_flags", []),
        "status": status,
        "quality_errors": errors,
        "char_count": len(body),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    with (out_dir / "meta.json").open("w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    with (out_dir / "issue.json").open("w", encoding="utf-8") as f:
        json.dump(issue, f, ensure_ascii=False, indent=2)

    header = f"> 자동 생성 초안 · {datetime.now(timezone.utc).strftime('%Y-%m-%d')}\n\n"
    with (out_dir / "body.md").open("w", encoding="utf-8") as f:
        f.write(header + body + "\n")

    mark_issue_drafted(issue["issue_id"])
    return out_dir


def main() -> int:
    parser = argparse.ArgumentParser(description="SEO 블로그 초안 생성")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--issue-id", type=str, default=None)
    args = parser.parse_args()

    ensure_dirs()
    seo_cfg = load_yaml("seo_style.yaml")

    if args.issue_id:
        matches = list(NEWS_ISSUES_DIR.glob(f"*{args.issue_id}*.json"))
        if not matches:
            print(f"이슈 없음: {args.issue_id}")
            return 1
        issues = [json.loads(matches[0].read_text(encoding="utf-8"))]
    else:
        issues = list_pending_issues()

    if not issues:
        print("생성할 이슈 없음.")
        return 0

    done = 0
    for issue in issues:
        if args.limit is not None and done >= args.limit:
            break
        print(f"[generate] {issue.get('headline', '')[:50]}…")
        out = generate_one(issue, seo_cfg)
        meta = json.loads((out / "meta.json").read_text(encoding="utf-8"))
        print(f"  → {out} (status={meta['status']}, {meta['char_count']}자)")
        if meta.get("quality_errors"):
            print(f"     warnings: {', '.join(meta['quality_errors'])}")
        done += 1

    print(f"\n완료: {done}건 → {DRAFTS_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
