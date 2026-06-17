# L3 대량 생성 — 서버 테스트·운영 인수인계

> **작성일: 2026-06-17**  
> **목적:** 새 Cursor/새 서버 세션에서 L3 bulk(5만 키워드 → L3 메타)를 바로 이어갈 수 있도록 정리  
> **선행 작업:** L2 네이버 확장 완료 → `outputs/l2_candidates_gte100_depth1.csv` (50,017행) 확정  
> **관련 구 문서:** `data2-taxonomy-handoff.md`, `data2-l3-claude-handoff.md` (slot2step·Claude 1만 L3 시대 — **본 bulk와 별 트랙**)

---

## 0. 한 줄 요약

| 항목 | 내용 |
|------|------|
| **목표** | 검색 키워드 50,017개 → L3 메타(title, intent, angle, desc) 생성 |
| **입력** | `outputs/l2_candidates_gte100_depth1.csv` + `data2/topics_l2.csv` |
| **기존 L3** | `data2/topics_l3_slot2step.csv` 10,560행 — **생성 입력 아님**, merge 때만 사용 |
| **모델** | YMYL(02·03) → Gemini / 나머지 → Groq 8b → 실패 시 Scout |
| **실행** | 서버 cron + `--resume` + 일일 API 한도 |
| **레거시** | `legacy/` (~450MB) — gitignore, 서버에 **불필요** |

---

## 1. 프로젝트 상태 (2026-06-17)

### 1-1. 택소노미 canonical (`data2/`)

| 파일 | 행 수 | 용도 |
|------|-------|------|
| `data2/seed/topics_l1.csv` | 18 L1 | 참조 |
| `data2/topics_l2.csv` | 704 L2 | **L3 bulk 필수** — `l2_id` 매핑 |
| `data2/topics_l3_slot2step.csv` | 10,560 L3 | claude_direct 본선 — **merge 전용** |
| `data2/topics_l3_slot2step.json` | CSV 미러 | 선택 |
| `data2/manifest_l3_slot2step.json` | 메타 | 선택 |

### 1-2. L3 bulk 입력 (`outputs/`)

| 파일 | 행 수 | 조건 |
|------|-------|------|
| `outputs/l2_candidates_gte100_depth1.csv` | **50,017** | depth=1, monthly_total≥100, norm dedup |

**입력 CSV 주요 컬럼**

```
l1_code, candidate_keyword, monthly_total, source_seed, depth, ...
```

**키워드 구성 (2026-06-17 기준)**

| 구분 | 건수 |
|------|------|
| 전체 | 50,017 |
| YMYL (L1 02·03) | 6,999 → **Gemini** |
| 나머지 | 43,018 → **8b** |
| hi (월 1,000+) | 18,649 → `l3_expanded_hitier.csv` |
| longtail | 31,368 → `l3_expanded_longtail.csv` |

### 1-3. 레거시 정리 (`legacy/` — gitignore)

2026-06-17에 운영 불필요 파일 일괄 이동. **삭제하지 않음**, 로컬 참고용.

```
legacy/
├── data/                  # 구 v1 taxonomy + 본문 1만 편
├── data2/
│   ├── archive/           # Nemotron·Scout·백업·로그
│   ├── answers/           # 본문 파일럿
│   ├── pilot/             # Phase1 계획
│   ├── incoming/          # L2 재설계 참조
│   ├── reports/
│   ├── state/             # slot2step SQLite
│   ├── claude_l3_all_excel.csv
│   ├── brief.txt
│   └── seed/blog_launch_priority.csv
└── outputs/               # L2 확장 중간 산출 (api_raw, candidates, experiment)
```

---

## 2. 모델 전략 (합의·코드 반영 완료)

### 2-1. 1차 라우팅

| L1 | 분야 | 모델 | batch |
|----|------|------|-------|
| 02, 03 | 금융·건강 (YMYL) | **Gemini** `gemini-3.1-flash-lite` | **10** |
| 01, 04~18 | 나머지 | **Groq 8b** `llama-3.1-8b-instant` | **10** |

