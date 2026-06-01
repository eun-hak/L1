# data2 대량 SEO 키워드 택소노미 — 기획·구현 인수인계

> 작성일: 2026-06-01  
> 목적: 대화 세션 초기화 전, 지금까지 기획·구현·결정 사항을 다른 환경에서 이어갈 수 있도록 기록

---

## 1. 프로젝트 목표

**네이버 블로그형 SEO 콘텐츠를 대량 생산**하기 위한 키워드·택소노미 파이프라인.

- L1(대분류) → L2(중분류) → L3(소분류/주제) → L4(리프 토픽) → **KW(검색 키워드)** 순으로 확장
- 최종 목표: **수백만~수천만** 검색 키워드 풀 구축
- LLM은 **대량 taxonomy 생성**에 사용, 본문·SEO polish는 **Gemini** 등 고품질 모델로 분리 (2-track 전략)

---

## 2. 프로젝트 내 트랙 구분

| 트랙 | 경로 | 데이터 소스 | 상태 |
|------|------|------------|------|
| **택소노미 v1** | `data/` | L1 72개 → L2 → L3 | ~10,368 L3, 템플릿 본문 대량 생성됨 |
| **ES 엔티티** | `keyword/es/` | Elasticsearch `simsimi_entities_v1` (~24만 QID) | 100건 질문 + ~144건 본문, Gemini 전환 중 |
| **택소노미 v2 (메인 작업)** | `data2/` | 사용자 정의 L1 14개 | **L2 210개 완료**, L3~ 미구현 |

**이 문서는 `data2/` 트랙 전용 인수인계입니다.**

---

## 3. L1 시드 (14개, 고정)

파일: `data2/seed/topics_l1.csv`

| code | name_ko | l1_slug |
|------|---------|---------|
| 01 | 이슈/연예 | entertainment |
| 02 | 경제 | economy |
| 03 | 건강/다이어트 | health |
| 04 | 패션 | fashion |
| 05 | 스포츠이슈 | sports |
| 06 | 자동차 | cars |
| 07 | IT/테크 | tech |
| 08 | 뷰티 | beauty |
| 09 | 맛집/카페 | cafe |
| 10 | 푸드 | food |
| 11 | 여행 | travel |
| 12 | 지식/교양 | knowledge |
| 13 | 펫 | pet |
| 14 | 아웃도어 | outdoor |

스키마: `l1_code, l1_slug, name_ko, theme, description, sort_order`

---

## 4. 레벨별 정의 (기획)

### L1 — 대분류 (고정, 14개)
사용자가 직접 정의. 블로그 최상위 메뉴.

### L2 — 중분류 (블로그 메뉴)
- L1당 **15개** (완료)
- 예: `이슈/연예` → 드라마, 예능, 연예인, 컴백 …
- 실제 블로그 카테고리처럼 **서로 겹치지 않는** 하위 축

### L3 — 소분류 / 주제 클러스터
- L2당 **50개** (다음 작업)
- 네이버·구글에서 검색될 **구체적 롱테일 주제**
- 예: L2 `드라마` → "2026 Netflix 드라마", "금토드라마 시청률", "드라마 OST 추천" …

### L4 — 리프 토피 (글 1편 단위)
- L3당 **30~80개** (미구현)
- 실제 URL 1개 = 콘텐츠 1편의 주제 단위

### KW — 검색 키워드 (최종 산출물)
- **L4 1개 = primary KW 1개** (기본 원칙, 아래 SEO 섹션 참고)
- intent별 무차별 곱셈 금지

---

## 5. 규모 시뮬레이션

| 레벨 | fan-out | 누적 |
|------|---------|------|
| L1 | 14 (고정) | 14 |
| L2 | ×15 | **210** ✅ |
| L3 | ×50 | **10,500** (다음) |
| L4 | ×50 | **525,000** |
| KW | ×1~1.2 (선별) | **~525,000~630,000** |

L4까지 LLM, KW는 L4와 1:1 또는 선별 variant만.

---

## 6. SEO 중복 콘텐츠 — 핵심 결정

### 문제
L4 하나에서 `search_intent × topic_angle` 템플릿으로 5~10개 KW를 **기계적으로 곱하면**:
- 제목만 `총정리` / `방법` / `후기`로 바꾸고 본문이 80% 동일 → **thin/duplicate content** 리스크

### 채택한 원칙

