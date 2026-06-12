# L3 claude_direct 재생성 — 세션 인계 문서

> **최종 갱신**: 2026-06-12 (배치 8 통합 완료)  
> **다른 세션에서 이 문서만 읽고 이어가면 됨.**  
> **역할**: Claude = L3 키워드 생성(배치 납품) / Cursor = 통합·검증·DB 동기화. **Cursor는 키워드 생성·수정 금지.**

**관련 문서**

| 문서 | 용도 |
|------|------|
| **본 문서** | 진행 현황·통합 절차·다음 작업 (최신) |
| [`cursor-master-guide.md`](./cursor-master-guide.md) | Claude 측 단일 기준 (확장안 704 L2) |
| [`data2-l3-claude-regen-plan.md`](./data2-l3-claude-regen-plan.md) | 전체 로드맵·L4 준비 |
| [`data2-system.md`](./data2-system.md) | 시스템 전체 개요 |

---

## 0. 한 줄 요약

Claude가 **704 L2 × 15 = 10,560행** L3를 24배치로 재생성 중.  
Cursor는 배치 CSV를 받아 **`integrate_claude_l3.py`로 통합**하고 DB를 동기화한다.

**지금**: 배치 **1~8 완료** (240 L2, claude_direct 3,600행). **다음은 배치 9.**

---

## 1. 목표 종료 상태 (End State)

| 항목 | 목표 | 현재 (2026-06-12) |
|------|------|-------------------|
| L1 | 18 (14 + 생활/육아/커리어/게임) | **18** ✅ |
| L2 | **704** (확장안 A+B) | **635** (69 신규 L2 남음) |
| L3 | **10,560** (전부 `claude_direct`) | **9,084** (claude 3,600 + Scout 5,484) |
| 전역 norm 중복 | 0 | claude 구간 **0** ✅ |
| 구본 Nemotron | 아카이브·미사용 | `data2/topics_l3.csv` (변경 없음) |

### L2 재설계 (확장안, `l2_redesign_map.csv`)

- **merge 40** 삭제 (최초 배치 1~3 통합 시 Scout 321행 아카이브)
- **rename 8** — `l2_id` 불변, `name_ko`만 변경
- **move 4** — L1 15~18로 이동 (확장안, `l1_code` 보정 완료)
- **신규 141** — 배치마다 description 오면 `topics_l2.csv`에 추가 (현재 **72개** description 누적, **52개** L2 추가됨)

---

## 2. 현재 데이터 스냅샷

### 마스터 파일 (canonical)

```
data2/
├── seed/topics_l1.csv              # 18 L1
├── topics_l2.csv                   # 635 L2  ← 목표 704
├── topics_l3_slot2step.csv         # 9,084 L3  ← 목표 10,560
├── topics_l3_slot2step.json        # CSV 미러
├── manifest_l3_slot2step.json      # 메타
└── state/taxonomy_slot2step.db     # SQLite
```

### model 분포

| model | 행 수 | L2 수 | 비고 |
|-------|-------|-------|------|
| `claude_direct` | **3,600** | **240** | 배치 1~8 완료 |
| `slot_2step` | 5,484 | 371 | **배치 12~24 replace 대상** (교체 예정) |

### L2당 15 미만 (55개)

- 전부 **Scout 잔여** L2 (curate 후 14개 등). claude_direct L2는 **전부 15개** ✅
- replace 단계(배치 12~)에서 Scout 행이 claude로 교체되면 해소됨

### manifest (`data2/manifest_l3_slot2step.json`)

```json
{
  "l2_count": 635,
  "l3_total": 9084,
  "l3_per_l2_target": 15,
  "l2_done": 556,
  "l2_partial": 55,
  "model": "mixed"
}
```

---

## 3. 배치 통합 이력

| 배치 | 상태 | L2/배치 | L1 | 누적 claude 행 | 누적 claude L2 | 신규 L2 추가 | 비고 |
|------|------|---------|-----|----------------|----------------|--------------|------|
| 1 | ✅ | 30 | 01,02 | 450 | 30 | 22 | 최초 통합 + merge 40 제거 |
| 2 | ✅ | 30 | 02,03,04 | 900 | 60 | — | |
| 3 | ✅ | 30 | 04,05 | 1,350 | 90 | — | |
| 4 | ✅ | 30 | 05,06,07 | 1,800 | 120 | +11 | |
| 5 | ✅ | 30 | 07,08 | 2,250 | 150 | +7 | |
| 6 | ✅ | 30 | 08,09,10 | 2,700 | 180 | +8 | |
| 7 | ✅ | 30 | 10,11 | 3,150 | 210 | +12 | fill 단계 마무리 |
| 8 | ✅ | 30 | 11,12,13 | **3,600** | **240** | +12 | topics_l2 → 635 |
| **9** | ⏳ | 30 | 13,14 | — | — | — | **다음** |
| 10~11 | ⏳ | fill | 14~18 | — | — | — | 신규 L1 영토 |
| 12~24 | ⏳ | fill+replace | — | — | — | — | replace 본격 (371 Scout L2) |

