# L3 slot_2step 실행 런북 — 다른 Cursor·환경에서 재개용

> 작성일: 2026-06-08 (갱신: 2026-06-08 본생산·파일 분리)  
> 목적: L3 생성(slot_2step) 파일럿·본생산을 **다른 Cursor 세션**에서도 동일하게 돌릴 수 있도록 명령·한도·산출물·판단 기준을 한곳에 정리  
> 관련: [`data2-taxonomy-master-plan.md`](./data2-taxonomy-master-plan.md), [`data2-taxonomy-handoff.md`](./data2-taxonomy-handoff.md)

---

## 1. 한 줄 요약

| 항목 | 내용 |
|------|------|
| **채택 파이프라인** | Scout 슬롯 → Gemini dedup → Scout 채우기 → 코드 검증 (**slot_2step**) |
| **L2당 L3 cap** | **12~15** (50개 고정 ❌) |
| **일 API 한도** | Scout **1,500** (키 2개 합산, 키당 ~500~700) · Gemini lite **500** |
| **603 L2 전체** | L3 **~8,350개** · Scout **~2,900** · Gemini **~1,000** · **2일** 분산 |
| **한도 풀 사용 시 실제 실행** | **약 2~2.5시간** (1500 Scout + 500 Gemini 몰아서) |
| **본생산 산출물** | **`data2/topics_l3_slot2step.csv`** (구 `topics_l3.csv`와 분리) |

---

## 2. 사전 준비

### 2.1 저장소·Python

```bash
cd /path/to/L1          # 예: ~/개인프로젝트/L1
python3 -m venv .venv   # 없을 때만
.venv/bin/pip install -r requirements.txt
```

**반드시** `.venv/bin/python` 사용 (시스템 Python에 `dotenv` 없음).

### 2.2 환경변수 (`.env`)

| 변수 | 용도 |
|------|------|
| `GROK_API_KEY` 또는 `GROQ_API_KEY` | Groq Scout (키 1) |
| `GROK_API_KEY_2` | Groq Scout (키 2, 로테이션) |
| `GEMINI_API_KEY` | Gemini 3.1 flash-lite (dedup) |

키는 커밋하지 말 것. 다른 머신에서는 `.env`를 직접 복사하거나 동일 변수를 설정.

### 2.3 모델 (코드 고정값)

| 역할 | 모델 ID | 파일 |
|------|---------|------|
| L3 슬롯·채우기 | `meta-llama/llama-4-scout-17b-16e-instruct` | `scripts/groq_client.py` |
| L3 슬롯 dedup | `gemini-3.1-flash-lite` | `scripts/gemini_client.py` |

### 2.4 입력 데이터 (이미 있어야 함)

| 파일 | 설명 |
|------|------|
| `data2/topics_l2.csv` | L2 **603** |
| `data2/seed/topics_l1.csv` | L1 14 |
| `data2/brief.txt` | SEO 브리프 (선택) |
| `data2/pilot/phase1_l3_l4_plan.csv` | L2 타입·cap 참고 (선택) |

### 2.5 현재 L3 DB 상태 (2026-06-08)

| 파일 | L2 | L3 행 | model | 용도 |
|------|-----|-------|-------|------|
| **`topics_l3_slot2step.csv`** | **84** | **1,669** | `slot_2step` | **메인** — 본생산 계속 여기에 추가 |
| `topics_l3.csv` | 210 | 11,099 | Nemotron/Scout | 구본 아카이브 (섞지 않음) |
| `test/l3_review_pilot/l3_review_pilot.csv` | 20 | 277 | — | 사람 검수 샘플 |

```
본생산 잔여: 309 L2  (신규 393 − slot_2step 84)
              구본 210 L2는 topics_l3.csv에만 존재 (재생성 대상 아님)
```

**slot_2step L1 분포 (84 L2)**: 01연예 25 · 02경제 31 · 03건강 10 · 04패션 14 · 05스포츠 4

---

## 3. 스크립트 역할

