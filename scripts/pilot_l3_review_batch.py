#!/usr/bin/env python3
"""
L3 리뷰 파일럿 — slot_2step만 생성, 사람 검증용 CSV export.

Usage:
  python scripts/pilot_l3_review_batch.py
  python scripts/pilot_l3_review_batch.py --resume
  python scripts/pilot_l3_review_batch.py --l2 11-travel-essentials --count 15
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

import test_l3_diversity as td  # noqa: E402
from groq_client import MODEL_FAST as SCOUT_MODEL  # noqa: E402
from gemini_client import MODEL_FAST as GEMINI_MODEL  # noqa: E402

OUT_DIR = ROOT / "data2" / "test" / "l3_review_pilot"
CHECKPOINT = OUT_DIR / "checkpoint.json"
REVIEW_CSV = OUT_DIR / "l3_review_pilot.csv"
REPORT_JSON = OUT_DIR / "l3_review_pilot_report.json"
PHASE1_PLAN = ROOT / "data2" / "pilot" / "phase1_l3_l4_plan.csv"
BRIEF = ROOT / "data2" / "brief.txt"

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

    slots = td.generate_slots(
        l1_name=l1_name,
        l1_desc=l1_desc,
        l2_name=l2_name,
        l2_desc=l2_desc,
        count=target,
        brief=brief,
    )
    scout_calls += 1
    time.sleep(1.0)

    slots = td.gemini_dedup_slots(slots[:target], l2_name)
    gemini_calls += 1
    time.sleep(1.0)

    accepted: list[dict[str, str]] = []
    rejected: list[str] = []
    avoid: list[str] = []
    max_rounds = 4

    for _ in range(max_rounds):
        if len(accepted) >= count:
            break
        need = count - len(accepted)
        batch_slots = slots[len(accepted) : len(accepted) + need + 2]
        if not batch_slots:
            extra = td.generate_slots(
                l1_name=l1_name,
                l1_desc=l1_desc,
                l2_name=l2_name,
                l2_desc=l2_desc,
                count=need + 3,
                brief=brief,
            )
            scout_calls += 1
            time.sleep(1.0)
            extra = td.gemini_dedup_slots(extra, l2_name)
            gemini_calls += 1
            time.sleep(1.0)
            slots.extend(extra)
            batch_slots = slots[len(accepted) : len(accepted) + need + 2]
            if not batch_slots:
                break

        raw = td.generate_diverse_l3(
            l1_name=l1_name,
            l1_desc=l1_desc,
            l2_name=l2_name,
            l2_desc=l2_desc,
            count=need + 2,
            brief=brief,
            slots=batch_slots,
        )
        scout_calls += 1
        time.sleep(1.0)

        for item in raw:
            avoid.append(item["focus_keyword"])
        new_accepted, new_rejected = td.filter_diverse(raw)
        for item in new_accepted:
            if len(accepted) >= count:
                break
            stem = td.stem_keyword(item["focus_keyword"])
            if any(td.stem_keyword(a["focus_keyword"]) == stem for a in accepted):
                new_rejected.append(f"{item['focus_keyword']} (stem_dup_cross_batch)")
                continue
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


def main() -> None:
    parser = argparse.ArgumentParser(description="L3 review pilot batch")
    parser.add_argument("--l2", action="append", default=None)
    parser.add_argument("--count", type=int, default=15)
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()

    l2_ids = args.l2 or DEFAULT_L2
    brief = BRIEF.read_text(encoding="utf-8").strip() if BRIEF.exists() else ""
    phase1_meta = load_phase1_meta()
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    done: dict[str, dict] = {}
    if args.resume and CHECKPOINT.exists():
        data = json.loads(CHECKPOINT.read_text(encoding="utf-8"))
        for r in data.get("results", []):
            done[r["l2_id"]] = r
        print(f"resume: {len(done)} L2 로드")

    results: list[dict] = []
    total_scout = 0
    total_gemini = 0

    print(f"L3 리뷰 파일럿 — L2 {len(l2_ids)}개 × {args.count}개 (slot_2step)\n")

    for i, l2_id in enumerate(l2_ids, 1):
        if l2_id in done:
            results.append(done[l2_id])
            total_scout += done[l2_id].get("scout_calls", 0)
            total_gemini += done[l2_id].get("gemini_calls", 0)
            print(f"[{i}/{len(l2_ids)}] {l2_id} — checkpoint 스킵")
            continue

        print(f"[{i}/{len(l2_ids)}] {l2_id} ...", flush=True)
        try:
            result = generate_l2_batch(
                l2_id, count=args.count, brief=brief, phase1_meta=phase1_meta,
            )
        except Exception as exc:
            print(f"  ERROR: {exc}")
            partial = {
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "results": results,
                "failed_l2": l2_id,
                "error": str(exc),
            }
            CHECKPOINT.write_text(
                json.dumps(partial, ensure_ascii=False, indent=2), encoding="utf-8",
            )
            raise

        m = result["metrics"]
        print(
            f"  수락 {result['accepted_count']}/{args.count} | "
            f"stem유일 {m.get('stem_unique_pct')}% | 추천 {m.get('ends_with_추천')} | "
            f"Scout {result['scout_calls']} Gemini {result['gemini_calls']}"
        )
        results.append(result)
        total_scout += result["scout_calls"]
        total_gemini += result["gemini_calls"]

        checkpoint = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "scout_model": SCOUT_MODEL,
            "gemini_model": GEMINI_MODEL,
            "results": results,
        }
        CHECKPOINT.write_text(
            json.dumps(checkpoint, ensure_ascii=False, indent=2), encoding="utf-8",
        )
        time.sleep(1.5)

    review_rows = build_review_rows(results)
    write_review_csv(review_rows)

    total_kw = sum(r["accepted_count"] for r in results)
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "l2_count": len(results),
        "target_per_l2": args.count,
        "total_keywords": total_kw,
        "scout_calls": total_scout,
        "gemini_calls": total_gemini,
        "scout_model": SCOUT_MODEL,
        "gemini_model": GEMINI_MODEL,
        "output_csv": str(REVIEW_CSV.relative_to(ROOT)),
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
    REPORT_JSON.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\n완료 — L3 {total_kw}개")
    print(f"  CSV  → {REVIEW_CSV}")
    print(f"  JSON → {REPORT_JSON}")
    print(f"  API  → Scout {total_scout}회, Gemini {total_gemini}회")


if __name__ == "__main__":
    main()
