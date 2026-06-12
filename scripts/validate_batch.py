#!/usr/bin/env python3
"""Claude 직접 생성 L3 배치 검증·조립.

입력: 파이프 구분 .psv (l2_id|slug|focus_keyword|title_ko|intent|angle|description)
검증: 스키마, L2내 stem dedup, 기존 5,805행+누적분과의 전역 norm 충돌, cap 15,
      intent/angle 유효성, suffix cap(>2 경고), intent 편중(info>9 경고)
출력: 풀 스키마 CSV (L3_FIELDNAMES 호환) + 누적 마스터
"""
import csv, sys, glob
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

UPLOADS = Path("/mnt/user-data/uploads")
WORK = Path("/home/claude/l3gen")

INTENT_SUFFIX = {
    "추천","레시피","방법","비교","가이드","일정","후기","분석","정리","소개",
    "팁","순위","가격","리스트","안내","체크리스트","예측","근황","라인업",
    "트렌드","입문","초보","무료","유튜브","다운로드","신청","예약","위치",
    "맛집","카페","여행","축제","음식","음악","의상","문화","전통","온라인",
    "인기","고전","초보자","스타일링","관리","점검","운동","식단","루틴",
    "사용법","리뷰","전략","법","확인","준비물","체험","일정표","필수품","필수",
}
VALID_INTENTS = {"info","howto","compare","cost","checklist","review","news"}
VALID_ANGLES = {"start","howto","compare","tip","review","issue","local"}
FIELDNAMES = ["l3_id","l2_id","l1_code","l3_slug","focus_keyword","title_ko",
              "search_intent","topic_angle","description","model","generated_at"]

def stem(text):
    toks = text.strip().split()
    while toks and toks[-1] in INTENT_SUFFIX:
        toks.pop()
    return " ".join(toks) if toks else text.strip()

def norm(text):
    return " ".join(sorted(stem(text).split()))