> 배치 7·8 누적 claude L2: 210→240 (표 상단 누적은 배치별 +30).  
> **남은**: 배치 9~24 = **16배치 / 464 L2** (fill 93 + replace 371).

### staging 위치 (수신 파일)

```
data2/incoming/claude_l3/
├── claude_l3_batch01.csv … batch08.csv   # 통합 완료
├── l2_new_descriptions_b2.csv … b8.csv   # 신규 L2 description 누적
├── l2_redesign_map.csv                   # L2 재설계 지시서
├── generation_batch_plan.csv             # 배치 2~24 대상 L2 확정
└── replacement_l2_order.csv              # (구) replace 순서 참고
```

---

## 4. 배치 도착 시 통합 절차 (복사용)

**프로젝트 루트**에서 실행 (`/Users/simsimi/개인프로젝트/L1`).

### Step 1 — 파일 staging

```bash
cd "/Users/simsimi/개인프로젝트/L1"

# 배치 CSV (NN = 09, 10, …)
cp "/Users/simsimi/Downloads/files (N)/claude_l3_batchNN.csv" \
   data2/incoming/claude_l3/

# 신규 L2 description (함께 왔을 때만)
cp "/Users/simsimi/Downloads/files (N)/l2_new_descriptions.csv" \
   data2/incoming/claude_l3/l2_new_descriptions_bNN.csv
```

### Step 2 — dry-run (오류 0 확인)

```bash
.venv/bin/python scripts/integrate_claude_l3.py \
  --batches data2/incoming/claude_l3/claude_l3_batchNN.csv \
  --dry-run
```

기대 출력: `[검증] 오류 0 경고 0`

### Step 3 — 반영

```bash
.venv/bin/python scripts/integrate_claude_l3.py \
  --batches data2/incoming/claude_l3/claude_l3_batchNN.csv
```

- 자동 백업: `backup/topics_l{1,2,3}_slot2step.*.csv`
- 리포트: `data2/reports/claude_l3_integration_report.json`

### Step 4 — DB 동기화 (필수, 매 배치 후)

`integrate_claude_l3.py`는 **CSV만 갱신**한다. DB는 아래를 매번 실행:

```bash
.venv/bin/python -c "
import sys; sys.path.insert(0,'scripts')
import csv
from pathlib import Path
from collections import Counter
from taxonomy_state import L3_FIELDNAMES, connect, upsert_topics, export_artifacts, _sync_l2_progress

ROOT=Path('.')
rows=[{k:r.get(k,'') for k in L3_FIELDNAMES}
      for r in csv.DictReader((ROOT/'data2/topics_l3_slot2step.csv').open(encoding='utf-8-sig'))]
keep={r['l3_id'] for r in rows}
l2n=sum(1 for _ in csv.DictReader((ROOT/'data2/topics_l2.csv').open(encoding='utf-8-sig')))
with connect(ROOT/'data2/state/taxonomy_slot2step.db') as conn:
    ph=','.join('?' for _ in keep)
    conn.execute(f'DELETE FROM l3_topics WHERE l3_id NOT IN ({ph})', list(keep))
    upsert_topics(conn, rows)
    _sync_l2_progress(conn, target_count=15)
    export_artifacts(
        conn,
        csv_path=ROOT/'data2/topics_l3_slot2step.csv',
        json_path=ROOT/'data2/topics_l3_slot2step.json',
        manifest_path=ROOT/'data2/manifest_l3_slot2step.json',
        l2_count=l2n, count_target=15, model='mixed',
        l2_processed=len(Counter(r['l2_id'] for r in rows)),
        interrupted=False,
        state_db='data2/state/taxonomy_slot2step.db',
    )
c=Counter(r['model'] for r in rows)
cl=len(set(r['l2_id'] for r in rows if r['model']=='claude_direct'))
print('L3', len(rows), 'L2', l2n, 'model', dict(c), 'claude L2', cl)
"
```

### Step 5 — 진행 확인

```bash
.venv/bin/python -c "
import csv
from collections import Counter
from pathlib import Path
rows=list(csv.DictReader(Path('data2/topics_l3_slot2step.csv').open(encoding='utf-8-sig')))
print('model', Counter(r['model'] for r in rows))
print('claude L2', len(set(r['l2_id'] for r in rows if r['model']=='claude_direct')))
bad=[(k,v) for k,v in Counter(r['l2_id'] for r in rows).items() if v!=15]
print('L2 != 15:', len(bad))
"
```

**기대값 (배치 N 완료 후)**: `claude_direct` = N × 450행, claude L2 = N × 30 (배치 24 후 10,560 / 704).

---

## 5. `integrate_claude_l3.py` 동작 요약

1. `backup/`에 L1·L2·L3 백업
2. `l2_redesign_map.csv` 적용
   - rename `name_ko` / move `l1_code` / merge L2 삭제 (최초 1회만 merge 제거)
   - **신규 L2**: 이번 배치에 등장 + `l2_new_descriptions_b*.csv`에 description 있는 것만 추가
3. 배치 L2의 **기존 행 전부 제거** 후 claude 행 삽입 (fill·replace 동일 로직)
4. merge Scout 행 아카이브 → `data2/archive/scout_replaced/`
5. 검증 실패 시 **파일 미반영** + exit 1