| 스크립트 | 용도 | 상태 |
|----------|------|------|
| `scripts/pilot_l3_review_batch.py` | **파일럿 + slot_2step 본생산** | ✅ **메인** |
| `scripts/test_l3_diversity.py` | A/B (Nemotron vs baseline vs slot_2step) | ✅ 사용 |
| `scripts/generate_taxonomy_l3.py` | 구 방식 (Nemotron 50/L2) | ⏸ 사용 안 함 |
| `scripts/dedup_l3_topics.py` | 구본 `topics_l3.csv` curate (API 없음) | ✅ (구본용) |

### `pilot_l3_review_batch.py` 주요 옵션

| 옵션 | 설명 |
|------|------|
| (기본) | `DEFAULT_L2` 20개 → `l3_review_pilot.csv` |
| `--missing-l2` | slot2step·구본 모두 없는 L2 전체 (현재 **309**) |
| `--merge-topics` | 결과를 **`topics_l3_slot2step.csv`**에 병합 |
| `--resume` | `l3_slot2step_prod_checkpoint.json` 이어하기 |
| `--max-scout N` | Scout 호출 상한 (호출 수, TPD와 별개) |
| `--max-gemini N` | Gemini dedup 호출 상한 |
| `--l2 ID` | 특정 L2만 (반복 가능) |
| `--count N` | L2당 목표 L3 (기본 15) |

> **본생산 = `pilot_l3_review_batch.py --missing-l2 --merge-topics`**  
> `generate_taxonomy_l3.py` slot_2step 통합은 추후 (현재 불필요).

---

## 4. 파일럿 — 사람 리뷰용 L3 생성

### 4.1 목적

- L3 **~300개** 샘플 생성 → CSV에서 **중복·merge** 직접 판정
- 본생산 전 품질·cap·슬롯축 확정

### 4.2 실행

```bash
cd /path/to/L1

# 기본: L2 20개 × 15개 (타입별 대표)
.venv/bin/python scripts/pilot_l3_review_batch.py

# 중단 후 이어하기 (checkpoint 사용)
.venv/bin/python scripts/pilot_l3_review_batch.py --resume

# 특정 L2만 / 개수 변경
.venv/bin/python scripts/pilot_l3_review_batch.py \
  --l2 08-skincare-routine --l2 13-pet-hospital --count 15
```

### 4.3 기본 L2 20개 (`DEFAULT_L2`)

`11-travel-essentials`, `11-accommodation-recommendations`, `11-domestic-travel`, `11-overseas-travel`, `11-travel-tips`, `11-family-trip`, `09-seoul-food`, `09-busan-food`, `09-gangnam-food`, `09-cafe-recommend`, `09-solo-food`, `09-date-course`, `10-korean-recipe`, `10-air-fryer-recipes`, `10-cooking-tips`, `08-skincare-routine`, `08-skin-type-care`, `13-cat-food`, `13-dog-health`, `13-pet-hospital`

### 4.4 산출물

| 파일 | 설명 |
|------|------|
| `data2/test/l3_review_pilot/l3_review_pilot.csv` | **리뷰용 메인** |
| `data2/test/l3_review_pilot/l3_review_pilot_report.json` | L2별 수락·API 집계 |
| `data2/test/l3_review_pilot/checkpoint.json` | `--resume` 체크포인트 |

### 4.5 파일럿 실측 (2026-06-08)

| 항목 | 값 |
|------|-----|
| L3 | **277** / 목표 300 |
| Scout | **96**회 |
| Gemini | **33**회 |
| 소요 | **~9.5분** |
| L2당 평균 | Scout **4.8** · Gemini **1.65** · L3 **13.9** |

수락 부족 L2: `08-skincare-routine` (5/15), `13-pet-hospital` (3/15) — `--l2`로 재실행 가능.

### 4.6 CSV 리뷰 방법

당신이 채울 컬럼:

| 컬럼 | 값 |
|------|-----|
| `your_verdict` | `ok` / `merge` / `drop` |
| `merge_group` | 같은 글 묶음 ID (`A1`, `A2` …) |
| `note` | 메모 |

참고용 `auto_flag`: `ends_추천`, `prep_checklist`, `duration_variant`, `travel_gadget_template`

**판단 기준:** 「이 키워드를 **별도 URL·별도 글**로 열겠는가?」

---

## 5. A/B 테스트 (선택)

