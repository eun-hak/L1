#!/usr/bin/env python3
"""
네이버 검색광고 API를 이용한 L2 확장 실험 스크립트.

파이프라인 (넓게 수집 → 마지막 dedup):
    python3 scripts/expand_l2_naver.py collect --l1-code 18 --depth 2
    python3 scripts/expand_l2_naver.py dedup  --l1-code 18

한 번에:
    python3 scripts/expand_l2_naver.py all --l1-code 18 --depth 2

산출물:
    outputs/game_api_raw.csv         — API 원본 (append-only)
    outputs/game_collect_state.json  — 이미 조회한 시드 추적
    outputs/game_l2_candidates.csv   — dedup 후 L2 후보 (UTF-8 BOM)
"""

import csv
import json
import re
import sys
import time
import hmac
import hashlib
import base64
import os
import argparse
import logging
from datetime import datetime, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv

# ─── 경로 ─────────────────────────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data2"
OUTPUT_DIR = PROJECT_ROOT / "outputs"
OUTPUT_DIR.mkdir(exist_ok=True)

sys.path.insert(0, str(PROJECT_ROOT / "scripts"))
from validate_batch import norm  # noqa: E402

load_dotenv(PROJECT_ROOT / ".env")
NAVER_API_KEY = os.environ["NAVER_AD_ACCESS_LICENSE"]
NAVER_SECRET_KEY = os.environ["NAVER_AD_SECRET_KEY"]
NAVER_CUSTOMER_ID = os.environ["NAVER_AD_CUSTOMER_ID"]

# ─── 튜닝 상수 ─────────────────────────────────────────────────────────────────
MONTHLY_TOTAL_THRESHOLD = 1_000
API_CALL_INTERVAL = 1.2
SEED_CHUNK_SIZE = 5
MAX_RETRIES = 3

# 2-hop 시드 선정
HOP2_MIN_TOTAL = 1_000       # 1-hop 결과 중 2-hop 시드로 쓸 최소 검색수
HOP2_MAX_SEEDS = 80          # 2-hop 시드 최대 개수
DEFAULT_MAX_CALLS = 200      # 1회 collect 실행당 API 호출 상한

RAW_FIELDS = [
    "rel_keyword", "monthly_pc", "monthly_mobile", "monthly_total",
    "comp_idx", "depth", "parent_keyword", "hint_keywords",
    "source_seed", "collected_at",
]
CAND_FIELDS = [
    "rank", "candidate_keyword", "norm_stem",
    "monthly_pc", "monthly_mobile", "monthly_total",
    "comp_idx", "depth", "source_seed", "is_new", "note",
]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

L1_META = {
    "01": {"name": "엔터·미디어", "prefix": "l1_01_entertainment"},
    "02": {"name": "금융·재테크", "prefix": "l1_02_finance"},
    "03": {"name": "건강·다이어트", "prefix": "l1_03_health"},
    "04": {"name": "패션", "prefix": "l1_04_fashion"},
    "05": {"name": "스포츠", "prefix": "l1_05_sports"},
    "06": {"name": "자동차", "prefix": "l1_06_auto"},
    "07": {"name": "IT·테크", "prefix": "l1_07_tech"},
    "08": {"name": "뷰티", "prefix": "l1_08_beauty"},
    "09": {"name": "맛집·카페", "prefix": "l1_09_restaurants"},
    "10": {"name": "요리·푸드", "prefix": "l1_10_food"},
    "11": {"name": "여행", "prefix": "l1_11_travel"},
    "12": {"name": "문화·예술", "prefix": "l1_12_culture"},
    "13": {"name": "반려동물", "prefix": "l1_13_pets"},
    "14": {"name": "아웃도어", "prefix": "l1_14_outdoor"},
    "15": {"name": "홈·리빙", "prefix": "l1_15_home"},
    "16": {"name": "육아", "prefix": "l1_16_parenting"},
    "17": {"name": "커리어", "prefix": "l1_17_career"},
    "18": {"name": "게임", "prefix": "l1_18_game"},
}

EXTRA_SEEDS_BY_L1 = {
    "18": [
        "게임", "모바일게임", "PC게임", "콘솔게임", "스팀게임",
        "온라인게임", "네이트게임", "플래시게임", "보드게임",
        "RPG", "FPS", "MMORPG", "로블록스", "마인크래프트",
        "리그오브레전드", "발로란트", "오버워치", "배틀그라운드",
    ],
}

