# Cursor 마스터 가이드 — L3 전량 재생성 통합 및 시스템 설계

> 이 문서가 단일 기준이다. 이전 `cursor-handoff-l3-claude.md`는 폐기되었다.

## 0. 한 줄 요약
Claude가 **603개 L2 전체의 L3(= 9,045행)를 배치 단위로 재생성**한다. Cursor의 역할은 (1) 배치 통합, (2) 검증, (3) 전역 키워드 레지스트리 구축, (4) L4 준비다. **Cursor는 키워드를 생성·수정하지 않는다.**

## 1. 목표 상태 (End State) — L2 개편(확장안 704) 반영
- **선행 단계**: `l2_redesign_map.csv`를 적용해 `topics_l2.csv`를 개편 (merge 40 삭제·rename 8은 name_ko만 변경·신규 141 추가·이동 4는 l1_code 변경, **l2_id는 전 과정 불변**). 최종 모습 = `topics_l2_v2_final.csv` (704 L2, 신규 L1 15~18 포함)
- `topics_l3_slot2step.csv` = 704 L2 × 15 = **10,560행, 전부 `model=claude_direct`**
- 폐기 대상: 기존 Scout 5,805행(배치 8~21에서 L2 단위로 교체), 구본 Nemotron 11,099행(아카이브 후 미사용)
- 모든 focus_keyword는 전역 norm 기준 유일 (교차 L2 중복 0)
- 품질 불변식(§3) 전 L2 충족

## 2. 배치 일정 (총 24배치, 회당 ~450행)
배치별 대상 L2는 **`generation_batch_plan.csv`가 확정한다** (배치 1 완료분 30 L2 제외, 잔여 674 L2를 배치 2~24에 배정. 마지막 배치 14개).
- **배치 1**: ✅ 납품 (`claude_l3_batch01.csv`, 450행 — 개편 후에도 30 L2 전부 생존, 수정 불필요)
- **fill 단계 (배치 2~12, append)**: Scout L3가 없는 빈 L2 303개 (기존 미커버 잔여 + 신규 141 + 신규 L1)
- **replace 단계 (배치 12~24, replace)**: Scout L3 보유 371 L2 교체 (배치 12는 fill 3 + replace 27 혼합 — plan CSV의 phase 컬럼 따름)

## 3. 데이터 계약과 불변식
스키마(기존과 동일): `l3_id,l2_id,l1_code,l3_slug,focus_keyword,title_ko,search_intent,topic_angle,description,model,generated_at`

배치마다 다음이 보장된 상태로 납품된다 (`validate_batch.py`가 강제):
1. L2당 정확히 15행, `l3_id = {l2_id}-{slug}` 유일
2. **전역 무충돌**: norm(suffix-strip + 토큰 정렬) 기준, claude 행 간 충돌 = 오류 0
3. **다양성**: L2당 info ≤ 9~10 · 동일 suffix ≤ 2 · 동일 선두 토큰 ≤ 5 · 동일 선두 바이그램 ≤ 2 · angle 7종 분산 · 주제 축(트렌드/대상/상황/장소/도구/비용/원리/사기예방/팩트체크/역사/이색/후기) 최소 9개
4. 제목 12~60자, intent ∈ {info,howto,compare,cost,checklist,review,news}, angle ∈ {start,howto,compare,tip,review,issue,local}
5. 실존 인물명·특정 브랜드 비특정, 기계적 곱 변형(연령대·계절 나열) 금지

**norm 사양은 `validate_batch.py`의 `stem()`/`norm()` 함수가 유일한 정의다.** Cursor가 레지스트리를 만들 때 이 코드를 그대로 임포트할 것 — 재구현 금지(서픽스 목록이 어긋나면 dedup이 갈라진다).