```bash
.venv/bin/python scripts/test_l3_diversity.py
.venv/bin/python scripts/test_l3_diversity.py --l2 11-travel-essentials --count 15
```

산출: `data2/test/l3_diversity/l3_diversity_report.json`

| 방식 | 체감 |
|------|------|
| Nemotron 50 (구) | stem 96%지만 **추천 70%** — 중복 |
| Scout baseline 15 | 허브 반복 |
| **slot_2step** | 의도 축 분리 ✅ — **채택** |

---

## 6. 본생산 계획 (603 L2)

### 6.1 규모

| 항목 | 추정 |
|------|------|
| L3 총량 | **~8,350** (L2당 ~14, cap 15) |
| 신규 393 L2만 | **~5,500** L3 |
| Scout 총 | **~2,900** |
| Gemini 총 | **~1,000** |

### 6.2 일정 (한도 분산)

| 일차 | Scout | Gemini | L2 처리 | L3 |
|------|-------|--------|---------|-----|
| 1일차 | ~1,500 | ~500 | ~300 L2 | ~4,200 |
| 2일차 | ~1,400 | ~500 | ~303 L2 | ~4,150 |
| **합계** | ~2,900 | ~1,000 | **603** | **~8,350** |

한도를 **한 번에** 쓰면 wall-clock **~2~2.5시간**. 2일은 **일일 상한** 때문.

### 6.3 권장 실행 순서

1. **Phase1·geo L2** 먼저 (L4 program 비중 큼)
2. **hold L2**(연예·경제·교양)는 cap **5~8**로 Scout 절약
3. 키 로테이션: `GROK_API_KEY` → `GROK_API_KEY_2` (Groq 429 시 자동 시도)
4. `--resume`으로 일일 분산

### 6.4 본생산 명령 (현재)

```bash
cd /path/to/L1

# Linux/macOS
.venv/bin/python scripts/pilot_l3_review_batch.py \
  --missing-l2 --merge-topics --resume \
  --max-scout 1500 --max-gemini 500

# Windows PowerShell
.venv\Scripts\python.exe scripts/pilot_l3_review_batch.py `
  --missing-l2 --merge-topics --resume `
  --max-scout 1500 --max-gemini 500
```

**`--merge-topics`는 `topics_l3_slot2step.csv`에만 씀.** 구 `topics_l3.csv`는 변경하지 않음.

### 6.5 본생산 산출물·체크포인트

| 파일 | 설명 |
|------|------|
| `data2/topics_l3_slot2step.csv` | slot_2step L3 **메인 DB** |
| `data2/topics_l3_slot2step.json` | JSON mirror |
| `data2/manifest_l3_slot2step.json` | 메타 |
| `data2/state/l3_slot2step_prod_checkpoint.json` | L2별 `--resume` 체크포인트 |
| `data2/state/taxonomy_slot2step.db` | SQLite (slot_2step 전용) |

### 6.6 실측 한도 (2026-06-08 Free tier)

| 한도 | 값 | 비고 |
|------|-----|------|
| Groq **TPD** | **500,000 토큰/키/일** | **실제 병목** — L2 ~40개면 키1 소진 |
| Groq **RPM** | 30/분 | 45~180초 재시도 후 통과 (정상) |
| Gemini dedup | ~500/일 | L2당 1~2회 |
| 키 로테이션 | `GROQ_API_KEY` → `GROK_API_KEY_2` | 키1 TPD 소진 후 키2 (~100만 TPD/일 합산) |

`--max-scout 1500`은 **API 호출 횟수** 상한이며 TPD를 막지 못함.

---

## 7. slot_2step 파이프라인 (구현 참고)

```
1. Scout: 의도 슬롯 N개 생성 (l2_type별 축 참고)
2. Gemini: stem·기간·허브 중복 슬롯 제거
3. Scout: 슬롯 1개당 L3 1개 (배치)
4. 코드: stem / suffix cap / intent_angle cap 검증
5. (부족 시) 슬롯 재생성 + fill 라운드 (최대 4회)
```

핵심 로직: `scripts/test_l3_diversity.py`  
파일럿 래퍼: `scripts/pilot_l3_review_batch.py`