BASE_URL = "https://api.naver.com"
URI = "/keywordstool"


def meta_for(l1_code: str) -> dict:
    return L1_META.get(l1_code, {"name": f"L1-{l1_code}", "prefix": f"l1_{l1_code}"})


def paths_for(l1_code: str) -> dict:
    p = meta_for(l1_code)["prefix"]
    return {
        "raw": OUTPUT_DIR / f"{p}_api_raw.csv",
        "state": OUTPUT_DIR / f"{p}_collect_state.json",
        "candidates": OUTPUT_DIR / f"{p}_l2_candidates.csv",
    }


# ─── API ──────────────────────────────────────────────────────────────────────
def make_signature(timestamp: str, method: str, uri: str, secret_key: str) -> str:
    message = f"{timestamp}.{method}.{uri}"
    digest = hmac.new(
        secret_key.encode("utf-8"), message.encode("utf-8"), hashlib.sha256
    ).digest()
    return base64.b64encode(digest).decode("utf-8")


def get_headers(method: str, uri: str) -> dict:
    ts = str(int(time.time() * 1000))
    return {
        "Content-Type": "application/json; charset=UTF-8",
        "X-Timestamp": ts,
        "X-API-KEY": NAVER_API_KEY,
        "X-Customer": str(NAVER_CUSTOMER_ID),
        "X-Signature": make_signature(ts, method, uri, NAVER_SECRET_KEY),
    }


def parse_count(val) -> int:
    if val is None:
        return 0
    s = str(val).strip()
    if s.startswith("<"):
        return 5
    try:
        return int(float(s))
    except (ValueError, TypeError):
        return 0


def sanitize_keyword(kw: str) -> str:
    kw = kw.replace(" ", "")
    kw = re.sub(r"[·∙•&/\\|]", "", kw)
    return kw.strip()


def call_keyword_tool(hint_keywords: list[str]) -> list[dict]:
    cleaned = [sanitize_keyword(kw) for kw in hint_keywords]
    cleaned = [kw for kw in cleaned if kw]
    if not cleaned:
        return []
    params = {"hintKeywords": ",".join(cleaned), "showDetail": "1"}

    for attempt in range(1, MAX_RETRIES + 1):
        headers = get_headers("GET", URI)
        try:
            resp = requests.get(
                BASE_URL + URI, headers=headers, params=params, timeout=15
            )
        except requests.RequestException as e:
            log.warning("요청 실패 (attempt %d/%d): %s", attempt, MAX_RETRIES, e)
            time.sleep(2 ** attempt)
            continue

        if resp.status_code == 429:
            wait = 2 ** attempt
            log.warning("429 — %d초 대기 (attempt %d/%d)", wait, attempt, MAX_RETRIES)
            time.sleep(wait)
            continue

        if resp.status_code != 200:
            log.error("API 오류 %d: %s", resp.status_code, resp.text[:300])
            return []

        return resp.json().get("keywordList", [])

    log.error("최대 재시도 초과.")
    return []


# ─── 상태 / raw I/O ───────────────────────────────────────────────────────────
def load_state(state_path: Path) -> dict:
    if state_path.exists():
        with open(state_path, encoding="utf-8") as f:
            return json.load(f)
    return {"queried_hints": [], "api_calls": 0}