> **batch 25 사용 금지** — 실험에서 파싱 붕괴 확인 (`experiment_l3_*` → legacy)

### 2-2. 재시도 (retry_stage)

| stage | YMYL (02·03) | 일반 |
|-------|--------------|------|
| 1 | Gemini batch 5 | 8b batch 5 |
| 2 | Gemini single | 8b single |
| 3 | Scout batch 5 | Scout batch 5 |
| 4+ | 포기 (abandoned) | 포기 |

### 2-3. 일일 API 한도 (기본값)

| 모델 키 | 한도/일 | 대략 처리량 |
|---------|---------|-------------|
| 8b | 10,000 calls | ~10만 키워드(이론) / 실제 필요 ~4,302 calls |
| gemini | 500 calls | ~5,000 키워드(이론) / YMYL 필요 ~700 calls |
| scout | 800 calls | 재시도·rescue |

한도 도달 시 checkpoint 저장 후 **정상 종료** → 다음 실행 `--resume`.

### 2-4. 산출 L3 필드

```
l3_id, l2_id, l1_code, l3_slug,
focus_keyword, title_ko, search_intent, topic_angle,
description, model, generated_at,
monthly_total, tier, meta_source
```

- `tier`: `hi` (monthly_total≥1000) / `lt`
- `meta_source`: `primary_8b`, `primary_gemini`, `retry_s1_8b` 등

### 2-5. 이 bulk의 역할 (중요)

- **L3 메타(제목·intent 등)만 생성** — 본문·SEO 본문은 **별도 단계**
- 8b 결과는 **최종 품질이 아닌 힌트** — YMYL은 Gemini 필수
- 본문 전량 Gemini (~500 calls/일) 시 5만 본문 ≈ **100일** 병목 (향후 과제)

---

## 3. 서버 배포 — 최소 파일 목록

```
L1/                          # 프로젝트 루트
├── .env                     # API 키 (git 제외)
├── requirements.txt
├── data2/
│   ├── seed/topics_l1.csv
│   ├── topics_l2.csv
│   └── topics_l3_slot2step.csv   # merge 때만 필수, 생성만 할 땐 선택
├── outputs/
│   └── l2_candidates_gte100_depth1.csv
└── scripts/
    ├── generate_l3_expanded.py
    ├── l3_bulk_config.py
    ├── gemini_client.py
    ├── groq_client.py
    ├── merge_l3_expanded.py
    └── run_l3_daily.sh
```

**용량:** ~15–20MB (결과 CSV 제외)

### rsync 예시 (로컬 → 서버)

```bash
rsync -avz --relative \
  ./.env \
  ./requirements.txt \
  ./data2/seed/topics_l1.csv \
  ./data2/topics_l2.csv \
  ./data2/topics_l3_slot2step.csv \
  ./outputs/l2_candidates_gte100_depth1.csv \
  ./scripts/generate_l3_expanded.py \
  ./scripts/l3_bulk_config.py \
  ./scripts/gemini_client.py \
  ./scripts/groq_client.py \
  ./scripts/merge_l3_expanded.py \
  ./scripts/run_l3_daily.sh \
  user@server:/path/to/L1/
```

> `legacy/`는 올리지 않음.

---

## 4. 서버 초기 설정

```bash
cd /path/to/L1

# Python venv
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

# .env 확인
grep -E '^(GEMINI|GROQ|GROK)' .env   # 값은 출력하지 말 것

# 스크립트 실행 권한
chmod +x scripts/run_l3_daily.sh
```

### `.env` 필수 변수

| 변수 | 용도 |
|------|------|
| `GEMINI_API_KEY` | YMYL L3 (02·03) |
| `GROQ_API_KEY` 또는 `GROK_API_KEY` | 8b + Scout |

선택 (Groq 키 로테이션):

```
GROK_API_KEY_2=...
GROK_API_KEY_3=...
GROQ_USE_KEYS=1,2
```

---

## 5. 실행 방법

### 5-1. 서버 테스트 (소량 — **먼저 이것**)

