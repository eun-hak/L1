# L3 전량 재생성(claude_direct) — 통합 현황 및 향후 계획

> **작성일**: 2026-06-12  
> **최신 진행 현황**: [`data2-l3-claude-handoff.md`](./data2-l3-claude-handoff.md) ← **다른 세션은 여기부터**  
> **단일 기준 가이드**: [`cursor-master-guide.md`](./cursor-master-guide.md) (Claude 측, 확장안 704)  
> **시스템 개요**: [`data2-system.md`](./data2-system.md)  
> **역할 분담**: Claude = L3 키워드 생성(배치 납품) / Cursor = 통합·검증·정합·레지스트리·L4 준비. **Cursor는 키워드 생성·수정 안 함.**

---

## 1. 무엇을 하는가

기존 Scout `slot_2step` L3(5,805행)와 구본 Nemotron(11,099행)을 폐기하고,
**Claude가 704 L2 × 15 = 10,560행을 24배치로 재생성**해 전량 교체한다.
동시에 **L2를 603 → 704로 재설계**(통합 40·신규 141·신규 L1 4개).

| 목표 상태 | 값 |
|-----------|-----|
| L2 | **704** (확장안 A+B) |
| L1 | **18** (기존 14 + 생활/육아/커리어/게임) |
| L3 | **10,560** (704×15), 전부 `model=claude_direct` |
| 전역 중복 | norm 기준 0 |

---

## 2. 현재까지 통합 완료 (2026-06-12)

배치 **1·2·3** 통합 완료 (`scripts/integrate_claude_l3.py`).

| 항목 | 통합 전 | 통합 후 |
|------|---------|---------|
| L2 (`topics_l2.csv`) | 603 | **585** (−40 merge +22 신규) |
| L1 (`seed/topics_l1.csv`) | 14 | **18** |
| L3 (`topics_l3_slot2step.csv`) | 5,805 | **6,834** |
| ├ `claude_direct` | 0 | **1,350** (90 L2 × 15) |
| └ `slot_2step` (잔여 Scout) | 5,805 | 5,484 |

- merge 대상 Scout 행 321개 → `data2/archive/scout_replaced/`
- 백업: `backup/topics_l{1,2,3}*.20260612-*.{csv}`
- 검증: L2당 15 ✅ · 전역 norm 중복 0 ✅ · orphan 0 ✅ · l1_code 정합 ✅
- DB `taxonomy_slot2step.db` 동기화(delete+upsert) ✅

> L2가 585인 이유: 신규 141개 중 **배치로 전달된 22개만** 추가했다. 나머지 119개는 해당 배치가 오면 description과 함께 추가된다(가이드 §1). **704는 배치 24 완료 시 도달.**

---

## 3. 데이터 계약 (불변식) — `validate_batch.py`가 기준

스키마: `l3_id,l2_id,l1_code,l3_slug,focus_keyword,title_ko,search_intent,topic_angle,description,model,generated_at`

1. L2당 정확히 15행, `l3_id={l2_id}-{slug}` 유일
2. 전역 무충돌: `norm()` = suffix-strip + 토큰 정렬 기준 claude 행 간 중복 0
3. 다양성: info ≤ 9~10 · 동일 suffix ≤ 2 · 선두 토큰 ≤ 5 · 선두 바이그램 ≤ 2 · angle 분산 · 주제 축 ≥ 9
4. 제목 12~60자, `intent ∈ {info,howto,compare,cost,checklist,review,news}`, `angle ∈ {start,howto,compare,tip,review,issue,local}`
5. 기계적 곱 변형(연령·계절 나열) 금지

**`norm`/`stem` 정의는 `scripts/validate_batch.py` 단일 소스. 재구현 금지** — 통합·레지스트리 모두 이 함수를 임포트한다.

---

## 4. 배치 통합 운영 절차 (배치 4~24 반복)

남은 작업: **21배치 / 614 L2** (fill 243 + replace 371).

### 배치 도착 시

```bash
# 1) 배치 CSV를 staging 으로
cp <받은>/claude_l3_batchNN.csv data2/incoming/claude_l3/
# 신규 L2 description 도 함께 오면
cp <받은>/l2_new_descriptions.csv data2/incoming/claude_l3/l2_new_descriptions_bNN.csv

# 2) dry-run 검증
.venv/bin/python scripts/integrate_claude_l3.py \
  --batches data2/incoming/claude_l3/claude_l3_batchNN.csv --dry-run

# 3) 오류 0 이면 반영
.venv/bin/python scripts/integrate_claude_l3.py \
  --batches data2/incoming/claude_l3/claude_l3_batchNN.csv

# 4) DB 동기화는 스크립트가 미수행 → 통합 후 1회
#    (현재는 별도 1-회성 sync; §6 자동화 과제)
```

`integrate_claude_l3.py` 동작:
- `replace`/`fill` 자동 처리: 배치에 포함된 L2의 **기존 행을 모두 제거 후 claude 행 삽입** (Scout 잔재 자동 소멸)
- merge L2 Scout 행은 최초 1회 아카이브 후 제거
- 신규 L2(배치 등장 + description 보유)만 `topics_l2.csv`에 추가
- 검증 오류 시 **파일 미반영** + 리포트(`data2/reports/claude_l3_integration_report.json`)