## 4. 배치 통합 절차
### Phase 1 (append) — 배치 1~7
```bash
cp data/topics_l3_slot2step.csv backup/topics_l3.$(date +%Y%m%d-%H%M).csv
tail -n +2 claude_l3_batchNN.csv >> data/topics_l3_slot2step.csv
python curate_l3_slot2step.py --apply-to-db
```
### Phase 2 (replace) — 배치 8~21
배치 CSV에 포함된 `l2_id` 집합에 대해 **해당 L2의 기존 행을 전부 삭제 후 신규 행 삽입**:
```python
import csv
new_rows = list(csv.DictReader(open('claude_l3_batchNN.csv', encoding='utf-8-sig')))
target_l2s = {r['l2_id'] for r in new_rows}
old = list(csv.DictReader(open('data/topics_l3_slot2step.csv', encoding='utf-8-sig')))
kept = [r for r in old if r['l2_id'] not in target_l2s]
# kept + new_rows 를 동일 스키마로 재기록 → curate → DB upsert(삭제 동기화 포함)
```
주의: DB 동기화 시 **삭제된 l3_id의 행도 DB에서 제거**되는지 확인할 것 (curate가 upsert-only라면 delete 단계를 추가 구현해야 한다 — Cursor 구현 과제 #1).

### 배치별 검증 (통합 직후 매번)
```sql
-- L2당 15행
SELECT l2_id, COUNT(*) c FROM topics_l3 GROUP BY l2_id HAVING c != 15;
-- 전역 중복 (norm 컬럼 구축 후)
SELECT norm_stem, COUNT(*) c FROM topics_l3 GROUP BY norm_stem HAVING c > 1;
-- 진행률
SELECT model, COUNT(*) FROM topics_l3 GROUP BY model;
```
기대값: 배치 N 통합 후 `claude_direct` = N×450 (배치 21 후 9,045), 전체 행수는 Phase 2 동안 일정하게 유지되다가 종료 시 9,045.

## 5. Cursor 구현 과제 (생성 외 설계 작업)
1. **DB delete 동기화** — §4 Phase 2 주의사항.
2. **전역 키워드 레지스트리 테이블** — `registry(norm_stem PK, l3_id, l2_id, created_at)`. `validate_batch.py`의 norm 함수로 9,045행 전체에서 생성. 이후 L4 행 삽입 시 이 테이블과 조인해 중복 차단하는 게 레지스트리의 존재 이유다.
3. **품질 대시보드 스크립트** — L2별 intent/angle 분포, 선두 바이그램 반복, suffix 반복을 출력하는 점검 스크립트(§3 기준). 배치 통합마다 실행해 회귀 감시.
4. **구본 아카이브** — Nemotron 210 L2 CSV와 Scout 교체분을 `archive/` 이동. 삭제하지 말 것(롤백 대비).

## 6. YMYL 규칙
- `l1_code IN ('02','03')` 행은 발행 파이프라인에서 hold 태깅 + 면책 문구 필수. L3 데이터에는 hold 컬럼이 없으므로 다운스트림에서 l1_code 조건으로 처리.
- 키워드 자체는 정보성으로 작성돼 있음(투자 권유·치료 효능 단정 없음). 제목 가공 시에도 이 톤을 깨지 말 것.

## 7. L4 준비 지침 (재생성 완료 후)
- **Scout 시드 위에서 L4를 절대 가동하지 말 것.** L4는 시드 결함을 100배로 증폭한다. 프로토타입도 claude_direct로 교체 완료된 L2에서만.
- 권장 첫 프로토타입: 09(서울 음식) 계열 1개 L2, engine=program (phase1_l3_l4_plan.csv 참조).
- L4 스키마(검토 보고서 합의안): `l4_id, l3_id, l2_id, l1_code, engine, primary_kw, norm_stem, axes_json, pattern_id, tier(warehouse/candidate/publish/hold), hold_reason, source, generated_at`
- L4 삽입 전 §5-2 레지스트리 조인 필수. 저장은 SQLite 원본 + L2 단위 샤딩 CSV export.

## 8. 금지사항
- 키워드/제목 텍스트 수정 금지 (전역 무충돌 검증 무효화). 문제 발견 시 보고 → Claude가 배치에서 수정.
- 구본 11,099행과의 merge 금지.
- curate 탈락분 임의 보충 생성 금지 (탈락 0이 정상이며, 발생 시 원인 보고).
- API 한도 우회 금지 (기존 정책 유지).

## 9. 파일 인벤토리
| 파일 | 용도 |
|---|---|
| `claude_l3_batch01.csv` | 배치 1 (450행, Phase 1 append) |
| `topics_l2_v2_final.csv` | 개편 후 L2 마스터 704개 (검증기도 이 파일 기준) |
| `l2_redesign_map.csv` | L2 개편 작업 지시서 (선행 단계) |
| `generation_batch_plan.csv` | 배치 2~24 대상 L2 확정본 |
| `validate_batch.py` | norm/stem 유일 정의 + 배치 검증기 (레지스트리 구현 시 임포트) |
| `claude_l3_batchNN.csv` | 이후 배치 (턴마다 추가 납품) |