| ❌ 하지 않을 것 | ✅ 할 것 |
|---------------|---------|
| L4마다 intent 5~10개 무조건 생성 | **L4 1개 = primary KW 1개** |
| 템플릿만 다른 URL 대량 생성 | variant는 SERP/의도가 **실제로 분리**될 때만 (0~2개) |
| 같은 outline으로 본문 생성 | intent별 **다른 아웃라인** 강제 (info/howto/compare 등) |

### intent × angle 테이블
**키워드 아이디어 분류용**으로만 사용. URL 생성은 L4 단위 + 선별 variant.

### 본문 생성 시 intent별 outline 예시

| search_intent | 필수 섹션 방향 |
|---------------|---------------|
| info | 배경 → 핵심 사실 → 타임라인 → 영향 |
| howto | 준비물 → 단계 → 주의사항 → FAQ |
| compare | A vs B 표 → 차이점 → 선택 기준 |
| review | 경험 → 장단 → 추천 대상 |

---

## 7. LLM 프로바이더 전략

### 채택: Groq Llama 4 Scout 17B (taxonomy 생성)

| 항목 | 값 |
|------|-----|
| 모델 ID | `meta-llama/llama-4-scout-17b-16e-instruct` |
| API | OpenAI-compatible, `https://api.groq.com/openai/v1` |
| 환경변수 | `GROQ_API_KEY` 또는 `GROK_API_KEY` (`.env`) |
| Rate limit | Developer plan 기준 ~1K RPM (충분) |

### L2 생성 실측 (2026-06-01)

| 항목 | NVIDIA 8B (1차) | Groq Scout 17B (최종) |
|------|----------------|----------------------|
| L2 총 개수 | 154 (14×15 미달) | **210 (14×15 완료)** |
| API 호출 | 14회 (L1당 1회) | **14회** (L1당 1회) |
| 소요 시간 | ~2.5분 | **~43초** |
| 품질 | JSON 파싱 실패·L1 이탈 다수 | 안정적 |

### NVIDIA 8B (보조/실험용)

| 항목 | 값 |
|------|-----|
| 모델 ID | `meta/llama-3.1-8b-instruct` |
| 환경변수 | `NVIDIA_API_KEY` |
| 클라이언트 | `scripts/nvidia_client.py` |
| 비고 | L2 1차 테스트 후 Groq로 전환. 품질·JSON 안정성 부족 |

### Gemini (본문·SEO polish)

| 용도 | 모델 |
|------|------|
| SEO 제목·메타 | gemini-3.1-flash-lite |
| 본문 초안 | gemini-2.5-flash |
| 도입부 polish | gemini-2.5-pro |
| 클라이언트 | `scripts/gemini_client.py` |

**원칙**: taxonomy(L2/L3/L4) = Groq(저비용·고속), 본문 = Gemini(고품질)

---

## 8. 현재 완료 상태

### ✅ 완료

- [x] `data2/seed/topics_l1.csv` — L1 14개
- [x] `data2/brief.txt` — L2/L3 생성 가이드
- [x] `data2/topics_l2.csv` / `.json` — **L2 210개**
- [x] `data2/manifest_l2.json`
- [x] `scripts/groq_client.py` — Groq API + `generate_l2_categories()`
- [x] `scripts/nvidia_client.py` — NVIDIA API + `generate_l2_categories()`
- [x] `scripts/generate_taxonomy_l2.py` — L2 생성 CLI (`--provider groq|nvidia`)

### ❌ 미구현

- [ ] `scripts/generate_taxonomy_l3.py`
- [ ] `scripts/generate_taxonomy_l4.py`
- [ ] `groq_client.generate_l3_topics()` (또는 공통 taxonomy 모듈)
- [ ] SQLite checkpoint (`data2/state/taxonomy.db`) — 대량 생성 시 resume용
- [ ] L3/L4용 `data2/brief.txt` 보강 (L3 기준 추가)

---

## 9. 디렉터리 구조

```
data2/
├── seed/
│   └── topics_l1.csv          # L1 14개 (고정)
├── brief.txt                   # LLM 생성 가이드
├── topics_l2.csv               # L2 210개 ✅
├── topics_l2.json
├── manifest_l2.json
├── topics_l3.csv               # (예정)
├── topics_l4.csv               # (예정)
└── state/                      # (예정) checkpoint

scripts/
├── groq_client.py              # Groq taxonomy 클라이언트
├── nvidia_client.py            # NVIDIA taxonomy 클라이언트
├── generate_taxonomy_l2.py     # L2 생성 CLI
├── gemini_client.py            # Gemini (본문·SEO·기존 taxonomy 함수)
└── generate_seo_*.py           # ES 엔티티 트랙 (별도)

docs/
└── data2-taxonomy-handoff.md   # 이 문서
```

