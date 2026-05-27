#!/usr/bin/env python3
"""기존 SEO 답변에서 '## 도입', '## 핵심 한 줄' 섹션 제목 제거."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from groq_client import normalize_body_structure  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DIR = ROOT / "keyword" / "es" / "answers" / "body"


def split_frontmatter(text: str) -> tuple[str, str]:
    if not text.startswith("---"):
        return "", text
    parts = text.split("---", 2)
    if len(parts) < 3:
        return "", text
    return f"---{parts[1]}---\n\n", parts[2]


def fix_file(path: Path) -> bool:
    frontmatter, body = split_frontmatter(path.read_text(encoding="utf-8"))
    fixed = normalize_body_structure(body)
    if fixed == body.strip():
        return False
    path.write_text(f"{frontmatter}{fixed}\n", encoding="utf-8")
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description="SEO body 도입 섹션 제목 정리")
    parser.add_argument("--dir", type=Path, default=DEFAULT_DIR)
    args = parser.parse_args()

    changed = 0
    for path in sorted(args.dir.glob("*-seo.md")):
        if fix_file(path):
            changed += 1
            print(f"fixed: {path.name}")

    print(f"\n완료: {changed}개 수정")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