### 검증 항목

- 배치 L2당 정확히 15행
- `claude_direct` 전역 `norm()` 중복 0 (`scripts/validate_batch.py`의 `norm` 임포트)
- `l3_id` 유일, intent/angle 유효
- 배치 L2가 `topics_l2.csv`에 존재

### 옵션

```bash
--batches PATH [PATH ...]   # 특정 배치만 (권장)
--dry-run                   # 검증만
--scenario ab               # 기본: 확장안 704 (L1 15~18 포함)
--scenario a                # 보수안 647
```

---

## 6. 데이터 계약 (불변식)

스키마 (`L3_FIELDNAMES`):

```
l3_id, l2_id, l1_code, l3_slug, focus_keyword, title_ko,
search_intent, topic_angle, description, model, generated_at
```

| 규칙 | 내용 |
|------|------|
| L2당 | 정확히 15행 |
| l3_id | `{l2_id}-{slug}` 유일 |
| model | `claude_direct` |
| 전역 중복 | `norm()` = suffix-strip + 토큰 정렬, claude 행 간 0 |
| intent | info, howto, compare, cost, checklist, review, news |
| angle | start, howto, compare, tip, review, issue, local |
| 다양성 | info ≤ 9~10, suffix ≤ 2, 선두 토큰 ≤ 5, 바이그램 ≤ 2 |

**`norm`/`stem`은 `scripts/validate_batch.py` 단일 정의. 재구현 금지.**

---

## 7. fill vs replace 단계

`generation_batch_plan.csv`의 `phase` 컬럼 기준.

| 구간 | 배치 | phase | 동작 |
|------|------|-------|------|
| fill | 2~11 (일부) | `fill` | Scout 없던 L2에 claude **추가** (행 수 증가) |
| replace | 12~24 | `replace` | Scout 15행 **삭제** → claude 15행 삽입 (행 수 유지, model 전환) |

- 현재 Scout-only L2: **371개** (replace 대기)
- 배치 9~11: fill (신규 L1 15~18 영토 포함)
- **배치 12부터**: replace 27 + fill 3 혼합 → 이후 Scout 행이 줄어듦

통합 스크립트는 두 phase 모두 동일하게 처리한다 (배치 L2 기존 행 제거 → claude 삽입).

---

## 8. 금지사항

- 키워드/제목 **텍스트 수정 금지** → 문제 시 Claude에 배치 수정 요청
- `topics_l3.csv`(Nemotron 구본 11,099)와 **merge 금지**
- curate로 임의 보충 생성 금지
- API 한도 우회·계정 쪼개기 제안 금지

---

## 9. 알려진 이슈·미완 과제

| # | 항목 | 상태 | 조치 |
|---|------|------|------|
| 1 | DB sync가 통합 스크립트 밖 | 매 배치 수동 실행 | `--apply-to-db` 옵션 추가 예정 |
| 2 | 전역 키워드 레지스트리 | 미구현 | `build_kw_registry.py` (L4 중복 차단용) |
| 3 | 품질 대시보드 | 미구현 | 배치마다 intent/angle·바이그램 회귀 감시 |
| 4 | 구본 Nemotron 아카이브 | 미이동 | `archive/legacy_nemotron/` |
| 5 | Scout partial 55 L2 | replace 전 정상 | 배치 12+ 교체 후 0 기대 |

---

## 10. 배치 24 완료 후

1. `slot_2step` 행 **0** — 전량 `claude_direct` 10,560
2. `topics_l2.csv` **704** L2
3. 구본·Scout 잔여 → `archive/` (삭제 금지)
4. **L4 fan-out** 착수 (Scout 시드 위에서 L4 가동 금지 — claude 완료 후만)
5. Phase1 25 L2 program 프로토타입 (`phase1_l3_l4_plan.csv`)

---

## 11. 다음 세션 체크리스트

배치 9 수령 시:

- [ ] `claude_l3_batch09.csv` + `l2_new_descriptions.csv` staging
- [ ] `integrate_claude_l3.py --dry-run`
- [ ] 통합 반영
- [ ] DB sync (§4 Step 4)
- [ ] claude_direct = 4,050행 / claude L2 = 270 확인
- [ ] 본 문서 §3 배치表 갱신

---

## 12. 파일·스크립트 인덱스

| 경로 | 용도 |
|------|------|
| `scripts/integrate_claude_l3.py` | **배치 통합 메인** |
| `scripts/validate_batch.py` | norm/stem 정의 + Claude 측 검증기 |
| `docs/cursor-master-guide.md` | Claude↔Cursor 역할·End State |
| `data2/incoming/claude_l3/` | 수신 배치 staging |
| `data2/archive/scout_replaced/` | merge·replace된 Scout 행 |
| `backup/` | 통합 직전 자동 백업 |
| `data2/reports/claude_l3_integration_report.json` | 마지막 통합 리포트 |

---

*갱신 규칙: 배치 통합할 때마다 §2 스냅샷·§3 배치表·§11 체크리스트를 먼저 수정.*