---

## 10. 데이터 스키마

### L1 (`data2/seed/topics_l1.csv`)

```csv
l1_code,l1_slug,name_ko,theme,description,sort_order
```

### L2 (`data2/topics_l2.csv`) — 현재

```csv
l2_id,l1_code,l2_slug,name_ko,description,sort_order,model,generated_at
```

- `l2_id` = `{l1_code}-{l2_slug}` (예: `01-drama`)
- slug: 영문 kebab-case

### L3 (예정) — `data/topics_l3.csv` v1 참고

```csv
l3_id,l2_id,l1_code,l3_slug,focus_keyword,title_ko,search_intent,topic_angle,description,model,generated_at
```

- `l3_id` = `{l2_id}-{l3_slug}` 또는 `{l2_id}-{seq}`
- `focus_keyword`: 네이버 검색 롱테일 (2~12단어)
- `search_intent`: info | howto | compare | cost | checklist | review | news
- `topic_angle`: start | howto | compare | tip | review | issue | local

### L4 (예정)

```csv
l4_id,l3_id,l2_id,l1_code,l4_slug,focus_keyword,seo_title,search_intent,description,model,generated_at
```

---

## 11. L2 생성 — 실행 방법

### 환경

```bash
cd /path/to/L1
python3 -m venv .venv
source .venv/bin/activate
pip install openai google-genai python-dotenv
```

`.env`:
```
GROQ_API_KEY=gsk_...        # 또는 GROK_API_KEY
NVIDIA_API_KEY=nvapi-...    # (선택)
GEMINI_API_KEY=...          # 본문 생성용
```

### 명령

```bash
# L2 전체 생성 (Groq Scout 17B, L1당 15개)
python scripts/generate_taxonomy_l2.py --provider groq --count 15 --sleep 1.0

# 특정 L1만 재생성 (기존 결과 merge)
python scripts/generate_taxonomy_l2.py --provider groq --l1 01 --count 15

# NVIDIA로 (비권장)
python scripts/generate_taxonomy_l2.py --provider nvidia --count 15
```

### L2 생성 로직 요약

1. `topics_l1.csv` 14개 순회
2. L1당 Groq 1회 호출 → L2 15개 JSON 배열
3. 전역 `avoid_names`로 L1 간 L2 이름 중복 방지
4. slug 중복 시 `-2`, `-3` suffix
5. 한국어 외 문자(중국어·베트남어 등) 필터

---

## 12. L3 생성 — 다음 작업 (미구현, 기획 확정)

### 목표

- L2 **210개** × L2당 **50개** = **10,500 L3**
- 프로바이더: **Groq Scout 17B** (L2와 동일)

### 호출 전략: **15개 × 4배치**

| 항목 | 값 |
|------|-----|
| L2당 L3 목표 | 50개 |
| 배치 크기 | 15개 |
| L2당 API 호출 | **4회** |
| **총 API 호출** | 210 × 4 = **840회** |
| 예상 시간 | **55~70분** (호출당 3~4초 + sleep) |
| sleep 권장 | L2 간 0.5~1.0초 (rate limit 여유 있음) |

### `generate_taxonomy_l3.py` 구현 시 참고

```bash
# 예상 사용법 (미구현)
python scripts/generate_taxonomy_l3.py --provider groq --count 50 --batch 15
python scripts/generate_taxonomy_l3.py --provider groq --l2 01-drama --resume
```

구현 포인트:
1. `topics_l2.csv` 순회 (210행)
2. L2당 while loop: batch 15 × max 4 attempts = 50개
3. `--l2` 단일 재생성 + `--resume` (기존 CSV merge, `exclude_l2` 패턴)
4. avoid list: 전역 `focus_keyword` + 같은 L2 내 중복
5. L1/L2 컨텍스트를 프롬프트에 포함 (L1 이탈 방지)
6. `attempts` 상한 L2 스크립트는 3 → L3는 **4**로 (50/15=3.33)

### L3 프롬프트 규칙 (brief에 추가 권장)

