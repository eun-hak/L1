#!/usr/bin/env python3
"""
Claude 직접 생성 L3 배치 통합 + L2 재설계 적용.

Cursor 역할(가이드 §0): 배치 통합·검증·정합. 키워드 생성/수정 안 함.

동작:
  1. topics_l2.csv / topics_l3_slot2step.csv / seed/topics_l1.csv 백업
  2. l2_redesign_map.csv 적용 (rename name_ko / move l1_code / merge 삭제)
     + 신규 L2 추가 (배치에 등장 & description 보유분만)
     + 확장안(--scenario ab): L1 15~18 추가, move l1_code 변경
  3. merge 대상 L2의 기존 Scout 행 아카이브 후 제거
  4. 배치 CSV 통합: 해당 L2 기존 행 제거 후 claude_direct 행 삽입 (fill=append, replace 겸용)
  5. 검증: L2당 15, 전역 norm 중복 0, l2_id/l1_code 정합, intent/angle 유효
  6. 리포트 JSON

norm/stem 정의는 scripts/validate_batch.py를 그대로 임포트 (가이드 §3: 재구현 금지).

Usage:
  python scripts/integrate_claude_l3.py                       # incoming/claude_l3/claude_l3_batch*.csv 전체
  python scripts/integrate_claude_l3.py --batches data2/incoming/claude_l3/claude_l3_batch04.csv
  python scripts/integrate_claude_l3.py --scenario a          # 보수안(647), L1 15~18 미추가
  python scripts/integrate_claude_l3.py --dry-run
"""

from __future__ import annotations

import argparse
import csv
import glob
import json
import shutil
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))

from validate_batch import norm, VALID_INTENTS, VALID_ANGLES  # noqa: E402

DATA = ROOT / "data2"
INCOMING = DATA / "incoming" / "claude_l3"
L1_CSV = DATA / "seed" / "topics_l1.csv"
L2_CSV = DATA / "topics_l2.csv"
L3_CSV = DATA / "topics_l3_slot2step.csv"
REDESIGN_MAP = INCOMING / "l2_redesign_map.csv"
ARCHIVE_DIR = DATA / "archive" / "scout_replaced"
BACKUP_DIR = ROOT / "backup"
REPORT = DATA / "reports" / "claude_l3_integration_report.json"

L2_FIELDS = ["l2_id", "l1_code", "l2_slug", "name_ko", "description", "sort_order", "model", "generated_at"]
L3_FIELDS = ["l3_id", "l2_id", "l1_code", "l3_slug", "focus_keyword", "title_ko",
             "search_intent", "topic_angle", "description", "model", "generated_at"]
L1_FIELDS = ["l1_code", "l1_slug", "name_ko", "theme", "description", "sort_order"]