### L2 타입별 cap 가이드

| l2_type | L3 cap | L4 엔진 |
|---------|--------|---------|
| hub | 10~12 | llm |
| geo / geo_local | 12~15 | program |
| howto | 12~15 | llm |
| cuisine / compare | 12~18 | hybrid / program |
| hold | 5~8 | 창고만 |

상세: `data2/pilot/phase1_l3_l4_plan.csv`

---

## 8. 트러블슈팅

| 증상 | 대응 |
|------|------|
| `ModuleNotFoundError: dotenv` | `.venv/bin/python` 또는 `.venv\Scripts\python.exe` |
| `GROQ_API_KEY 가 .env 에 없습니다` | `.env`에 `GROK_API_KEY` 확인 |
| Groq 429 **RPM** (`Limit 30`) | 재시도 후 통과. L2 간 `sleep` 늘리면 완화 |
| Groq 429 **TPD** (`Limit 500000`) | 키1 소진 → 키2 자동 시도 → 둘 다 소진 시 **내일 `--resume`** |
| Gemini **403 blocked** | [Google AI Studio](https://aistudio.google.com/apikey)에서 새 키 발급 · `.env` 교체 |
| Gemini 500 초과 | 당일 중단 · 다음날 `--resume` |
| L2당 수락 11/15 등 부족 | `--l2 ID --resume` 재실행 |
| `UnicodeEncodeError` (Windows) | `$env:PYTHONIOENCODING="utf-8"` 설정 |

중단 시 `l3_slot2step_prod_checkpoint.json` / `taxonomy_slot2step.db` / `topics_l3_slot2step.csv`는 유지됨.

---

## 9. 후처리 (API 없음)

**구본** (`topics_l3.csv`) curate:

```bash
.venv/bin/python scripts/dedup_l3_topics.py
```

| 출력 | 용도 |
|------|------|
| `data2/topics_l3_curated.csv` | publish / edit (구본) |
| `data2/topics_l3_hold.csv` | hold |
| `data2/topics_l3_dropped.csv` | merge·탈락 |

**slot_2step** (`topics_l3_slot2step.csv`) 전용 curate 스크립트는 **미구현** — 추후 `dedup_l3_slot2step.py` 또는 동 스크립트 `--input` 옵션 추가 예정.

---

## 10. 다른 Cursor에서 시작할 때 체크리스트

```
[ ] repo clone / pull
[ ] .env 에 GROK_API_KEY, GROK_API_KEY_2, GEMINI_API_KEY
[ ] .venv + pip install -r requirements.txt
[ ] data2/topics_l2.csv 603행 확인
[ ] data2/topics_l3_slot2step.csv 현재 행 수 확인
[ ] §6 본생산: --missing-l2 --merge-topics --resume
[ ] 실행 후 topics_l3_slot2step.csv 행 수 증가 확인
```

### AI에게 넘길 한 줄 프롬프트 예시

> `docs/data2-l3-slot2step-runbook.md` 보고 L3 slot_2step 본생산을 `--missing-l2 --merge-topics --resume`으로 실행해줘. 산출물은 `topics_l3_slot2step.csv`에만 쌓아.

---

## 11. 관련 파일 인덱스

```
docs/data2-l3-slot2step-runbook.md     ← 이 문서
docs/data2-taxonomy-master-plan.md
docs/data2-taxonomy-handoff.md

scripts/pilot_l3_review_batch.py       ← 파일럿·리뷰 CSV
scripts/test_l3_diversity.py           ← A/B·slot_2step 코어
scripts/generate_taxonomy_l3.py        ← 본생산 (구방식, 통합 예정)
scripts/groq_client.py
scripts/gemini_client.py
scripts/dedup_l3_topics.py

data2/topics_l3_slot2step.csv          ← slot_2step 메인 DB
data2/topics_l3.csv                    ← Nemotron 구본 (아카이브)
data2/state/l3_slot2step_prod_checkpoint.json
data2/state/taxonomy_slot2step.db
data2/test/l3_review_pilot/            ← 파일럿 검수 샘플
data2/pilot/phase1_l3_l4_plan.csv
data2/topics_l2.csv
```
