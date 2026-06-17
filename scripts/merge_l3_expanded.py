#!/usr/bin/env python3
"""생성된 L3 파일 통합 + 기존 L3와 합산.

실행:
  python scripts/merge_l3_expanded.py

출력:
  outputs/l3_expanded_all.csv   (신규 5만 + 기존 slot2step 1만 = 약 6만행)
  outputs/l3_merge_report.json  (통합 리포트)
"""

from __future__ import annotations

import csv
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "outputs"
DATA2_DIR = ROOT / "data2"

HITIER_CSV = OUT_DIR / "l3_expanded_hitier.csv"
LONGTAIL_CSV = OUT_DIR / "l3_expanded_longtail.csv"
EXISTING_L3_CSV = DATA2_DIR / "topics_l3_slot2step.csv"
ALL_CSV = OUT_DIR / "l3_expanded_all.csv"
REPORT_JSON = OUT_DIR / "l3_merge_report.json"

L3_FIELDNAMES = [
    "l3_id", "l2_id", "l1_code", "l3_slug",
    "focus_keyword", "title_ko",
    "search_intent", "topic_angle",
    "description", "model", "generated_at",
    "monthly_total", "tier",
]


def load_csv(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return list(csv.DictReader(path.open(encoding="utf-8-sig")))


def main() -> None:
    now = datetime.now(timezone.utc).isoformat()
    print(f"L3 통합 시작: {now}")

    hitier = load_csv(HITIER_CSV)
    longtail = load_csv(LONGTAIL_CSV)
    existing = load_csv(EXISTING_L3_CSV)

    print(f"  고티어 신규: {len(hitier):,}개")
    print(f"  롱테일 신규: {len(longtail):,}개")
    print(f"  기존 L3:     {len(existing):,}개")

    # 기존 L3에 월 검색량·티어 컬럼 없으면 채움
    for r in existing:
        r.setdefault("monthly_total", "")
        r.setdefault("tier", "existing")

    all_rows = existing + hitier + longtail

    # 중복 제거 (l3_id 기준)
    seen_ids: set[str] = set()
    deduped: list[dict] = []
    dup_count = 0
    for r in all_rows:
        lid = r.get("l3_id", "")
        if lid in seen_ids:
            dup_count += 1
            continue
        seen_ids.add(lid)
        deduped.append(r)

    print(f"  중복 제거: {dup_count}개")
    print(f"  최종 통합: {len(deduped):,}개")

    # 출력
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    with open(ALL_CSV, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=L3_FIELDNAMES, extrasaction="ignore")
        w.writeheader()
        w.writerows(deduped)
    print(f"  저장: {ALL_CSV}")

    # BOM 없는 버전도 저장 (프로그램용)
    all_no_bom = ALL_CSV.with_suffix(".nobom.csv")
    with open(all_no_bom, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=L3_FIELDNAMES, extrasaction="ignore")
        w.writeheader()
        w.writerows(deduped)

    # 리포트
    l1_dist = Counter(r["l1_code"] for r in deduped)
    intent_dist = Counter(r["search_intent"] for r in deduped)
    model_dist = Counter(r.get("model", "unknown") for r in deduped)
    tier_dist = Counter(r.get("tier", "unknown") for r in deduped)

    report = {
        "generated_at": now,
        "counts": {
            "hitier_new": len(hitier),
            "longtail_new": len(longtail),
            "existing": len(existing),
            "deduped": dup_count,
            "total": len(deduped),
        },
        "l1_distribution": dict(sorted(l1_dist.items())),
        "intent_distribution": dict(intent_dist.most_common()),
        "model_distribution": dict(model_dist.most_common()),
        "tier_distribution": dict(tier_dist.most_common()),
    }
    REPORT_JSON.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  리포트: {REPORT_JSON}")
    print(f"\n완료. 총 {len(deduped):,}개 L3 항목.")


if __name__ == "__main__":
    main()
