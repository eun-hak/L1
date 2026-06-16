#!/usr/bin/env python3
import csv, statistics
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
rows = list(csv.DictReader(open(ROOT / "outputs/l2_candidates_gte100_depth1.csv", encoding="utf-8-sig")))

# source_seed 예시
print("=== source_seed 구조 ===")
for r in rows[:3]:
    print(f"  keyword={r['candidate_keyword']}, l1={r['l1_code']}")
    print(f"  source_seed={r['source_seed']}")
    seeds = r["source_seed"].split("|")
    print(f"  -> {len(seeds)}개 L2 seed: {seeds[:3]}")
    print()

# L1별 통계
l1_vols = defaultdict(list)
for r in rows:
    l1_vols[r["l1_code"]].append(int(r["monthly_total"]))

l1names = {
    "01": "엔터·미디어", "02": "금융·재테크", "03": "건강",
    "04": "패션", "05": "스포츠", "06": "생활정보",
    "07": "IT·테크", "08": "교육", "09": "맛집·카페",
    "10": "취미", "11": "여행", "12": "반려동물",
    "13": "부동산", "14": "육아", "15": "자동차",
    "16": "환경", "17": "법률", "18": "기타"
}

print("=== L1별 통계 ===")
print(f"{'코드':>4} {'건수':>6} {'고티어':>6} {'중앙값':>8} {'최대값':>12}  L1명")
total_hi, total_lt = 0, 0
for code, vols in sorted(l1_vols.items()):
    hi = sum(1 for v in vols if v >= 1000)
    lt = len(vols) - hi
    total_hi += hi
    total_lt += lt
    med = statistics.median(vols)
    mx = max(vols)
    print(f"  {code:>2}  {len(vols):>6}  {hi:>6}  {med:>8,.0f}  {mx:>12,}  {l1names.get(code,'')}")
print(f"{'합계':>6}  {len(rows):>6}  {total_hi:>6}  {'':>8}  {'':>12}")

# source_seed에서 L2 아이디 추출 → 중복 확인
print()
print("=== source_seed L2 패턴 예시 (상위 20개) ===")
from collections import Counter
seed_parts = []
for r in rows:
    for s in r["source_seed"].split("|"):
        seed_parts.append(s.strip())
seed_cnt = Counter(seed_parts)
for s, c in seed_cnt.most_common(20):
    print(f"  {s:40s} {c:6,}건")