```bash
cd /path/to/L1

# 5배치만 (~50키워드), API 소량 소모
.venv/bin/python scripts/generate_l3_expanded.py --resume --max-batches 5

# 결과 확인
wc -l outputs/l3_expanded_*.csv 2>/dev/null
tail outputs/l3_bulk_run.log
cat outputs/l3_daily_budget.json
head -3 outputs/l3_expanded_hitier.csv
```

**체크리스트**

- [ ] `focus_keyword`가 원본 키워드와 일치하는가
- [ ] YMYL(02·03) 행의 `model`에 gemini가 찍히는가
- [ ] 일반 L1 행의 `model`에 8b가 찍히는가
- [ ] `l3_id` = `{l2_id}-{slug}` 형태인가
- [ ] checkpoint·budget 파일 생성되는가

**테스트 후 초기화 (처음부터 다시)**

```bash
rm -f outputs/l3_bulk_checkpoint.json \
      outputs/l3_bulk_retry_queue.jsonl \
      outputs/l3_daily_budget.json \
      outputs/l3_expanded_hitier.csv \
      outputs/l3_expanded_longtail.csv
```

### 5-2. 본 실행 (일일 cron)

```bash
# 수동 1회
.venv/bin/python scripts/generate_l3_expanded.py --resume

# 또는 래퍼 (일일 한도 내장)
./scripts/run_l3_daily.sh
```

**crontab 예 (매일 09:00 KST 서버 시간에 맞게 조정)**

```cron
0 9 * * * /path/to/L1/scripts/run_l3_daily.sh >> /path/to/L1/outputs/l3_cron.log 2>&1
```

### 5-3. CLI 옵션 전체

```bash
python scripts/generate_l3_expanded.py --help

--resume              # checkpoint 이어서 (운영 필수)
--retry-only          # 재시도 큐만 처리
--batch-size 10       # 1차 배치 (기본 10, 25 금지)
--sleep 1.5           # 배치 간 대기(초)
--max-batches N       # 이번 실행 배치 상한 (0=무제한)
--max-calls-8b 10000
--max-calls-gemini 500
--max-calls-scout 800
```

> `--resume` 없이 checkpoint가 있으면 **실행 안 하고 안내만 출력** 후 종료.

### 5-4. 진행 모니터링

```bash
# 완료 건수
python3 -c "import json; d=json.load(open('outputs/l3_bulk_checkpoint.json')); print(d.get('count',0))"

# 오늘 API 사용량
cat outputs/l3_daily_budget.json

# 최근 로그
tail -20 outputs/l3_bulk_run.log

# 재시도 큐 잔량
wc -l outputs/l3_bulk_retry_queue.jsonl 2>/dev/null
```

### 5-5. 완료 후 merge

50,017건 checkpoint 완료 후:

```bash
.venv/bin/python scripts/merge_l3_expanded.py
```

| 출력 | 내용 |
|------|------|
| `outputs/l3_expanded_all.csv` | 기존 10,560 + 신규 ~50,017 (중복 제거) |
| `outputs/l3_expanded_all.nobom.csv` | BOM 없는 버전 |
| `outputs/l3_merge_report.json` | L1·intent·tier 분포 |

---

## 6. runtime 파일 (`outputs/`)

| 파일 | 생성 시점 | 역할 |
|------|-----------|------|
| `l3_expanded_hitier.csv` | 실행 중 append | hi tier 결과 |
| `l3_expanded_longtail.csv` | 실행 중 append | longtail 결과 |
| `l3_bulk_checkpoint.json` | 실행 중·종료 | `done_norms` — 재개 기준 |
| `l3_bulk_retry_queue.jsonl` | 실패 시 | 재시도 대기 |
| `l3_daily_budget.json` | 실행 중·종료 | UTC 날짜별 call 카운트 |
| `l3_bulk_run.log` | 종료마다 append | 요약 로그 |
| `l3_cron.log` | cron | 셸 stdout |

**설정 상수:** `scripts/l3_bulk_config.py`

---

## 7. 예상 일정

| 구간 | calls | 일 한도 | 예상 일수 |
|------|-------|---------|-----------|
| 8b 1차 (43,018 kw) | ~4,302 | 10,000/일 | **1일** |
| Gemini YMYL (6,999 kw) | ~700 | 500/일 | **2일** |
| Scout 재시도 | 가변 | 800/일 | +0~2일 |