def save_state(state_path: Path, state: dict):
    with open(state_path, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def load_raw_rows(raw_path: Path) -> list[dict]:
    if not raw_path.exists():
        return []
    with open(raw_path, encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def append_raw_rows(raw_path: Path, rows: list[dict]):
    write_header = not raw_path.exists() or raw_path.stat().st_size == 0
    with open(raw_path, "a", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=RAW_FIELDS, extrasaction="ignore")
        if write_header:
            w.writeheader()
        w.writerows(rows)


def reset_outputs(paths: dict):
    for key in ("raw", "state", "candidates"):
        p = paths[key]
        if p.exists():
            p.unlink()
            log.info("삭제: %s", p)


# ─── 시드 준비 ─────────────────────────────────────────────────────────────────
def all_l1_codes() -> list[str]:
    codes = set()
    with open(DATA_DIR / "topics_l2.csv", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            codes.add(row["l1_code"])
    return sorted(codes, key=int)


def load_l2_seeds(l1_code: str) -> list[dict]:
    seeds = []
    with open(DATA_DIR / "topics_l2.csv", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            if row["l1_code"] == l1_code:
                seeds.append({"l2_id": row["l2_id"], "name_ko": row["name_ko"]})
    return seeds


def build_hop1_seeds(l1_code: str) -> list[dict]:
    """L2 name_ko + EXTRA_SEEDS → depth=1 시드."""
    l2_seeds = load_l2_seeds(l1_code)
    extra = EXTRA_SEEDS_BY_L1.get(l1_code, [])
    out = []
    seen = set()

    for s in l2_seeds:
        kw = sanitize_keyword(s["name_ko"])
        if kw and kw not in seen:
            seen.add(kw)
            out.append({"keyword": kw, "source": s["l2_id"], "parent": ""})

    for kw in extra:
        clean = sanitize_keyword(kw)
        if clean and clean not in seen:
            seen.add(clean)
            out.append({"keyword": clean, "source": "extra", "parent": ""})

    return out


def pick_hop2_seeds(
    raw_rows: list[dict],
    queried: set[str],
    hop2_min_total: int,
    hop2_max_seeds: int,
) -> list[dict]:
    """1-hop raw 결과에서 2-hop 시드 선정 (검색수 내림차순, 미조회만)."""
    best: dict[str, dict] = {}
    for row in raw_rows:
        if str(row.get("depth", "1")) != "1":
            continue
        kw = sanitize_keyword(row.get("rel_keyword", ""))
        if not kw or kw in queried:
            continue
        total = int(row.get("monthly_total") or 0)
        if total < hop2_min_total:
            continue
        if kw not in best or total > int(best[kw]["monthly_total"]):
            best[kw] = {**row, "keyword": kw, "monthly_total": total}

    ranked = sorted(best.values(), key=lambda r: r["monthly_total"], reverse=True)
    seeds = []
    for row in ranked[:hop2_max_seeds]:
        seeds.append({
            "keyword": row["keyword"],
            "source": f"hop2:{row.get('source_seed', '')}",
            "parent": row["keyword"],
        })
    return seeds


# ─── 수집 ─────────────────────────────────────────────────────────────────────
def collect_seeds(
    seeds: list[dict],
    depth: int,
    queried: set[str],
    state: dict,
    raw_path: Path,
    max_calls: int,
) -> int:
    """시드 목록을 API 호출해 raw에 append. 반환: 이번에 한 API 호출 수."""
    pending = [s for s in seeds if s["keyword"] not in queried]
    if not pending:
        log.info("depth=%d — 조회할 새 시드 없음", depth)
        return 0

    chunks = [pending[i:i + SEED_CHUNK_SIZE] for i in range(0, len(pending), SEED_CHUNK_SIZE)]
    calls_this_run = 0
    now = datetime.now(timezone.utc).isoformat()

    for idx, chunk in enumerate(chunks, 1):
        if calls_this_run >= max_calls:
            log.warning("max-calls(%d) 도달 — 수집 중단", max_calls)
            break

        kws = [s["keyword"] for s in chunk]
        sources = [s["source"] for s in chunk]
        parents = [s.get("parent", "") for s in chunk]
        log.info("[depth=%d %d/%d] 호출: %s", depth, idx, len(chunks), kws)

        items = call_keyword_tool(kws)
        batch_rows = []
        for item in items:
            pc = parse_count(item.get("monthlyPcQcCnt"))
            mob = parse_count(item.get("monthlyMobileQcCnt"))
            batch_rows.append({
                "rel_keyword": item.get("relKeyword", ""),
                "monthly_pc": pc,
                "monthly_mobile": mob,
                "monthly_total": pc + mob,
                "comp_idx": item.get("compIdx", ""),
                "depth": depth,
                "parent_keyword": "|".join(p for p in parents if p) or kws[0],
                "hint_keywords": "|".join(kws),
                "source_seed": "|".join(sources),
                "collected_at": now,
            })

        append_raw_rows(raw_path, batch_rows)
        log.info("  → %d개 연관어 저장", len(batch_rows))

        for kw in kws:
            queried.add(kw)
        state["queried_hints"] = sorted(queried)
        calls_this_run += 1
        state["api_calls"] = state.get("api_calls", 0) + 1

        if idx < len(chunks) and calls_this_run < max_calls:
            time.sleep(API_CALL_INTERVAL)

    return calls_this_run


def cmd_collect(args):
    paths = paths_for(args.l1_code)
    meta = meta_for(args.l1_code)

    if args.reset:
        reset_outputs(paths)

    state = load_state(paths["state"])
    queried = set(state.get("queried_hints", []))
    raw_before = len(load_raw_rows(paths["raw"]))

    log.info("=== collect: %s (l1=%s, depth=%d) ===", meta["name"], args.l1_code, args.depth)

    # 1-hop
    hop1 = build_hop1_seeds(args.l1_code)
    log.info("1-hop 시드 %d개 (미조회 %d개)", len(hop1), sum(1 for s in hop1 if s["keyword"] not in queried))
    calls = collect_seeds(hop1, 1, queried, state, paths["raw"], args.max_calls)
    save_state(paths["state"], state)

    # 2-hop
    if args.depth >= 2 and calls < args.max_calls:
        raw_rows = load_raw_rows(paths["raw"])
        hop2 = pick_hop2_seeds(raw_rows, queried, args.hop2_min_total, args.hop2_max_seeds)
        remaining = args.max_calls - calls
        log.info(
            "2-hop 시드 %d개 선정 (min_total=%d, 미조회 %d개, 남은 호출 %d)",
            len(hop2), args.hop2_min_total,
            sum(1 for s in hop2 if s["keyword"] not in queried),
            remaining,
        )
        if hop2 and remaining > 0:
            calls += collect_seeds(hop2, 2, queried, state, paths["raw"], remaining)
            save_state(paths["state"], state)

    raw_after = len(load_raw_rows(paths["raw"]))
    unique_kw = len({sanitize_keyword(r["rel_keyword"]) for r in load_raw_rows(paths["raw"]) if r.get("rel_keyword")})

    print()
    print("=" * 60)
    print(f"[collect 완료] {meta['name']}")
    print("=" * 60)
    print(f"  이번 API 호출:     {calls}회 (누적 {state.get('api_calls', 0)}회)")
    print(f"  raw 행:            {raw_before} → {raw_after} (+{raw_after - raw_before})")
    print(f"  raw 유니크 키워드: {unique_kw}개")
    print(f"  조회한 시드:       {len(queried)}개")
    print(f"  raw 파일:          {paths['raw']}")
    print("=" * 60)
    print("다음: python3 scripts/expand_l2_naver.py dedup --l1-code", args.l1_code)


# ─── dedup ────────────────────────────────────────────────────────────────────
def load_existing_norm_registry() -> set[str]:
    registry = set()
    with open(DATA_DIR / "topics_l3_slot2step.csv", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            fk = row.get("focus_keyword", "").strip()
            if fk:
                registry.add(norm(fk))
    return registry


def load_existing_l2_norms(l1_code: str) -> set[str]:
    norms = set()
    with open(DATA_DIR / "topics_l2.csv", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            if row["l1_code"] == l1_code:
                norms.add(norm(row["name_ko"]))
    return norms


def dedup_raw(raw_rows: list[dict], l1_code: str) -> tuple[list[dict], dict]:
    l3_norms = load_existing_norm_registry()
    l2_norms = load_existing_l2_norms(l1_code)
    stats = {"dup_api": 0, "dup_l2": 0, "dup_l3": 0, "empty": 0}

    seen: dict[str, dict] = {}
    for row in raw_rows:
        kw = sanitize_keyword(row.get("rel_keyword", ""))
        if not kw:
            stats["empty"] += 1
            continue
        n = norm(kw)
        total = int(row.get("monthly_total") or 0)

        if n in l2_norms:
            stats["dup_l2"] += 1
            continue
        if n in l3_norms:
            stats["dup_l3"] += 1
            continue

        entry = {
            "candidate_keyword": kw,
            "norm_stem": n,
            "monthly_pc": int(row.get("monthly_pc") or 0),
            "monthly_mobile": int(row.get("monthly_mobile") or 0),
            "monthly_total": total,
            "comp_idx": row.get("comp_idx", ""),
            "depth": row.get("depth", ""),
            "source_seed": row.get("source_seed", ""),
        }
        if n in seen:
            stats["dup_api"] += 1
            if total > seen[n]["monthly_total"]:
                seen[n] = entry
        else:
            seen[n] = entry

    candidates = sorted(seen.values(), key=lambda r: r["monthly_total"], reverse=True)
    return candidates, stats


def cmd_dedup(args, quiet: bool = False) -> dict:
    paths = paths_for(args.l1_code)
    meta = meta_for(args.l1_code)
    raw_rows = load_raw_rows(paths["raw"])

    if not raw_rows:
        log.error("raw 파일 없음: %s — 먼저 collect를 실행하세요.", paths["raw"])
        sys.exit(1)

    log.info("=== dedup: %s (raw %d행) ===", meta["name"], len(raw_rows))
    candidates, stats = dedup_raw(raw_rows, args.l1_code)
    l2_count = len(load_l2_seeds(args.l1_code))
    thresholds = [500, 1_000, 5_000, 10_000]

    with open(paths["candidates"], "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=CAND_FIELDS, extrasaction="ignore")
        w.writeheader()
        for i, row in enumerate(candidates, 1):
            note = ""
            if row["monthly_total"] < args.threshold:
                note = f"검색수 임계값({args.threshold}) 미달"
            w.writerow({
                "rank": i,
                "is_new": "Y",
                "note": note,
                **row,
            })

    best = sum(1 for r in candidates if r["monthly_total"] >= args.threshold)
    result = {
        "l1_code": args.l1_code,
        "name": meta["name"],
        "l2_count": l2_count,
        "raw_rows": len(raw_rows),
        "candidates": len(candidates),
        "cand_threshold": best,
        "stats": stats,
    }

    if quiet:
        return result

    depth_counts = {}
    for c in candidates:
        d = str(c.get("depth", "?"))
        depth_counts[d] = depth_counts.get(d, 0) + 1

    print()
    print("=" * 60)
    print(f"[dedup 완료] {meta['name']} 분야 L2 확장 실험")
    print("=" * 60)
    print(f"  raw 입력:             {len(raw_rows):,}행")
    print(f"    └ API 내 중복:      {stats['dup_api']:,}개 병합")
    print(f"    └ 기존 L2 충돌:     {stats['dup_l2']:,}개 제외")
    print(f"    └ 기존 L3 충돌:     {stats['dup_l3']:,}개 제외")
    print(f"  최종 고유 후보:       {len(candidates):,}개")
    for d, cnt in sorted(depth_counts.items()):
        print(f"    └ depth={d}:         {cnt:,}개")
    print()
    print("  ── 검색수 임계값별 L2 후보 ──")
    print(f"  {'임계값':>10}   {'후보 수':>8}")
    for thr in thresholds:
        cnt = sum(1 for r in candidates if r["monthly_total"] >= thr)
        marker = " ◀ 기본값" if thr == args.threshold else ""
        print(f"  {thr:>10,}   {cnt:>8,}{marker}")
    print()
    print(f"  ▶ 결론: 현재 L2 {l2_count}개 → 검색수 {args.threshold:,}+ 기준 약 {best:,}개 추가 여력")
    print(f"  후보 파일: {paths['candidates']}")
    print("=" * 60)
    return result


def cmd_all(args):
    cmd_collect(args)
    cmd_dedup(args)


def cmd_batch(args):
    codes = all_l1_codes()
    if args.only:
        codes = [c for c in codes if c in args.only.split(",")]
    results = []
    started = time.time()

    print("=" * 70)
    print(f"18분야 일괄 L2 확장 실험 시작 — {len(codes)}개 분야, depth={args.depth}")
    print("=" * 70)

    for i, code in enumerate(codes, 1):
        meta = meta_for(code)
        print(f"\n>>> [{i}/{len(codes)}] L1={code} {meta['name']}")
        field_args = argparse.Namespace(**vars(args))
        field_args.l1_code = code
        field_args.reset = args.reset
        cmd_collect(field_args)
        results.append(cmd_dedup(field_args, quiet=True))

    elapsed = time.time() - started
    summary_path = OUTPUT_DIR / "l2_expansion_summary.csv"
    with open(summary_path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=[
            "l1_code", "name", "l2_count", "raw_rows", "candidates", "cand_1000plus",
        ])
        w.writeheader()
        for r in results:
            w.writerow({
                "l1_code": r["l1_code"],
                "name": r["name"],
                "l2_count": r["l2_count"],
                "raw_rows": r["raw_rows"],
                "candidates": r["candidates"],
                "cand_1000plus": r["cand_threshold"],
            })

    print()
    print("=" * 70)
    print("[전체 요약]")
    print("=" * 70)
    print(f"{'code':>4}  {'분야':<14} {'L2':>4} {'raw':>8} {'후보':>8} {'1000+':>8}")
    print("-" * 70)
    for r in results:
        print(
            f"{r['l1_code']:>4}  {r['name']:<14} {r['l2_count']:>4} "
            f"{r['raw_rows']:>8,} {r['candidates']:>8,} {r['cand_threshold']:>8,}"
        )
    print("-" * 70)
    print(
        f"{'합계':>4}  {'':14} {sum(r['l2_count'] for r in results):>4} "
        f"{sum(r['raw_rows'] for r in results):>8,} "
        f"{sum(r['candidates'] for r in results):>8,} "
        f"{sum(r['cand_threshold'] for r in results):>8,}"
    )
    print(f"\n소요 시간: {elapsed/60:.1f}분 ({elapsed:.0f}초)")
    print(f"요약 CSV: {summary_path}")
    print("=" * 70)


# ─── CLI ──────────────────────────────────────────────────────────────────────
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="네이버 검색광고 API L2 확장 실험")
    sub = p.add_subparsers(dest="command", required=True)

    def add_common(sp):
        sp.add_argument("--l1-code", default="18", help="L1 코드 (기본: 18=게임)")
        sp.add_argument("--threshold", type=int, default=MONTHLY_TOTAL_THRESHOLD,
                        help="dedup 리포트/ note용 검색수 임계값")

    sp_collect = sub.add_parser("collect", help="넓게 API 수집 → raw 누적")
    add_common(sp_collect)
    sp_collect.add_argument("--depth", type=int, default=2, choices=[1, 2],
                            help="수집 깊이 (1=시드만, 2=1-hop 결과로 2-hop)")
    sp_collect.add_argument("--hop2-min-total", type=int, default=HOP2_MIN_TOTAL,
                            help="2-hop 시드 최소 월 검색수")
    sp_collect.add_argument("--hop2-max-seeds", type=int, default=HOP2_MAX_SEEDS,
                            help="2-hop 시드 최대 개수")
    sp_collect.add_argument("--max-calls", type=int, default=DEFAULT_MAX_CALLS,
                            help="이번 collect 실행 API 호출 상한")
    sp_collect.add_argument("--reset", action="store_true",
                            help="raw/state/candidates 초기화 후 새로 수집")

    sp_dedup = sub.add_parser("dedup", help="raw → 중복 제거 → 후보 CSV")
    add_common(sp_dedup)

    sp_all = sub.add_parser("all", help="collect + dedup 한 번에")
    add_common(sp_all)
    sp_all.add_argument("--depth", type=int, default=2, choices=[1, 2])
    sp_all.add_argument("--hop2-min-total", type=int, default=HOP2_MIN_TOTAL)
    sp_all.add_argument("--hop2-max-seeds", type=int, default=HOP2_MAX_SEEDS)
    sp_all.add_argument("--max-calls", type=int, default=DEFAULT_MAX_CALLS)
    sp_all.add_argument("--reset", action="store_true")

    sp_batch = sub.add_parser("batch", help="18분야 일괄 collect + dedup")
    add_common(sp_batch)
    sp_batch.add_argument("--depth", type=int, default=2, choices=[1, 2])
    sp_batch.add_argument("--hop2-min-total", type=int, default=HOP2_MIN_TOTAL)
    sp_batch.add_argument("--hop2-max-seeds", type=int, default=HOP2_MAX_SEEDS)
    sp_batch.add_argument("--max-calls", type=int, default=DEFAULT_MAX_CALLS)
    sp_batch.add_argument("--reset", action="store_true",
                          help="분야마다 raw/state/candidates 초기화")
    sp_batch.add_argument("--only", default="",
                          help="특정 L1만 (쉼표 구분). 예: 01,09,18")

    return p


if __name__ == "__main__":
    parser = build_parser()
    args = parser.parse_args()
    if args.command == "collect":
        cmd_collect(args)
    elif args.command == "dedup":
        cmd_dedup(args)
    elif args.command == "all":
        cmd_all(args)
    elif args.command == "batch":
        cmd_batch(args)
