# data2 — 택소노미 canonical

> L3 claude_direct **완료** (704 L2 × 15 = 10,560행)  
> **L3 bulk(5만) 운영 문서:** [`docs/l3-bulk-server-handoff-2026-06-17.md`](../docs/l3-bulk-server-handoff-2026-06-17.md)

## 프로덕션 파일 (이 폴더만)

```
data2/
├── seed/topics_l1.csv              # 18 L1
├── topics_l2.csv                   # 704 L2
├── topics_l3_slot2step.csv         # 10,560 L3 (claude_direct)
├── topics_l3_slot2step.json        # CSV 미러
└── manifest_l3_slot2step.json      # 행수·메타
```

## L3 bulk에서 쓰는 것

| 파일 | bulk 생성 | merge |
|------|-----------|-------|
| `topics_l2.csv` | ✅ l2_id 매핑 | — |
| `topics_l3_slot2step.csv` | — | ✅ 기존 1만 합산 |
| `seed/topics_l1.csv` | — | 참조 |

입력 키워드: `outputs/l2_candidates_gte100_depth1.csv` (50,017행)

## 레거시

참고·아카이브·실험·파일럿은 **`legacy/`** 로 이동 (gitignore).

```
legacy/data2/archive/     # Nemotron·Scout·백업
legacy/data2/pilot/       # Phase1 계획
legacy/data2/answers/     # 본문 파일럿
legacy/data2/incoming/    # L2 재설계 참조
legacy/data2/state/       # slot2step SQLite
...
```

## 주의

- canonical과 `legacy/` 파일 **merge 금지**
- 키워드 텍스트 수동 수정 금지
- slot2step 구 파이프: `docs/data2-l3-claude-handoff.md`
