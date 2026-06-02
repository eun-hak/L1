#!/usr/bin/env python3
"""
data2/topics_l2.csv 기반 L3 소분류 생성.

SQLite checkpoint + CSV export. API 한도 초과·중단 시 --resume 으로 재개.

Usage:
  python scripts/generate_taxonomy_l3.py --provider nvidia --count 50 --batch 15 --resume
  python scripts/generate_taxonomy_l3.py --provider groq --count 50 --batch 15
  python scripts/generate_taxonomy_l3.py --provider groq --l2 01-drama
  python scripts/generate_taxonomy_l3.py --resume
  python scripts/generate_taxonomy_l3.py --fresh
  python scripts/generate_taxonomy_l3.py --dry-run
"""

from __future__ import annotations

import argparse
import csv
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))

from groq_client import (  # noqa: E402
    MODEL_FAST as GROQ_MODEL,
    RateLimitExhausted as GroqRateLimitExhausted,
    _keyword_seen,
    generate_l3_topics as groq_generate_l3_topics,
)
from nvidia_client import (  # noqa: E402
    MODEL_NEMOTRON,
    RateLimitExhausted as NvidiaRateLimitExhausted,
    generate_l3_topics as nvidia_generate_l3_topics,
)

PROVIDERS = {
    "groq": (GROQ_MODEL, groq_generate_l3_topics),
    "nvidia": (MODEL_NEMOTRON, nvidia_generate_l3_topics),
}
from taxonomy_state import (  # noqa: E402
    connect,
    count_by_l2,
    export_artifacts,
    import_csv,
    init_db,
    load_all_topics,
    update_l2_progress,
    upsert_topics,
    wipe_db,
)

L1_CSV = ROOT / "data2" / "seed" / "topics_l1.csv"
L2_CSV = ROOT / "data2" / "topics_l2.csv"
BRIEF = ROOT / "data2" / "brief.txt"
OUT_CSV = ROOT / "data2" / "topics_l3.csv"
OUT_JSON = ROOT / "data2" / "topics_l3.json"
MANIFEST = ROOT / "data2" / "manifest_l3.json"
STATE_DB = ROOT / "data2" / "state" / "taxonomy.db"

SLEEP_SEC = 0.8
BATCH_SIZE = 15
MAX_ATTEMPTS = 10

_interrupted = False


def _handle_sigint(signum: int, frame: object) -> None:
    global _interrupted
    _interrupted = True
    print("\n[interrupt] Ctrl+C — 현재까지 checkpoint 저장 후 종료...", flush=True)


signal.signal(signal.SIGINT, _handle_sigint)