### 진행률 점검

```sql
SELECT model, COUNT(*) FROM l3_topics GROUP BY model;   -- claude_direct = 완료 L2 × 15
SELECT l2_id, COUNT(*) c FROM l3_topics GROUP BY l2_id HAVING c != 15;  -- 비정상
```

기대값: 배치 N 통합 후 `claude_direct = N×450` (배치 24 후 10,560, slot_2step 0).

---

## 5. 기대 종료 상태 (배치 24)

| 항목 | 값 |
|------|-----|
| `claude_direct` | 10,560 (704 L2 × 15) |
| `slot_2step` | 0 (전량 교체) |
| `topics_l2.csv` | 704 L2 |
| 구본 `topics_l3.csv` | 아카이브 유지 (롤백 대비, 미사용) |

---

## 6. Cursor 구현 과제 (생성 외 설계)

가이드 §5 기준. 우선순위 순.

| # | 과제 | 상태 | 산출물 |
|---|------|------|--------|
| 1 | **DB delete 동기화** | ✅ 통합 스크립트 + 1회성 sync로 처리 (배치마다 delete 반영) | `integrate_claude_l3.py` |
| 2 | **전역 키워드 레지스트리** | ⏳ TODO | `registry(norm_stem PK, l3_id, l2_id, created_at)` — `validate_batch.norm`으로 전체 생성. **L4 중복 차단의 근거** |
| 3 | **품질 대시보드** | ⏳ TODO | L2별 intent/angle 분포·선두 바이그램·suffix 반복 점검 스크립트, 배치마다 회귀 감시 |
| 4 | **구본 아카이브** | ⏳ TODO | Nemotron 210 L2 + Scout 교체분 → `archive/` (삭제 금지, 롤백 대비) |

### DB sync 자동화 (과제 1 보강)

현재 `integrate_claude_l3.py`는 CSV/JSON만 기록하고 DB는 별도 1회성 스크립트로 동기화한다.
→ 통합 스크립트에 `--apply-to-db` 옵션을 넣어 delete+upsert+export까지 한 번에 처리하도록 보강.

---

## 7. L4 준비 지침 (재생성 완료 후)

- **Scout 시드 위에서 L4 가동 금지.** L4는 시드 결함을 100배 증폭. claude_direct로 교체 완료된 L2에서만.
- 권장 첫 프로토타입: `09` 서울 음식 계열 1개 L2, `engine=program` (`data2/pilot/phase1_l3_l4_plan.csv` 참조).
- L4 스키마(합의안):

```
l4_id, l3_id, l2_id, l1_code, engine, primary_kw, norm_stem,
axes_json, pattern_id, tier(warehouse/candidate/publish/hold),
hold_reason, source, generated_at
```

- L4 삽입 전 **레지스트리(과제 2) 조인 필수**. 저장은 SQLite 원본 + L2 단위 샤딩 CSV.

---

## 8. 금지사항 (가이드 §8)

- 키워드/제목 텍스트 **수정 금지** (전역 무충돌 검증 무효화). 문제 발견 시 보고 → Claude가 배치에서 수정.
- 구본 11,099행과 **merge 금지**.
- curate 탈락분 **임의 보충 생성 금지** (탈락 0이 정상, 발생 시 원인 보고).
- API 한도 **우회 금지**.

---

## 9. 즉시 다음 작업 (2주)

1. **배치 4~7 수령·통합** (fill 위주, L1 03~10 신규/미커버) — 배치당 `integrate_claude_l3.py` dry-run→반영
2. **전역 키워드 레지스트리** 스크립트 (`scripts/build_kw_registry.py`) — `validate_batch.norm` 임포트
3. **품질 대시보드** 스크립트 — §3 불변식 회귀 감시
4. **DB sync 통합** (`--apply-to-db`) — 통합 1단계화
5. **구본 아카이브** — `topics_l3.csv`·`topics_l3_curated/hold/dropped` → `data2/archive/legacy_nemotron/`

> 배치 8부터 replace 단계 진입(기존 Scout L2 교체). 통합 스크립트가 이미 replace를 지원하므로 동일 절차.

---

## 10. 파일 인벤토리

| 파일 | 용도 |
|------|------|
| `scripts/integrate_claude_l3.py` | 배치 통합 + L2 재설계 (재사용) |
| `scripts/validate_batch.py` | `norm/stem` 단일 정의 + 검증 (임포트 전용) |
| `data2/incoming/claude_l3/` | 수신 배치·맵·plan staging |
| `data2/incoming/claude_l3/l2_redesign_map.csv` | L2 재설계 지시서 (keep/merge/rename/move/new) |
| `data2/incoming/claude_l3/generation_batch_plan.csv` | 배치 2~24 대상 L2 확정본 |
| `data2/topics_l2.csv` | L2 마스터 (현재 585 → 704) |
| `data2/topics_l3_slot2step.csv` | L3 마스터 (claude_direct + 잔여 Scout) |
| `data2/archive/scout_replaced/` | merge·replace된 Scout 행 |
| `backup/` | 통합 직전 L1/L2/L3 백업 |
| `data2/reports/claude_l3_integration_report.json` | 마지막 통합 리포트 |

---

*갱신: 배치 통합·L4 착수 시 §2 현황과 §9 작업을 먼저 수정할 것.*
