# data2 — 택소노미 데이터

> L3 claude_direct 재생성 **완료** (704 L2 × 15 = 10,560행)

## 지금 쓰는 파일 (canonical)

```
data2/
├── seed/topics_l1.csv              # 18 L1
├── topics_l2.csv                   # 704 L2
├── topics_l3_slot2step.csv         # 10,560 L3 (claude_direct)
├── topics_l3_slot2step.json        # CSV 미러
├── manifest_l3_slot2step.json      # 메타 (행수·진행률)
├── brief.txt                       # 생성 브리프
└── state/taxonomy_slot2step.db     # SQLite 원본
```

**L4·본문 착수 시** → `pilot/phase1_l3_l4_plan.csv`  
**L2 재설계 참조** → `incoming/claude_l3/generation_batch_plan.csv`, `l2_redesign_map.csv`

## 폴더 역할

| 폴더 | 용도 |
|------|------|
| `pilot/` | Phase1 L4·본문 계획·샘플 큐 |
| `answers/` | 본문 파일럿 샘플 (10건) |
| `reports/` | 최신 통합 리포트 |
| `incoming/claude_l3/` | 운영 참조 CSV 2종만 유지 |
| `archive/` | 레거시·로그·테스트·통합 완료 배치 (삭제 금지) |

## archive/ 하위

| 경로 | 내용 |
|------|------|
| `legacy_nemotron/` | 구 Nemotron L3 (11,099행), taxonomy.db |
| `legacy_scout/` | Scout slot2step 시대 checkpoint·리포트 |
| `scout_replaced/` | claude 통합 시 교체된 Scout 행 |
| `incoming_processed/` | claude_l3_batch01~08, all, descriptions |
| `l2_expansion/` | L2 603→704 확장 중간본 |
| `logs/`, `test/`, `reports/` | 실행 로그·실험·구 리포트 |
| `backups/` | 오래된 통합 백업 |
| `REORG_MANIFEST.json` | 정리 이동 기록 |

## 주의

- **canonical 6종만** 프로덕션. `archive/` 파일과 merge 금지.
- 키워드 텍스트 수정 금지 → 문제 시 Claude 배치 재요청.
- 상세 운영: `docs/data2-l3-claude-handoff.md`
