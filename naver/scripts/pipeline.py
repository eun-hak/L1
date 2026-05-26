#!/usr/bin/env python3
"""
naver/ 파이프라인 오케스트레이션

Usage:
  python3 naver/scripts/pipeline.py run              # ingest → analyze → generate
  python3 naver/scripts/pipeline.py ingest [--limit N]
  python3 naver/scripts/pipeline.py analyze [--limit N]
  python3 naver/scripts/pipeline.py generate [--limit N]
  python3 naver/scripts/pipeline.py status
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
NAVER_ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(SCRIPT_DIR))

from _paths import DRAFTS_DIR, NEWS_ISSUES_DIR, NEWS_RAW_DIR, ensure_dirs  # noqa: E402


def run_script(name: str, extra: list[str] | None = None) -> int:
    cmd = [sys.executable, str(SCRIPT_DIR / name)] + (extra or [])
    print(f"\n{'=' * 60}\n$ {' '.join(cmd)}\n")
    return subprocess.call(cmd)


def count_json(directory: Path, pattern: str = "*.json", skip_index: bool = True) -> int:
    n = 0
    for p in directory.glob(pattern):
        if skip_index and p.name.startswith("_"):
            continue
        n += 1
    return n


def status() -> None:
    ensure_dirs()
    raw_n = count_json(NEWS_RAW_DIR)
    issue_n = count_json(NEWS_ISSUES_DIR)
    draft_n = sum(1 for _ in DRAFTS_DIR.iterdir() if _.is_dir() and ( _ / "body.md").exists())

    pending_raw = 0
    for p in NEWS_RAW_DIR.glob("*.json"):
        if p.name.startswith("_"):
            continue
        data = json.loads(p.read_text(encoding="utf-8"))
        if data.get("status") != "analyzed":
            pending_raw += 1

    print("=== naver/ 파이프라인 상태 ===")
    print(f"  수집 기사 (raw):     {raw_n}건 (미분석 {pending_raw}건)")
    print(f"  이슈 (issues):       {issue_n}건")
    print(f"  초안 (drafts):       {draft_n}건")
    print(f"\n  data/news/raw/      → {NEWS_RAW_DIR}")
    print(f"  data/news/issues/   → {NEWS_ISSUES_DIR}")
    print(f"  data/publish/drafts/→ {DRAFTS_DIR}")


def main() -> int:
    parser = argparse.ArgumentParser(description="naver 블로그 자동화 파이프라인")
    parser.add_argument(
        "command",
        choices=["run", "ingest", "analyze", "generate", "status"],
    )
    parser.add_argument("--limit", type=int, default=None, help="단계별 처리 건수 제한")
    args = parser.parse_args()

    ensure_dirs()
    extra = ["--limit", str(args.limit)] if args.limit is not None else []

    if args.command == "status":
        status()
        return 0

    if args.command == "ingest":
        return run_script("ingest_news.py", extra)

    if args.command == "analyze":
        return run_script("analyze_issue.py", extra)

    if args.command == "generate":
        return run_script("generate_issue_post.py", extra)

    # run = full pipeline
    steps = [
        ("ingest_news.py", extra),
        ("analyze_issue.py", extra),
        ("generate_issue_post.py", extra),
    ]
    for script, opts in steps:
        code = run_script(script, opts)
        if code != 0:
            return code
    status()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
