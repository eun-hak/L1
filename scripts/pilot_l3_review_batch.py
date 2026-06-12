#!/usr/bin/env python3
"""
L3 리뷰 파일럿 / 본생산 — slot_2step 생성.

Usage:
  python scripts/pilot_l3_review_batch.py
  python scripts/pilot_l3_review_batch.py --resume
  python scripts/pilot_l3_review_batch.py --l2 11-travel-essentials --count 15
  python scripts/pilot_l3_review_batch.py --missing-l2 --merge-topics --resume \\
    --max-scout 1500 --max-gemini 500
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))

import test_l3_diversity as td  # noqa: E402
from groq_client import (  # noqa: E402
    MODEL_FAST as SCOUT_MODEL,
    RateLimitExhausted,
    _keyword_seen,
    get_groq_session_report,
    parse_rate_limit_error,
    reset_groq_session_stats,
)
from gemini_client import MODEL_FAST as GEMINI_MODEL, _is_rate_or_quota_error  # noqa: E402
from taxonomy_state import (  # noqa: E402
    L3_FIELDNAMES,
    connect,
    export_artifacts,
    upsert_topics,
)

OUT_DIR = ROOT / "data2" / "test" / "l3_review_pilot"
CHECKPOINT = OUT_DIR / "checkpoint.json"
PROD_CHECKPOINT = ROOT / "data2" / "state" / "l3_slot2step_prod_checkpoint.json"
TOPUP_CHECKPOINT = ROOT / "data2" / "state" / "l3_slot2step_topup_checkpoint.json"
REVIEW_CSV = OUT_DIR / "l3_review_pilot.csv"
REPORT_JSON = OUT_DIR / "l3_review_pilot_report.json"
PHASE1_PLAN = ROOT / "data2" / "pilot" / "phase1_l3_l4_plan.csv"
BRIEF = ROOT / "data2" / "brief.txt"
L2_CSV = ROOT / "data2" / "topics_l2.csv"
LEGACY_L3 = ROOT / "data2" / "topics_l3.csv"  # Nemotron/Scout 구본 (건드리지 않음)
SLOT2STEP_CSV = ROOT / "data2" / "topics_l3_slot2step.csv"
SLOT2STEP_JSON = ROOT / "data2" / "topics_l3_slot2step.json"
SLOT2STEP_MANIFEST = ROOT / "data2" / "manifest_l3_slot2step.json"
STATE_DB_SLOT2STEP = ROOT / "data2" / "state" / "taxonomy_slot2step.db"
SLOT2STEP_MODEL = "slot_2step"

# 타입별 대표 L2 20개 (Phase1 + 신규 geo/howto)
DEFAULT_L2 = [
    "11-travel-essentials",
    "11-accommodation-recommendations",
    "11-domestic-travel",
    "11-overseas-travel",
    "11-travel-tips",
    "11-family-trip",
    "09-seoul-food",
    "09-busan-food",
    "09-gangnam-food",
    "09-cafe-recommend",
    "09-solo-food",
    "09-date-course",
    "10-korean-recipe",
    "10-air-fryer-recipes",
    "10-cooking-tips",
    "08-skincare-routine",
    "08-skin-type-care",
    "13-cat-food",
    "13-dog-health",
    "13-pet-hospital",
]

DURATION_PATTERN = re.compile(r"(단기|장기|여름|겨울|봄|가을|국내|해외)")


class ApiBudget:
    def __init__(self, *, max_scout: int | None, max_gemini: int | None) -> None:
        self.scout = 0
        self.gemini = 0
        self.max_scout = max_scout
        self.max_gemini = max_gemini
        self.stop_reason = ""

    def require_scout(self) -> None:
        if self.max_scout is not None and self.scout >= self.max_scout:
            self.stop_reason = f"scout 한도 {self.max_scout} 도달"
            raise BudgetExhausted(self.stop_reason)

    def require_gemini(self) -> None:
        if self.max_gemini is not None and self.gemini >= self.max_gemini:
            self.stop_reason = f"gemini 한도 {self.max_gemini} 도달"
            raise BudgetExhausted(self.stop_reason)

    def add_scout(self, n: int = 1) -> None:
        self.scout += n
        if self.max_scout is not None and self.scout >= self.max_scout:
            self.stop_reason = f"scout 한도 {self.max_scout} 도달"

    def add_gemini(self, n: int = 1) -> None:
        self.gemini += n
        if self.max_gemini is not None and self.gemini >= self.max_gemini:
            self.stop_reason = f"gemini 한도 {self.max_gemini} 도달"

    @property
    def exhausted(self) -> bool:
        scout_hit = self.max_scout is not None and self.scout >= self.max_scout
        gemini_hit = self.max_gemini is not None and self.gemini >= self.max_gemini
        return scout_hit and gemini_hit


class BudgetExhausted(RuntimeError):
    pass


def _gemini_blocked(exc: Exception) -> bool:
    msg = str(exc).lower()
    return "403" in msg or "blocked" in msg or "permission_denied" in msg


def gemini_dedup_safe(slots: list[dict[str, str]], l2_name: str) -> tuple[list[dict[str, str]], bool]:
    try:
        return td.gemini_dedup_slots(slots, l2_name), True
    except Exception as exc:
        if _is_rate_or_quota_error(exc) or _gemini_blocked(exc):
            print(f"  [warn] Gemini dedup skip: {exc}", flush=True)
            return slots, False
        raise


def load_phase1_meta() -> dict[str, dict[str, str]]:
    meta: dict[str, dict[str, str]] = {}
    if not PHASE1_PLAN.exists():
        return meta
    with PHASE1_PLAN.open(encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            meta[row["l2_id"]] = {
                "l2_type": row.get("l2_type", ""),
                "l3_slot_axes": row.get("l3_slot_axes", ""),
                "l4_engine": row.get("l4_engine", ""),
            }
    return meta


def load_l2_map() -> dict[str, dict[str, str]]:
    out: dict[str, dict[str, str]] = {}
    with L2_CSV.open(encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            out[row["l2_id"]] = row
    return out


def load_missing_l2_ids() -> list[str]:
    """slot_2step 미생성 L2 — 구 topics_l3(Nemotron) 있는 L2는 제외."""
    l2_map = load_l2_map()
    has_slot2step: set[str] = set()
    if SLOT2STEP_CSV.exists():
        with SLOT2STEP_CSV.open(encoding="utf-8-sig") as f:
            for row in csv.DictReader(f):
                has_slot2step.add(row["l2_id"])
    has_legacy: set[str] = set()
    if LEGACY_L3.exists():
        with LEGACY_L3.open(encoding="utf-8-sig") as f:
            for row in csv.DictReader(f):
                has_legacy.add(row["l2_id"])
    missing = [
        lid for lid in l2_map
        if lid not in has_slot2step and lid not in has_legacy
    ]
    missing.sort(key=lambda x: (l2_map[x].get("l1_code", ""), int(l2_map[x].get("sort_order") or 0)))
    return missing


def load_l2_l3_counts() -> dict[str, int]:
    counts: dict[str, int] = {}
    if not SLOT2STEP_CSV.exists():
        return counts
    with SLOT2STEP_CSV.open(encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            lid = row.get("l2_id", "")
            if lid:
                counts[lid] = counts.get(lid, 0) + 1
    return counts


def load_existing_keywords(l2_id: str) -> list[str]:
    if not SLOT2STEP_CSV.exists():
        return []
    out: list[str] = []
    with SLOT2STEP_CSV.open(encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            if row.get("l2_id") == l2_id:
                kw = row.get("focus_keyword", "").strip()
                if kw:
                    out.append(kw)
    return out


def load_topup_l2_ids(*, cap: int) -> list[tuple[str, int]]:
    """L2별 cap 미만 — (l2_id, 부족 개수) 목록."""
    counts = load_l2_l3_counts()
    l2_map = load_l2_map()
    topup: list[tuple[str, int]] = []
    for l2_id, n in counts.items():
        if n < cap:
            topup.append((l2_id, cap - n))
    topup.sort(key=lambda x: (l2_map.get(x[0], {}).get("l1_code", ""), -x[1], x[0]))
    return topup


def _unique_slug(slug: str, seen: set[str]) -> str:
    if slug not in seen:
        return slug
    base = slug
    n = 2
    while slug in seen:
        slug = f"{base}-{n}"
        n += 1
    return slug


def result_to_l3_rows(result: dict, l2_row: dict[str, str], *, seen_l3_ids: set[str]) -> list[dict[str, str]]:
    now = datetime.now(timezone.utc).isoformat()
    l2_id = result["l2_id"]
    l1_code = l2_row.get("l1_code", "")
    rows: list[dict[str, str]] = []
    local_slugs: set[str] = set()
    for item in result["items"]:
        slug = (item.get("slug") or td.slugify_en(item["focus_keyword"])).strip() or "topic"
        slug = _unique_slug(slug, local_slugs)
        local_slugs.add(slug)
        l3_id = f"{l2_id}-{slug}"
        if l3_id in seen_l3_ids:
            slug = _unique_slug(f"{slug}-2", local_slugs)
            local_slugs.add(slug)
            l3_id = f"{l2_id}-{slug}"
        seen_l3_ids.add(l3_id)
        rows.append({
            "l3_id": l3_id,
            "l2_id": l2_id,
            "l1_code": l1_code,
            "l3_slug": slug,
            "focus_keyword": item["focus_keyword"],
            "title_ko": item.get("title_ko", ""),
            "search_intent": item.get("search_intent", "info"),
            "topic_angle": item.get("topic_angle", "tip"),
            "description": item.get("description", ""),
            "model": SLOT2STEP_MODEL,
            "generated_at": now,
        })
    return rows


def merge_results_to_slot2step(results: list[dict], l2_map: dict[str, dict[str, str]]) -> int:
    existing: list[dict[str, str]] = []
    seen_l3_ids: set[str] = set()
    if SLOT2STEP_CSV.exists():
        with SLOT2STEP_CSV.open(encoding="utf-8-sig") as f:
            for row in csv.DictReader(f):
                existing.append({k: row.get(k, "") for k in L3_FIELDNAMES})
                seen_l3_ids.add(row.get("l3_id", ""))

    existing_per_l2 = Counter(r["l2_id"] for r in existing)
    L2_CAP = 15

    new_rows: list[dict[str, str]] = []
    for result in results:
        l2_row = l2_map.get(result["l2_id"])
        if not l2_row:
            continue
        l2_id = result["l2_id"]
        room = max(0, L2_CAP - existing_per_l2.get(l2_id, 0))
        if room == 0:
            continue
        trimmed = {**result, "items": result["items"][:room]}
        batch_rows = result_to_l3_rows(trimmed, l2_row, seen_l3_ids=seen_l3_ids)
        new_rows.extend(batch_rows)
        existing_per_l2[l2_id] += len(batch_rows)

    if not new_rows:
        return 0

    by_id = {r["l3_id"]: r for r in existing}
    for row in new_rows:
        by_id[row["l3_id"]] = row
    merged = sorted(by_id.values(), key=lambda r: (r["l2_id"], r["l3_id"]))

    SLOT2STEP_CSV.parent.mkdir(parents=True, exist_ok=True)
    with SLOT2STEP_CSV.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=L3_FIELDNAMES)
        w.writeheader()
        w.writerows(merged)

    SLOT2STEP_JSON.write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")

    with connect(STATE_DB_SLOT2STEP) as conn:
        upsert_topics(conn, merged)
        export_artifacts(
            conn,
            csv_path=SLOT2STEP_CSV,
            json_path=SLOT2STEP_JSON,
            manifest_path=SLOT2STEP_MANIFEST,
            l2_count=len(l2_map),
            count_target=15,
            model=SLOT2STEP_MODEL,
            l2_processed=len({r["l2_id"] for r in merged}),
            interrupted=False,
            state_db=str(STATE_DB_SLOT2STEP.relative_to(ROOT)),
        )

    return len(new_rows)


def auto_flags(keyword: str, stem: str, l2_stems: list[str]) -> str:
    flags: list[str] = []
    if keyword.endswith("추천"):
        flags.append("ends_추천")
    if "체크리스트" in keyword or keyword.endswith("준비물"):
        flags.append("prep_checklist")
    if DURATION_PATTERN.search(keyword):
        flags.append("duration_variant")
    if stem in l2_stems:
        flags.append("stem_dup_in_batch")
    if keyword.startswith("여행용 휴대용"):
        flags.append("travel_gadget_template")
    return "|".join(flags) if flags else ""


def generate_l2_batch(
    l2_id: str,
    *,
    count: int,
    brief: str,
    phase1_meta: dict[str, dict[str, str]],
    budget: ApiBudget | None = None,
    existing_keywords: list[str] | None = None,
) -> dict:
    l2 = td.load_l2(l2_id)
    l1 = td.load_l1(l2["l1_code"])
    l1_name = l1.get("name_ko", l2["l1_code"])
    l1_desc = l1.get("description", "")
    l2_name = l2["name_ko"]
    l2_desc = l2.get("description", "")
    p1 = phase1_meta.get(l2_id, {})
    l2_type = p1.get("l2_type") or l2.get("l2_type", "")

    scout_calls = 0
    gemini_calls = 0
    target = count + 5
    avoid: list[str] = list(existing_keywords or [])
    existing_stems = {td.stem_keyword(k) for k in avoid}
    accepted: list[dict[str, str]] = []
    rejected: list[str] = []

    if budget:
        budget.require_scout()
    slots = td.generate_slots(
        l1_name=l1_name,
        l1_desc=l1_desc,
        l2_name=l2_name,
        l2_desc=l2_desc,
        count=target,
        brief=brief,
        avoid_keywords=avoid or None,
    )
    scout_calls += 1
    if budget:
        budget.add_scout()
    time.sleep(1.0)

    try:
        if budget:
            budget.require_gemini()
        slots, gemini_used = gemini_dedup_safe(slots[:target], l2_name)
        if gemini_used:
            gemini_calls += 1
            if budget:
                budget.add_gemini()
            time.sleep(1.0)
    except (BudgetExhausted, RateLimitExhausted):
        raise

    max_rounds = 4

    for _ in range(max_rounds):
        if len(accepted) >= count:
            break
        need = count - len(accepted)
        batch_slots = slots[len(accepted) : len(accepted) + need + 2]
        if not batch_slots:
            if budget:
                budget.require_scout()
            extra = td.generate_slots(
                l1_name=l1_name,
                l1_desc=l1_desc,
                l2_name=l2_name,
                l2_desc=l2_desc,
                count=need + 3,
                brief=brief,
                avoid_keywords=avoid or None,
            )
            scout_calls += 1
            if budget:
                budget.add_scout()
            time.sleep(1.0)
            try:
                if budget:
                    budget.require_gemini()
                extra, gemini_used = gemini_dedup_safe(extra, l2_name)
                if gemini_used:
                    gemini_calls += 1
                    if budget:
                        budget.add_gemini()
                    time.sleep(1.0)
            except (BudgetExhausted, RateLimitExhausted):
                raise
            slots.extend(extra)
            batch_slots = slots[len(accepted) : len(accepted) + need + 2]
            if not batch_slots:
                break

        if budget:
            budget.require_scout()
        raw = td.generate_diverse_l3(
            l1_name=l1_name,
            l1_desc=l1_desc,
            l2_name=l2_name,
            l2_desc=l2_desc,
            count=need + 2,
            brief=brief,
            slots=batch_slots,
            avoid_keywords=avoid or None,
        )
        scout_calls += 1
        if budget:
            budget.add_scout()
        time.sleep(1.0)

        new_accepted, new_rejected = td.filter_diverse(raw)
        for item in new_accepted:
            if len(accepted) >= count:
                break
            kw = item["focus_keyword"]
            stem = td.stem_keyword(kw)
            if stem in existing_stems or _keyword_seen(kw, avoid):
                new_rejected.append(f"{kw} (dup_existing)")
                continue
            if any(td.stem_keyword(a["focus_keyword"]) == stem for a in accepted):
                new_rejected.append(f"{kw} (stem_dup_cross_batch)")
                continue
            existing_stems.add(stem)
            avoid.append(kw)
            accepted.append(item)
        rejected.extend(new_rejected)

    items = accepted[:count]
    stems = [td.stem_keyword(x["focus_keyword"]) for x in items]
    metrics = td.analyze_keywords([x["focus_keyword"] for x in items])

    return {
        "l2_id": l2_id,
        "l2_name": l2_name,
        "l2_type": l2_type,
        "l3_slot_axes": p1.get("l3_slot_axes", ""),
        "l4_engine": p1.get("l4_engine", ""),
        "target_count": count,
        "accepted_count": len(items),
        "scout_calls": scout_calls,
        "gemini_calls": gemini_calls,
        "metrics": metrics,
        "rejected": rejected,
        "items": items,
        "stems": stems,
    }


def write_review_csv(rows: list[dict]) -> None:
    fields = [
        "l2_id", "l2_name", "l2_type", "l4_engine",
        "focus_keyword", "title_ko", "slug",
        "search_intent", "topic_angle", "description",
        "stem", "auto_flag",
        "your_verdict", "merge_group", "note",
    ]
    with REVIEW_CSV.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def build_review_rows(results: list[dict]) -> list[dict]:
    rows: list[dict] = []
    for r in results:
        l2_stems: list[str] = []
        for item in r["items"]:
            kw = item["focus_keyword"]
            stem = td.stem_keyword(kw)
            flag = auto_flags(kw, stem, l2_stems)
            if stem not in l2_stems:
                l2_stems.append(stem)
            rows.append({
                "l2_id": r["l2_id"],
                "l2_name": r["l2_name"],
                "l2_type": r.get("l2_type", ""),
                "l4_engine": r.get("l4_engine", ""),
                "focus_keyword": kw,
                "title_ko": item.get("title_ko", ""),
                "slug": item.get("slug", ""),
                "search_intent": item.get("search_intent", ""),
                "topic_angle": item.get("topic_angle", ""),
                "description": item.get("description", ""),
                "stem": stem,
                "auto_flag": flag,
                "your_verdict": "",
                "merge_group": "",
                "note": "",
            })
    return rows


def _groq_stop_extra(exc: Exception | None = None) -> dict:
    extra: dict = {"groq_session_report": get_groq_session_report()}
    if isinstance(exc, RateLimitExhausted):
        extra["rate_limit_type"] = (
            exc.rate_limit_info.limit_type if exc.rate_limit_info else "UNKNOWN"
        )
        extra["rate_limit_message"] = (
            exc.rate_limit_info.message if exc.rate_limit_info else str(exc)
        )
    elif exc is not None and _is_rate_or_quota_error(exc):
        info = parse_rate_limit_error(exc)
        extra["rate_limit_type"] = info.limit_type
        extra["rate_limit_message"] = info.message
    return extra


def _print_groq_stop(exc: Exception | None, stop_reason: str) -> None:
    print(f"\n[stop] {stop_reason}")
    report = get_groq_session_report()
    print(f"  활성 Groq 키: {', '.join(report.get('active_keys', []))}")
    for row in report.get("per_key", []):
        hits = row.get("rate_limit_hits") or {}
        hit_str = ", ".join(f"{k}×{v}" for k, v in hits.items()) if hits else "-"
        print(
            f"  {row['key']}: 성공 {row['success_calls']}회 | 429 [{hit_str}] | "
            f"{'소진' if row['exhausted'] else 'OK'}"
        )
        if row.get("exhausted_reason"):
            print(f"    → {row['exhausted_reason']}")


def _save_checkpoint(path: Path, results: list[dict], *, extra: dict | None = None) -> None:
    payload: dict = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "scout_model": SCOUT_MODEL,
        "gemini_model": GEMINI_MODEL,
        "results": results,
        "groq_session_report": get_groq_session_report(),
    }
    if extra:
        payload.update(extra)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_report(
    path: Path,
    results: list[dict],
    *,
    target: int,
    scout_calls: int,
    gemini_calls: int,
    output_csv: str,
    stop_reason: str = "",
) -> None:
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "l2_count": len(results),
        "target_per_l2": target,
        "total_keywords": sum(r["accepted_count"] for r in results),
        "scout_calls": scout_calls,
        "gemini_calls": gemini_calls,
        "scout_model": SCOUT_MODEL,
        "gemini_model": GEMINI_MODEL,
        "output_csv": output_csv,
        "stop_reason": stop_reason,
        "groq_session_report": get_groq_session_report(),
        "by_l2": [
            {
                "l2_id": r["l2_id"],
                "l2_name": r["l2_name"],
                "l2_type": r.get("l2_type", ""),
                "accepted": r["accepted_count"],
                "stem_unique_pct": r["metrics"].get("stem_unique_pct"),
                "ends_with_추천": r["metrics"].get("ends_with_추천"),
                "scout_calls": r["scout_calls"],
                "gemini_calls": r["gemini_calls"],
            }
            for r in results
        ],
    }
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="L3 review pilot / slot_2step production")
    parser.add_argument("--l2", action="append", default=None)
    parser.add_argument("--missing-l2", action="store_true", help="topics_l3에 없는 L2 전체")
    parser.add_argument(
        "--topup-under",
        type=int,
        default=None,
        metavar="N",
        help="slot2step L2 중 L3가 N개 미만인 것만 부족분 채우기 (429까지)",
    )
    parser.add_argument("--count", type=int, default=15)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--merge-topics", action="store_true", help="topics_l3_slot2step.csv에 병합")
    parser.add_argument("--max-scout", type=int, default=None, help="Scout 호출 상한 (429 전 중단)")
    parser.add_argument("--max-gemini", type=int, default=None, help="Gemini 호출 상한")
    parser.add_argument(
        "--groq-keys",
        default=None,
        help="Groq 키 번호만 사용 (예: 3,4 → GROK_API_KEY_3·_4). 기본: GROQ_USE_KEYS env",
    )
    args = parser.parse_args()

    if args.groq_keys:
        os.environ["GROQ_USE_KEYS"] = args.groq_keys

    prod_mode = args.missing_l2 or args.merge_topics or args.topup_under is not None
    topup_needs: dict[str, int] = {}
    if args.topup_under is not None:
        topup_list = load_topup_l2_ids(cap=args.topup_under)
        topup_needs = {lid: need for lid, need in topup_list}
        l2_ids = [lid for lid, _ in topup_list]
    elif args.missing_l2:
        l2_ids = load_missing_l2_ids()
    else:
        l2_ids = args.l2 or DEFAULT_L2

    if args.topup_under is not None:
        checkpoint_path = TOPUP_CHECKPOINT
    elif prod_mode:
        checkpoint_path = PROD_CHECKPOINT
    else:
        checkpoint_path = CHECKPOINT
    report_path = ROOT / "data2" / "reports" / "l3_slot2step_prod_report.json" if prod_mode else REPORT_JSON

    brief = BRIEF.read_text(encoding="utf-8").strip() if BRIEF.exists() else ""
    phase1_meta = load_phase1_meta()
    l2_map = load_l2_map()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    budget = ApiBudget(max_scout=args.max_scout, max_gemini=args.max_gemini)

    if not args.resume:
        reset_groq_session_stats()

    done: dict[str, dict] = {}
    if args.resume and checkpoint_path.exists():
        data = json.loads(checkpoint_path.read_text(encoding="utf-8"))
        for r in data.get("results", []):
            if args.topup_under is not None and int(r.get("accepted_count") or 0) == 0:
                continue
            done[r["l2_id"]] = r
        prev_scout = int(data.get("session_scout", 0))
        prev_gemini = int(data.get("session_gemini", 0))
        print(
            f"resume: {len(done)} L2 완료 로드 "
            f"(이전 세션 Scout {prev_scout}, Gemini {prev_gemini} — 오늘 한도는 0부터)"
        )

    results: list[dict] = list(done.values())
    stop_reason = ""

    if args.topup_under is not None:
        mode_label = f"topup (L3<{args.topup_under})"
        need_sum = sum(topup_needs.values())
        print(f"L3 slot_2step {mode_label} - L2 {len(l2_ids)}개, 부족 L3 합 {need_sum}")
    elif args.missing_l2:
        mode_label = "본생산(missing L2)"
        print(f"L3 slot_2step {mode_label} - L2 {len(l2_ids)} x {args.count}")
    else:
        mode_label = "리뷰 파일럿"
        print(f"L3 slot_2step {mode_label} - L2 {len(l2_ids)} x {args.count}")
    groq_report = get_groq_session_report()
    print(f"  Groq 키: {', '.join(groq_report.get('active_keys', []))}")
    if args.max_scout or args.max_gemini:
        print(f"  한도: Scout ≤{args.max_scout}, Gemini ≤{args.max_gemini}")
    print()

    for i, l2_id in enumerate(l2_ids, 1):
        if l2_id in done:
            print(f"[{i}/{len(l2_ids)}] {l2_id} - checkpoint skip")
            continue

        scout_blocked = args.max_scout is not None and budget.scout >= args.max_scout
        gemini_blocked = args.max_gemini is not None and budget.gemini >= args.max_gemini
        if scout_blocked or gemini_blocked:
            stop_reason = budget.stop_reason or "API 일일 한도 도달"
            print(f"\n[stop] {stop_reason}")
            break

        batch_count = topup_needs.get(l2_id, args.count) if args.topup_under is not None else args.count
        existing_kw = load_existing_keywords(l2_id) if args.topup_under is not None else None
        print(f"[{i}/{len(l2_ids)}] {l2_id} (목표 +{batch_count}) ...", flush=True)
        try:
            result = generate_l2_batch(
                l2_id,
                count=batch_count,
                brief=brief,
                phase1_meta=phase1_meta,
                budget=budget,
                existing_keywords=existing_kw,
            )
        except (RateLimitExhausted, BudgetExhausted) as exc:
            stop_reason = str(exc)
            _print_groq_stop(exc if isinstance(exc, RateLimitExhausted) else None, stop_reason)
            _save_checkpoint(
                checkpoint_path,
                results,
                extra={
                    "failed_l2": l2_id,
                    "error": stop_reason,
                    "session_scout": budget.scout,
                    "session_gemini": budget.gemini,
                    **_groq_stop_extra(exc if isinstance(exc, RateLimitExhausted) else None),
                },
            )
            break
        except Exception as exc:
            if _is_rate_or_quota_error(exc):
                stop_reason = str(exc)
                _print_groq_stop(exc, stop_reason)
                _save_checkpoint(
                    checkpoint_path,
                    results,
                    extra={
                        "failed_l2": l2_id,
                        "error": stop_reason,
                        "session_scout": budget.scout,
                        "session_gemini": budget.gemini,
                        **_groq_stop_extra(exc),
                    },
                )
                break
            print(f"  ERROR: {exc}")
            _save_checkpoint(
                checkpoint_path,
                results,
                extra={"failed_l2": l2_id, "error": str(exc)},
            )
            raise

        m = result["metrics"]
        print(
            f"  수락 {result['accepted_count']}/{batch_count} | "
            f"stem유일 {m.get('stem_unique_pct')}% | 추천 {m.get('ends_with_추천')} | "
            f"Scout {result['scout_calls']} Gemini {result['gemini_calls']} | "
            f"누적 Scout {budget.scout} Gemini {budget.gemini}"
        )
        results.append(result)
        done[l2_id] = result

        if args.merge_topics:
            added = merge_results_to_slot2step([result], l2_map)
            print(f"  >> topics_l3_slot2step.csv +{added}")

        _save_checkpoint(
            checkpoint_path,
            results,
            extra={
                "session_scout": budget.scout,
                "session_gemini": budget.gemini,
            },
        )

        if budget.stop_reason and (
            (args.max_scout and budget.scout >= args.max_scout)
            or (args.max_gemini and budget.gemini >= args.max_gemini)
        ):
            stop_reason = budget.stop_reason
            print(f"\n[stop] {stop_reason}")
            break

        time.sleep(1.5)

    if not prod_mode:
        review_rows = build_review_rows(results)
        write_review_csv(review_rows)

    if args.merge_topics and results and not any(r.get("_merged") for r in results):
        pass  # already merged per L2

    report_path.parent.mkdir(parents=True, exist_ok=True)
    _write_report(
        report_path,
        results,
        target=args.count,
        scout_calls=budget.scout,
        gemini_calls=budget.gemini,
        output_csv=str(SLOT2STEP_CSV.relative_to(ROOT)) if args.merge_topics else str(REVIEW_CSV.relative_to(ROOT)),
        stop_reason=stop_reason,
    )

    total_kw = sum(r["accepted_count"] for r in results)
    print(f"\nDONE - L3 {total_kw} | Scout {budget.scout}, Gemini {budget.gemini}")
    if stop_reason:
        print(f"  중단 사유: {stop_reason}")
        print(f"  재개: .venv/Scripts/python scripts/pilot_l3_review_batch.py --missing-l2 --merge-topics --resume ...")
    if args.merge_topics:
        print(f"  slot2step: {SLOT2STEP_CSV}")
    elif not prod_mode:
        print(f"  CSV: {REVIEW_CSV}")
    print(f"  report: {report_path}")


if __name__ == "__main__":
    main()