- `focus_keyword`: 실제 검색할 2~12단어, 의도가 서로 달라야 함
- L2 범위 안에서만
- clickbait 금지 ("완벽 가이드", "놓치면 후회")
- 패턴 반복 금지 (전부 "~방법 | ~가이드")
- `data2/brief.txt`에 L3 섹션 추가 필요

---

## 13. L4 생성 — 이후 작업 (기획)

| 항목 | 값 |
|------|-----|
| L3 | 10,500 |
| L3당 L4 | 50 |
| L4 총 | **525,000** |
| 배치 | 15 × 4 (L3와 동일 패턴) |
| API 호출 | 10,500 × 4 = **42,000회** |
| 예상 시간 | 수일 (sleep·resume 필수) |

→ **SQLite checkpoint + `--resume` 필수**. CSV만으로는 중단·재개 어려움.

---

## 14. 콘텐츠 품질 규칙 (공통)

`keyword/example.jsonl`, `keyword/brief.txt`, `scripts/gemini_client.py` 스타일 가이드 기준.

### SEO 제목
- 핵심 키워드 앞 배치, 25~55자
- 명사형·설명형
- clickbait 금지

### 본문 (Gemini 생성 시)
- 존댓말 (`~합니다`, `~하세요`)
- 소제목: `**굵은 한 줄**` (마크다운 `##` 금지)
- 도입: "많은 분들이 궁금해하실 것입니다" …
- 마무리: `**결론적으로**`
- 분량: 1,500~3,000자

---

## 15. 기존 코드 재사용 참고

### `scripts/gemini_client.py`

taxonomy 함수 (Gemini용, Groq 클라이언트에서 프롬프트 참고 가능):
- `generate_l2_categories()`
- `generate_l3_topics()` — L3 프롬프트·필드 정의 참고
- `parse_json_array()` — JSON 파싱 (Groq/NVIDIA 클라이언트에서 import)

### `data/topics_l3.csv` (v1)

10,368행. L3 ID·slug·focus_keyword 패턴 참고용. **data2와 L1 체계 다름.**

---

## 16. 알려진 이슈·주의사항

1. **NVIDIA 8B**: L2 1차 테스트 — JSON 파싱 실패·L1 범위 이탈·목표 개수 미달. **L3+에는 Groq 권장.**
2. **gap-fill 시 CSV 덮어쓰기**: `--l1` 단독 실행 시 merge 로직 있으나, 전체 재생성 전 `rm topics_l2.csv` 권장.
3. **`data2/brief.txt` 헤더**: "NVIDIA Llama 3.1 8B" 문구 남아 있음 → L3 작업 전 Groq/Scout으로 수정 권장.
4. **L2 품질**: Scout 17B 결과 양호하나, L1 간 의미 중복 가능 (예: 여행 L2에 `국내여행` / `국내여행지`). L3 전 dedup 검토 optional.
5. **`.venv`**: 프로젝트 로컬 venv 사용. `pip install openai google-genai python-dotenv`.

---

## 17. 다음 작업 체크리스트

```
[ ] data2/brief.txt — L3 섹션 추가
[ ] groq_client.py — generate_l3_topics() 추가
[ ] scripts/generate_taxonomy_l3.py — L2 CSV 입력, 50×4 배치
[ ] data2/topics_l3.csv 생성 (10,500행)
[ ] (선택) data2/state/taxonomy.db — 840회+ resume
[ ] L4 스크립트 + SQLite
[ ] 본문 생성 파이프라인 연결 (Gemini, L4 → markdown)
```

### L3 파일럿 권장 순서

1. L2 1개 (`01-drama`) × L3 50개 테스트
2. 프롬프트·JSON 품질 확인
3. L2 210개 전체 배치 (840 calls, ~1시간)
4. 샘플 50개 수동 리뷰

---

## 18. 관련 파일 빠른 링크

| 파일 | 설명 |
|------|------|
| `data2/seed/topics_l1.csv` | L1 14개 |
| `data2/topics_l2.csv` | L2 210개 (현재 산출물) |
| `data2/manifest_l2.json` | L2 생성 메타 |
| `data2/brief.txt` | LLM 기획 방향 |
| `scripts/generate_taxonomy_l2.py` | L2 CLI |
| `scripts/groq_client.py` | Groq 클라이언트 |
| `scripts/gemini_client.py` | Gemini + taxonomy 함수 원본 |
| `keyword/es/` | ES 엔티티 SEO 트랙 (별도) |

---

*이 문서는 Cursor 대화 세션(2026-06-01) 기준 기획·구현 상태를 반영합니다.*