def main():
    batch_files = sorted(sys.argv[1:])
    l2_map = {r["l2_id"]: r for r in csv.DictReader(open(UPLOADS/"topics_l2.csv", encoding="utf-8-sig"))}

    # 배치가 다루는 L2 집합 선파악 (교체 모드: 해당 L2의 기존 행은 레지스트리 제외)
    batch_l2s = set()
    for bf in batch_files:
        for line in open(bf, encoding="utf-8"):
            line = line.strip()
            if line and not line.startswith("#"):
                batch_l2s.add(line.split("|")[0].strip())

    # 전역 레지스트리: 기존 마스터(교체 대상 L2 제외, Scout 행은 soft) + 이전 배치 누적(hard)
    registry = {}      # hard: 충돌 시 ERR
    soft_registry = {} # soft: 충돌 시 WARN (교체 예정 Scout 행)
    for r in csv.DictReader(open(UPLOADS/"topics_l3_slot2step.csv", encoding="utf-8-sig")):
        if r["l2_id"] in batch_l2s:
            continue
        soft_registry.setdefault(norm(r["focus_keyword"]), f"scout:{r['l2_id']}")
    acc_path = WORK/"claude_l3_accumulated.csv"
    accumulated = []
    if acc_path.exists():
        accumulated = list(csv.DictReader(open(acc_path, encoding="utf-8-sig")))
        for r in accumulated:
            registry.setdefault(norm(r["focus_keyword"]), f"batch:{r['l2_id']}")

    now = datetime.now(timezone.utc).isoformat()
    rows, errors, warns = [], [], []
    per_l2 = defaultdict(list)
    seen_ids = {r["l3_id"] for r in accumulated}

    for bf in batch_files:
        for ln, line in enumerate(open(bf, encoding="utf-8"), 1):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split("|")
            if len(parts) != 7:
                errors.append(f"{bf}:{ln} 필드수 {len(parts)}")
                continue
            l2_id, slug, kw, title, intent, angle, desc = [p.strip() for p in parts]
            if l2_id not in l2_map:
                errors.append(f"{bf}:{ln} 미존재 l2_id {l2_id}"); continue
            if intent not in VALID_INTENTS:
                errors.append(f"{bf}:{ln} intent {intent}"); continue
            if angle not in VALID_ANGLES:
                errors.append(f"{bf}:{ln} angle {angle}"); continue
            n = norm(kw)
            if n in registry:
                errors.append(f"GLOBAL_DUP {l2_id} '{kw}' ↔ {registry[n]}"); continue
            if n in soft_registry:
                warns.append(f"SCOUT_DUP {l2_id} '{kw}' ↔ {soft_registry[n]} (교체 시 해소)")
            l3_id = f"{l2_id}-{slug}"
            if l3_id in seen_ids:
                errors.append(f"ID_DUP {l3_id}"); continue
            registry[n] = f"new:{l2_id}"
            seen_ids.add(l3_id)
            row = {"l3_id": l3_id, "l2_id": l2_id, "l1_code": l2_map[l2_id]["l1_code"],
                   "l3_slug": slug, "focus_keyword": kw, "title_ko": title,
                   "search_intent": intent, "topic_angle": angle, "description": desc,
                   "model": "claude_direct", "generated_at": now}
            rows.append(row)
            per_l2[l2_id].append(row)

    # L2 단위 품질 리포트
    for l2_id, grp in sorted(per_l2.items()):
        if len(grp) > 15:
            errors.append(f"CAP {l2_id}: {len(grp)}")
        if len(grp) < 15:
            warns.append(f"UNDER {l2_id}: {len(grp)}")
        ic = Counter(r["search_intent"] for r in grp)
        if ic.get("info", 0) > 9:
            warns.append(f"INFO_SKEW {l2_id}: info={ic['info']}")
        sc = Counter(r["focus_keyword"].split()[-1] for r in grp if r["focus_keyword"].split()[-1] in INTENT_SUFFIX)
        for s, c in sc.items():
            if c > 2:
                warns.append(f"SUFFIX {l2_id}: '{s}'×{c}")
        # 선두 토큰/바이그램 다양성 (축 분산 프록시)
        ft = Counter(r["focus_keyword"].split()[0] for r in grp)
        for tok, c in ft.items():
            if c > 5:
                warns.append(f"FIRST_TOKEN {l2_id}: '{tok}'×{c}")
        bg = Counter(" ".join(r["focus_keyword"].split()[:2]) for r in grp if len(r["focus_keyword"].split()) >= 2)
        for b, c in bg.items():
            if c > 2:
                warns.append(f"BIGRAM {l2_id}: '{b}'×{c}")
        # 제목 길이
        for r in grp:
            tl = len(r["title_ko"])
            if tl < 12 or tl > 60:
                warns.append(f"TITLE_LEN {r['l3_id']}: {tl}자")

    print(f"입력 행: {sum(1 for _ in rows) + len(errors)}건 시도 → 유효 {len(rows)}")
    print(f"L2 수: {len(per_l2)} | 오류 {len(errors)} | 경고 {len(warns)}")
    for e in errors: print(" ERR:", e)
    for w in warns[:25]: print(" WARN:", w)

    if errors:
        print("\n오류 존재 — 파일 미생성. 수정 후 재실행.")
        sys.exit(1)

    batch_out = WORK/"claude_l3_batch_current.csv"
    with open(batch_out, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDNAMES); w.writeheader(); w.writerows(rows)
    all_rows = accumulated + rows
    with open(acc_path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDNAMES); w.writeheader(); w.writerows(all_rows)

    # 통계
    ic = Counter(r["search_intent"] for r in rows)
    ac = Counter(r["topic_angle"] for r in rows)
    print("\nintent:", dict(ic.most_common()))
    print("angle:", dict(ac.most_common()))
    print(f"배치: {batch_out} ({len(rows)}) | 누적: {acc_path} ({len(all_rows)})")

if __name__ == "__main__":
    main()