**primary만:** 약 **2–3일** (서버 매일 cron 가정)  
재시도·429 많으면 +α.

---

## 8. 트러블슈팅

| 증상 | 원인 | 조치 |
|------|------|------|
| `ModuleNotFoundError: google` | venv 미설치 | `pip install -r requirements.txt` |
| `GROQ_API_KEY ... 없습니다` | .env 누락 | 서버에 `.env` 배포 |
| checkpoint 있는데 시작 안 됨 | `--resume` 누락 | `--resume` 추가 |
| `limit_hit=true` 로 종료 | 일일 한도 도달 | 정상 — 다음날 cron |
| YMYL인데 8b로 나감 | l1_code 오타 | 입력 CSV `l1_code` 확인 |
| 파싱 실패 다수 | batch 25 사용 | **batch 10만** 사용 |
| `l2_id`가 `XX-unknown` | source_seed 매핑 실패 | `data2/topics_l2.csv` 동일 L1 fallback |
| merge 후 행 수 이상 | l3_id 중복 | `l3_merge_report.json` deduped 확인 |

**429 / rate limit:** `groq_client.py`·`gemini_client.py`에 재시도 내장. `--sleep` 늘리기 (예: 2.0).

---

## 9. 스크립트 역할

| 스크립트 | 역할 | 서버 필요 |
|----------|------|-----------|
| `generate_l3_expanded.py` | L3 bulk 본체 | ✅ |
| `l3_bulk_config.py` | 경로·한도·YMYL·retry plan | ✅ |
| `gemini_client.py` | Gemini API | ✅ |
| `groq_client.py` | Groq 8b + Scout | ✅ |
| `run_l3_daily.sh` | cron 래퍼 | ✅ |
| `merge_l3_expanded.py` | 완료 후 통합 | ✅ (마지막) |
| `expand_l2_naver.py` | L2 확장 | ❌ (이미 입력 CSV 있음) |
| `experiment_l3_models.py` | 모델 실험 | ❌ (legacy) |
| `pilot_l3_review_batch.py` | slot2step 구 파이프 | ❌ |

---

## 10. git / legacy 주의

- `legacy/` → `.gitignore` (커밋 안 됨)
- `data/` 삭제·`data2/archive/` 삭제가 git에 staged 안 된 상태일 수 있음 — 레포 정리 커밋 별도
- `outputs/l2_candidates_gte100_depth1.csv` — git untracked 가능 → **서버 rsync로 직접 전달**
- `.env` — 절대 커밋 금지

---

## 11. 새 세션 시작 프롬프트 (복붙용)

```
프로젝트 L1 L3 bulk 서버 테스트 중.
핸드오프: docs/l3-bulk-server-handoff-2026-06-17.md 먼저 읽고 이어서.

상태:
- 입력: outputs/l2_candidates_gte100_depth1.csv (50,017)
- canonical L2/L3: data2/topics_l2.csv, data2/topics_l3_slot2step.csv
- 실행: scripts/generate_l3_expanded.py --resume
- legacy/ 는 gitignore, 서버 불필요

목표: [테스트 5배치 / 본 cron / merge / 트러블슈팅] 중 하나
```

---

## 12. 문서 인덱스

| 문서 | 시대 | 용도 |
|------|------|------|
| **`docs/l3-bulk-server-handoff-2026-06-17.md`** | **2026-06-17~** | **← 지금 이 작업 (5만 L3 bulk)** |
| `docs/data2-l3-claude-handoff.md` | 2026-06 | slot2step / claude_direct 1만 L3 |
| `docs/data2-taxonomy-handoff.md` | 2026-06 | 전체 택소노미·API 한도·구 slot2step |
| `data2/README.md` | 2026-06-17 갱신 | canonical 파일 구조 |

---

## 변경 이력

| 날짜 | 내용 |
|------|------|
| 2026-06-17 | 최초 작성 — legacy 정리 후 서버 bulk 인수인계 |