def load_l1_map(path: Path) -> dict[str, dict[str, str]]:
    out: dict[str, dict[str, str]] = {}
    with path.open(encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            out[row["l1_code"]] = row
    return out


def load_l2(path: Path, l2_filter: str | None) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    with path.open(encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            if l2_filter and row.get("l2_id") != l2_filter:
                continue
            rows.append(row)
    rows.sort(key=lambda r: (r.get("l1_code", ""), int(r.get("sort_order") or 0)))
    return rows


def load_brief(path: Path) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8").strip()


def _build_row(
    *,
    l2_id: str,
    l1_code: str,
    item: dict[str, str],
    slug: str,
    model: str,
    generated_at: str,
) -> dict[str, str]:
    return {
        "l3_id": f"{l2_id}-{slug}",
        "l2_id": l2_id,
        "l1_code": l1_code,
        "l3_slug": slug,
        "focus_keyword": item["focus_keyword"],
        "title_ko": item["title_ko"],
        "search_intent": item["search_intent"],
        "topic_angle": item["topic_angle"],
        "description": item["description"],
        "model": model,
        "generated_at": generated_at,
    }


def _unique_slug(slug: str, seen: set[str]) -> str:
    if slug not in seen:
        return slug
    base = slug
    n = 2
    while slug in seen:
        slug = f"{base}-{n}"
        n += 1
    return slug


def _save_checkpoint(
    conn,
    *,
    all_l3: list[dict[str, str]],
    l2_total: int,
    count_target: int,
    model: str,
    l2_processed: int,
    interrupted: bool = False,
    last_error: str | None = None,
    state_db: Path,
) -> None:
    export_artifacts(
        conn,
        csv_path=OUT_CSV,
        json_path=OUT_JSON,
        manifest_path=MANIFEST,
        l2_count=l2_total,
        count_target=count_target,
        model=model,
        l2_processed=l2_processed,
        interrupted=interrupted,
        last_error=last_error,
        state_db=str(state_db),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="data2 L3 소분류 생성 (SQLite checkpoint)")
    parser.add_argument("--provider", choices=list(PROVIDERS), default="nvidia")
    parser.add_argument("--input", type=Path, default=L2_CSV)
    parser.add_argument("--state-db", type=Path, default=STATE_DB)
    parser.add_argument("--count", type=int, default=50, help="L2당 L3 개수")
    parser.add_argument("--batch", type=int, default=BATCH_SIZE, help="API 호출당 생성 개수")
    parser.add_argument("--l2", type=str, default=None, help="특정 L2 id만 (예: 01-drama)")
    parser.add_argument("--sleep", type=float, default=SLEEP_SEC)
    parser.add_argument("--resume", action="store_true", help="SQLite/CSV 기존 결과 이어서 생성")
    parser.add_argument("--fresh", action="store_true", help="SQLite·진행 상태 초기화 후 새로 시작")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    model_name, generate_l3_topics = PROVIDERS[args.provider]

    state_db: Path = args.state_db
    if args.fresh and state_db.exists():
        wipe_db(state_db)
        print(f"fresh: {state_db} 초기화")
    else:
        init_db(state_db)

    l1_map = load_l1_map(L1_CSV)
    l2_rows = load_l2(args.input, args.l2)
    if not l2_rows:
        raise SystemExit("L2 데이터가 없습니다.")

    brief = load_brief(BRIEF)
    now = datetime.now(timezone.utc).isoformat()
    l2_total = len(l2_rows)

    with connect(state_db) as conn:
        synced = import_csv(conn, OUT_CSV)
        if synced:
            print(f"SQLite sync: CSV +{synced}행 → {state_db} (총 {len(load_all_topics(conn))}행)")

        if args.l2:
            conn.execute("DELETE FROM l3_topics WHERE l2_id=?", (args.l2,))
            conn.execute("DELETE FROM l2_progress WHERE l2_id=?", (args.l2,))
            conn.commit()

        all_l3 = load_all_topics(conn)
        counts = count_by_l2(conn)

        auto_resume = args.resume or (not args.fresh and len(all_l3) > 0)
        skip_l2 = set()
        if auto_resume and not args.l2:
            skip_l2 = {l2_id for l2_id, c in counts.items() if c >= args.count}
            if skip_l2:
                print(f"resume: {len(skip_l2)}개 L2 스킵 (이미 {args.count}개 이상)")

        global_avoid = [r["focus_keyword"] for r in all_l3]
        to_process = [r for r in l2_rows if r["l2_id"] not in skip_l2]

        print(
            f"L2 {len(to_process)}/{l2_total}개 × L3 {args.count}개 목표 "
            f"(provider={args.provider}, model={model_name}, batch={args.batch}, state={state_db.name})"
        )

        processed = 0
        interrupted = False
        last_error: str | None = None

        try:
            for l2 in to_process:
                if _interrupted:
                    interrupted = True
                    break

                l2_id = l2["l2_id"]
                l1_code = l2["l1_code"]
                l1 = l1_map.get(l1_code, {})
                l1_name = l1.get("name_ko", l1_code)
                l1_desc = l1.get("description", "")
                l2_name = l2["name_ko"]
                print(f"\n[{l2_id}] {l1_name} > {l2_name} ...", flush=True)

                if args.dry_run:
                    processed += 1
                    continue

                existing_l2 = [r for r in all_l3 if r["l2_id"] == l2_id]
                need_total = args.count - len(existing_l2)
                if need_total <= 0:
                    processed += 1
                    continue

                update_l2_progress(
                    conn, l2_id,
                    target_count=args.count,
                    current_count=len(existing_l2),
                    status="in_progress",
                )

                seen_slugs: set[str] = {r["l3_slug"] for r in existing_l2}
                batch_added = 0
                attempts = 0

                while batch_added < need_total and attempts < MAX_ATTEMPTS:
                    if _interrupted:
                        interrupted = True
                        break

                    need = min(args.batch, need_total - batch_added)
                    request_n = min(need + 5, args.batch + 5)
                    current_l2_keywords = [r["focus_keyword"] for r in all_l3 if r["l2_id"] == l2_id]
                    prompt_avoid = current_l2_keywords[-40:]

                    chunk = generate_l3_topics(
                        brief=brief,
                        l1_name=l1_name,
                        l1_description=l1_desc,
                        l2_name=l2_name,
                        l2_description=l2.get("description", ""),
                        count=request_n,
                        avoid_keywords=prompt_avoid,
                        model=model_name,
                    )

                    new_rows: list[dict[str, str]] = []
                    known = list(global_avoid)
                    for item in chunk:
                        kw = item["focus_keyword"]
                        if _keyword_seen(kw, known):
                            continue
                        slug = _unique_slug(item["slug"], seen_slugs)
                        seen_slugs.add(slug)
                        row = _build_row(
                            l2_id=l2_id,
                            l1_code=l1_code,
                            item=item,
                            slug=slug,
                            model=model_name,
                            generated_at=now,
                        )
                        new_rows.append(row)
                        known.append(kw)

                    if new_rows:
                        upsert_topics(conn, new_rows)
                        all_l3.extend(new_rows)
                        global_avoid.extend(r["focus_keyword"] for r in new_rows)
                        batch_added += len(new_rows)
                        current = len(existing_l2) + batch_added
                        update_l2_progress(
                            conn, l2_id,
                            target_count=args.count,
                            current_count=current,
                            status="partial" if current < args.count else "done",
                        )
                        _save_checkpoint(
                            conn,
                            all_l3=all_l3,
                            l2_total=l2_total,
                            count_target=args.count,
                            model=model_name,
                            l2_processed=processed,
                            state_db=state_db,
                        )
                        for row in new_rows:
                            print(f"  · {row['focus_keyword'][:50]}")

                    attempts += 1
                    if batch_added < need_total and chunk:
                        time.sleep(args.sleep)

                final_count = len(existing_l2) + batch_added
                if final_count < args.count:
                    print(f"  [warn] {final_count}/{args.count}개 (신규 {batch_added}/{need_total})")
                    update_l2_progress(
                        conn, l2_id,
                        target_count=args.count,
                        current_count=final_count,
                        status="partial",
                    )
                else:
                    update_l2_progress(
                        conn, l2_id,
                        target_count=args.count,
                        current_count=final_count,
                        status="done",
                    )

                processed += 1
                _save_checkpoint(
                    conn,
                    all_l3=all_l3,
                    l2_total=l2_total,
                    count_target=args.count,
                    model=model_name,
                    l2_processed=processed,
                    state_db=state_db,
                )
                time.sleep(args.sleep)

        except (GroqRateLimitExhausted, NvidiaRateLimitExhausted) as exc:
            interrupted = True
            last_error = str(exc)
            print(f"\n[rate-limit] {exc}", flush=True)
            _save_checkpoint(
                conn,
                all_l3=all_l3,
                l2_total=l2_total,
                count_target=args.count,
                model=model_name,
                l2_processed=processed,
                interrupted=True,
                last_error=last_error,
                state_db=state_db,
            )
            print(
                f"\ncheckpoint 저장됨 ({len(all_l3)}개 L3). "
                f"잠시 후 재실행:\n"
                f"  python scripts/generate_taxonomy_l3.py --provider {args.provider} --resume",
                flush=True,
            )
            raise SystemExit(2) from exc

        if interrupted:
            _save_checkpoint(
                conn,
                all_l3=all_l3,
                l2_total=l2_total,
                count_target=args.count,
                model=model_name,
                l2_processed=processed,
                interrupted=True,
                last_error="user interrupt (Ctrl+C)",
                state_db=state_db,
            )
            print(f"\n중단됨 — {len(all_l3)}개 L3 저장. --resume 으로 재개 가능.")
            raise SystemExit(130)

        if args.dry_run:
            print("dry-run 완료")
            return

        _save_checkpoint(
            conn,
            all_l3=all_l3,
            l2_total=l2_total,
            count_target=args.count,
            model=model_name,
            l2_processed=processed,
            state_db=state_db,
        )
        print(f"\n완료: {len(all_l3)}개 L3 → {OUT_CSV}")


if __name__ == "__main__":
    main()