# 확장안 신규 L1 (l2-redesign-proposal.md §3)
NEW_L1 = [
    {"l1_code": "15", "l1_slug": "living", "name_ko": "생활·리빙", "theme": "인테리어·정리·청소·자취",
     "description": "인테리어·정리수납·청소·이사·자취·식물 가드닝 등 생활 리빙 정보", "sort_order": "15"},
    {"l1_code": "16", "l1_slug": "parenting", "name_ko": "육아·교육", "theme": "임신출산·육아·학습",
     "description": "임신·출산·영유아 육아·초등 학습 등 육아와 교육 정보", "sort_order": "16"},
    {"l1_code": "17", "l1_slug": "career", "name_ko": "직장·커리어", "theme": "취업·이직·부업·재택",
     "description": "취업·이직·부업·재택·공무원 등 직장과 커리어 정보 (돈 관점은 02)", "sort_order": "17"},
    {"l1_code": "18", "l1_slug": "game", "name_ko": "게임", "theme": "게임·e스포츠",
     "description": "PC·모바일·콘솔 게임 공략·리뷰·e스포츠 정보", "sort_order": "18"},
]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_csv(path: Path) -> list[dict]:
    with path.open(encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def write_csv(path: Path, rows: list[dict], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def slug_from_l2_id(l2_id: str) -> str:
    return l2_id.split("-", 1)[1] if "-" in l2_id else l2_id


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--batches", nargs="*", default=None, help="배치 CSV 경로 (기본 incoming 전체)")
    ap.add_argument("--scenario", choices=["a", "ab"], default="ab", help="a=보수647, ab=확장704")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    batch_paths = sorted(args.batches or glob.glob(str(INCOMING / "claude_l3_batch*.csv")))
    if not batch_paths:
        print("배치 파일 없음"); sys.exit(1)

    redesign = read_csv(REDESIGN_MAP)
    new_desc: dict[str, dict] = {}
    for p in INCOMING.glob("l2_new_descriptions*.csv"):
        for r in read_csv(p):
            new_desc[r["l2_id"]] = r

    rename = {r["l2_id"]: r["new_value"] for r in redesign if r["action"] == "rename"}
    move = {r["l2_id"]: r["new_value"] for r in redesign if r["action"] == "move"}
    merge_ids = {r["l2_id"] for r in redesign if r["action"] == "merge"}
    new_meta = {r["l2_id"]: r for r in redesign if r["action"] == "new"}

    # 배치 로드 + 대상 L2
    batch_rows: list[dict] = []
    batch_l2s: set[str] = set()
    for bp in batch_paths:
        rows = read_csv(Path(bp))
        batch_rows.extend(rows)
        batch_l2s |= {r["l2_id"] for r in rows}

    report: dict = {
        "generated_at": now_iso(),
        "scenario": args.scenario,
        "batches": [Path(p).name for p in batch_paths],
        "batch_rows": len(batch_rows),
        "batch_l2s": len(batch_l2s),
    }

    # ---- L2 v2 ----
    l2_rows = read_csv(L2_CSV)
    l2_by_id = {r["l2_id"]: r for r in l2_rows}
    applied = {"rename": 0, "move": 0, "merge_removed": 0, "new_added": 0}

    for lid, nm in rename.items():
        if lid in l2_by_id:
            l2_by_id[lid]["name_ko"] = nm
            applied["rename"] += 1
    if args.scenario == "ab":
        for lid, new_l1 in move.items():
            if lid in l2_by_id:
                l2_by_id[lid]["l1_code"] = new_l1
                applied["move"] += 1
    for lid in merge_ids:
        if lid in l2_by_id:
            del l2_by_id[lid]
            applied["merge_removed"] += 1

    # 신규 L2 — 이번 배치에 등장하고 description 보유분만 추가
    max_sort = max((int(r["sort_order"] or 0) for r in l2_by_id.values()), default=0)
    for lid in sorted(batch_l2s):
        if lid in l2_by_id or lid not in new_meta:
            continue
        meta = new_meta[lid]
        desc_row = new_desc.get(lid, {})
        if args.scenario == "a" and meta["scenario"] == "B":
            continue
        max_sort += 1
        l2_by_id[lid] = {
            "l2_id": lid,
            "l1_code": meta["l1_code"],
            "l2_slug": slug_from_l2_id(lid),
            "name_ko": meta["name_ko"] or desc_row.get("name_ko", ""),
            "description": desc_row.get("description", ""),
            "sort_order": str(max_sort),
            "model": "claude_direct",
            "generated_at": now_iso(),
        }
        applied["new_added"] += 1

    l2_final = sorted(l2_by_id.values(), key=lambda r: (r["l1_code"], int(r["sort_order"] or 0)))
    report["l2"] = {**applied, "l2_total_after": len(l2_final)}

    # ---- L1 v2 (확장) ----
    l1_rows = read_csv(L1_CSV)
    l1_codes = {r["l1_code"] for r in l1_rows}
    if args.scenario == "ab":
        for nl1 in NEW_L1:
            if nl1["l1_code"] not in l1_codes:
                l1_rows.append(nl1)
    report["l1_total_after"] = len(l1_rows)

    # ---- L3 통합 ----
    l3_rows = read_csv(L3_CSV)
    valid_l2 = set(l2_by_id)

    # merge 대상 Scout 행 아카이브 + 제거
    merged_scout = [r for r in l3_rows if r["l2_id"] in merge_ids]
    # 배치 대상 L2 기존 행 (replace) 도 아카이브
    replaced = [r for r in l3_rows if r["l2_id"] in batch_l2s]
    kept = [r for r in l3_rows if r["l2_id"] not in merge_ids and r["l2_id"] not in batch_l2s]

    # 배치 행 l1_code 정합 보정 (move 반영)
    for r in batch_rows:
        if r["l2_id"] in l2_by_id:
            r["l1_code"] = l2_by_id[r["l2_id"]]["l1_code"]

    l3_final = kept + batch_rows
    report["l3"] = {
        "before": len(l3_rows),
        "merged_scout_archived": len(merged_scout),
        "replaced_archived": len(replaced),
        "claude_appended": len(batch_rows),
        "after": len(l3_final),
    }

    # ---- 검증 ----
    errors: list[str] = []
    warns: list[str] = []

    # 배치 L2당 정확히 15
    bl2_count = Counter(r["l2_id"] for r in batch_rows)
    for lid, c in bl2_count.items():
        if c != 15:
            errors.append(f"CAP {lid}: {c}")
        if lid not in valid_l2:
            errors.append(f"L2_MISSING {lid} (topics_l2 v2에 없음)")

    # 전역 norm 중복 (claude_direct 행 전체)
    claude_rows = [r for r in l3_final if r["model"] == "claude_direct"]
    norm_map: dict[str, str] = {}
    for r in claude_rows:
        n = norm(r["focus_keyword"])
        if n in norm_map:
            errors.append(f"GLOBAL_DUP '{r['focus_keyword']}' ({r['l2_id']}) ↔ {norm_map[n]}")
        else:
            norm_map[n] = f"{r['l2_id']}"

    # intent/angle/l1_code 정합
    for r in batch_rows:
        if r["search_intent"] not in VALID_INTENTS:
            errors.append(f"INTENT {r['l3_id']}: {r['search_intent']}")
        if r["topic_angle"] not in VALID_ANGLES:
            errors.append(f"ANGLE {r['l3_id']}: {r['topic_angle']}")
        exp_l1 = l2_by_id.get(r["l2_id"], {}).get("l1_code")
        if exp_l1 and r["l1_code"] != exp_l1:
            warns.append(f"L1_FIX {r['l3_id']}: {r['l1_code']}→{exp_l1}")

    # l3_id 유일
    ids = Counter(r["l3_id"] for r in l3_final)
    for i, c in ids.items():
        if c > 1:
            errors.append(f"ID_DUP {i}: {c}")

    report["validation"] = {"errors": len(errors), "warnings": len(warns),
                            "error_list": errors[:50], "warn_sample": warns[:20]}
    report["model_counts_after"] = dict(Counter(r["model"] for r in l3_final))

    print(f"[L2] rename {applied['rename']} move {applied['move']} merge제거 {applied['merge_removed']} "
          f"신규 {applied['new_added']} → {len(l2_final)} L2")
    print(f"[L1] → {len(l1_rows)} L1")
    print(f"[L3] {len(l3_rows)} → {len(l3_final)} (merge아카이브 {len(merged_scout)}, "
          f"replace아카이브 {len(replaced)}, claude +{len(batch_rows)})")
    print(f"[검증] 오류 {len(errors)} 경고 {len(warns)}")
    for e in errors[:20]:
        print("  ERR:", e)

    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    if errors:
        print("\n오류 존재 — 파일 미반영. 리포트:", REPORT)
        sys.exit(1)
    if args.dry_run:
        print("\n(dry-run) 파일 미반영. 리포트:", REPORT)
        return

    # ---- 백업 후 기록 ----
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    BACKUP_DIR.mkdir(exist_ok=True)
    for src in (L2_CSV, L3_CSV, L1_CSV):
        shutil.copy(src, BACKUP_DIR / f"{src.stem}.{ts}{src.suffix}")

    if merged_scout or replaced:
        ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
        arch = merged_scout + replaced
        write_csv(ARCHIVE_DIR / f"scout_replaced_{ts}.csv", arch, L3_FIELDS)

    write_csv(L2_CSV, l2_final, L2_FIELDS)
    write_csv(L1_CSV, l1_rows, L1_FIELDS)
    write_csv(L3_CSV, l3_final, L3_FIELDS)
    # JSON 미러
    (DATA / "topics_l3_slot2step.json").write_text(
        json.dumps([{k: r.get(k, "") for k in L3_FIELDS} for r in l3_final], ensure_ascii=False, indent=2),
        encoding="utf-8")

    print(f"\n반영 완료. 백업: {BACKUP_DIR}/*.{ts}.*  리포트: {REPORT}")


if __name__ == "__main__":
    main()
