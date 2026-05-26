#!/usr/bin/env python3
"""Groq API 연결 테스트."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from groq_client import generate_blog_titles, chat, MODEL_FAST  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Groq API 테스트")
    parser.add_argument(
        "prompt",
        nargs="?",
        default="씨제이택배배송조회 관련 블로그 제목 10개 추천해줘",
        help="보낼 프롬프트",
    )
    parser.add_argument(
        "--titles",
        metavar="KEYWORD",
        help="키워드로 제목 10개 생성 (예: --titles 씨제이택배)",
    )
    args = parser.parse_args()

    if args.titles:
        result = generate_blog_titles(args.titles)
    else:
        result = chat(args.prompt, model=MODEL_FAST)

    print(result)


if __name__ == "__main__":
    main()
