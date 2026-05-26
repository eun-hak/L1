#!/usr/bin/env python3
"""ES simsimi_entities_v1 키워드 CLI."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.analyze import analyze_query, export_keywords_csv, fetch_overview, fetch_top_keywords  # noqa: E402
from app.es import get_es  # noqa: E402


def cmd_health() -> None:
    es = get_es()
    info = es.info()
    print(json.dumps({"cluster": info["cluster_name"], "version": info["version"]["number"]}, ensure_ascii=False))


def cmd_overview() -> None:
    print(json.dumps(fetch_overview(), ensure_ascii=False, indent=2))


def cmd_top(args: argparse.Namespace) -> None:
    items = fetch_top_keywords(limit=args.limit, safe_mode=not args.unsafe, min_popularity=args.min_pop)
    for row in items:
        print(f"{row['popularity']:>6.1f}  {row['keyword_ko']}  ({row['qid']})")


def cmd_query(args: argparse.Namespace) -> None:
    result = analyze_query(args.q, limit=args.limit, safe_mode=not args.unsafe)
    print(json.dumps(result, ensure_ascii=False, indent=2))


def cmd_export(args: argparse.Namespace) -> None:
    path = export_keywords_csv(
        safe_mode=not args.unsafe,
        max_docs=args.max,
        min_popularity=args.min_pop,
    )
    print(f"exported: {path}")


def main() -> None:
    parser = argparse.ArgumentParser(description="simsimi_entities_v1 키워드 분석 CLI")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("health", help="ES 연결 확인")

    sub.add_parser("overview", help="인덱스 통계")

    p_top = sub.add_parser("top", help="인기 키워드 top N")
    p_top.add_argument("--limit", type=int, default=20)
    p_top.add_argument("--min-pop", type=float, default=0.0)
    p_top.add_argument("--unsafe", action="store_true", help="민감 엔티티 포함")

    p_q = sub.add_parser("query", help="검색어로 키워드 분석")
    p_q.add_argument("q")
    p_q.add_argument("--limit", type=int, default=20)
    p_q.add_argument("--unsafe", action="store_true")

    p_exp = sub.add_parser("export", help="CSV export → keyword/es/")
    p_exp.add_argument("--max", type=int, default=5000)
    p_exp.add_argument("--min-pop", type=float, default=0.0)
    p_exp.add_argument("--unsafe", action="store_true")

    args = parser.parse_args()
    cmds = {
        "health": lambda _: cmd_health(),
        "overview": lambda _: cmd_overview(),
        "top": cmd_top,
        "query": cmd_query,
        "export": cmd_export,
    }
    cmds[args.cmd](args)


if __name__ == "__main__":
    main()
