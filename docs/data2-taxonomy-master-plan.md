# data2 택소노미 마스터 플랜 — L2 확장 · L3 다양화 · L4 · 100만 KW

> 작성일: 2026-06-05  
> 목적: L1~L4 택소노미 설계, L2 확장, L3 중복 해결, 100만 KW 창고 전략을 한 문서에 통합  
> 관련 인수인계: [`data2-taxonomy-handoff.md`](./data2-taxonomy-handoff.md) (구현 세부·재개 명령)  
> **L3 실행 런북**: [`data2-l3-slot2step-runbook.md`](./data2-l3-slot2step-runbook.md) (다른 Cursor·환경 재개)

---

## 목차

1. [현재 상태 스냅샷](#1-현재-상태-스냅샷-2026-06-05)
2. [프로젝트 목표와 정의](#2-프로젝트-목표와-정의)
3. [문제 진단 — 왜 L3가 중복되었나](#3-문제-진단--왜-l3가-중복되었나)
4. [채택 아키텍처 — 3트랙 + 계층](#4-채택-아키텍처--3트랙--계층)
5. [L2 확장 (210 → 603)](#5-l2-확장-210--603)
6. [L3 생성 전략 — 슬롯 2단계](#6-l3-생성-전략--슬롯-2단계)
7. [L3 다양성 A/B 테스트 결과](#7-l3-다양성-ab-테스트-결과)
8. [L4 fan-out 및 100만 KW 경로](#8-l4-fan-out-및-100만-kw-경로)
9. [Phase1 파일럿 — L3 cap · L4 축 (25 L2)](#9-phase1-파일럿--l3-cap--l4-축-25-l2)
10. [토큰·API 한도 전략](#10-토큰api-한도-전략)
11. [발행 vs 창고 vs hold](#11-발행-vs-창고-vs-hold)
12. [파일·스크립트 인덱스](#12-파일스크립트-인덱스)
13. [로드맵·체크리스트](#13-로드맵체크리스트)

---

## 1. 현재 상태 스냅샷 (2026-06-05)

| 레벨 | 수량 | 상태 | 비고 |
|------|------|------|------|
| **L1** | 14 | ✅ 고정 | `data2/seed/topics_l1.csv` |
| **L2** | **603** | ✅ 확장 완료 | 기존 210 + Wave1~3 **+393** |
| **L3** | 11,099행 | ⏸ 구 L2만 | **210개 L2**에만 존재, **393개 신규 L2는 L3 0** |
| **L4** | 0 | 미시작 | 100만 KW의 본체 |
| **본문** | 10건 | 파일럿 | `11-travel-essentials` 샘플 |

### L3 DB 현황

```
L2 총 603
├── L3 있음 (구 210 L2): 각 50개 내외 → 11,099행
└── L3 없음 (신규 393 L2): 0행
```

### L2 L1별 분포 (603)

| L1 | L2 수 | 비고 |
|----|-------|------|
| 09 맛집/카페 | 78 | geo·메뉴·상황 확장 최다 |
| 11 여행 | 68 | 국가·지역·howto |
| 10 푸드 | 56 | 국가별·재료·조리법 |
| 02 경제 | 46 | hold, 창고 위주 |
| 05 스포츠 | 45 | hold |
| 08 뷰티 | 44 | 고민·루틴·부위 |
| 01 연예 | 40 | hold |
| 14 아웃도어 | 42 | Phase2 |
| 07 IT/테크 | 38 | Phase3 |
| 13 펫 | 36 | Phase1 |
| 06 자동차 | 31 | Phase2 |
| 04 패션 | 29 | Phase3 |
| 03 건강 | 25 | YMYL |
| 12 교양 | 25 | hold |

---

## 2. 프로젝트 목표와 정의

### 목표

네이버형 **대량 SEO 블로그**를 위한 키워드·택소노미 파이프라인.

- **최종 KW 창고**: 약 **100만** 검색 질문/키워드 풀
- **발행 URL**: **10만~30만** (본문·아웃라인이 실질적으로 다른 페이지)
- **품질**: stem·의도 기준 **중복 최소화**, 템플릿 도배 방지

### 용어 정의

| 용어 | 의미 |
|------|------|
| **stem** | `focus_keyword`에서 끝수식어(추천·방법·체크리스트 등) 제거한 줄기 |
| **L3 허브** | 카테고리·메뉴용 클러스터 (L2당 5~12) |
| **L3 시드** | L4 fan-out의 부모 노드 (L2당 10~20) |
| **KW 창고** | URL 후보·내부링크·사이트맵용 대량 풀 |
| **발행** | Gemini 본문 + `tier=publish` |

### 100만의 정의 (중요)

> **100만 = 서로 다른 (의도 × 확장축 × stem) 조합의 KW 창고**  
> **≠** 100만 개가 전부 다른 상위 유형(추천/howto/compare…)  
> **≠** 100만 URL 전부 2000자 풀본문

현실적 체감 다양성: 창고 **75~90%**가 서로 다른 검색 상황.

---

## 3. 문제 진단 — 왜 L3가 중복되었나

### 근본 원인

| 잘못된 가정 | 실제 결과 |
|-------------|-----------|
| L2당 L3 **50개 필수** | 모델이 슬롯 채우기용 **템플릿 변형** 생성 |
| stem 검증 **생성 후**만 | `dedup_l3_topics.py`로 사후 정리 → 이미 11k 쌓임 |
| avoid **L2당 최근 40키워드**만 | 배치마다 같은 패턴 반복 |
| L3 = 글감 = KW 창고 **혼재** | 발행용·창고용 구분 없음 |
| L4 없이 L3만 키움 | 50개 변형으로 100만 착시 |

### `11-travel-essentials` 실측 (Nemotron 50개)

| 지표 | 값 | 해석 |
|------|-----|------|
| stem 유일율 | **96%** | 숫자상 양호 |
| `~추천` 비율 | **35/50 (70%)** | **같은 글 유형** 반복 |
| 기간 변형 | 국내/장기/단기/여름/겨울 | **같은 의도 축** |
| 대표 패턴 | `여행용 휴대용 X 추천` | 제품군만 다른 thin 변형 |

**교훈**: stem 유일 %만으로 다양성을 판단하면 안 됨. **의도 축·suffix·허브 반복**을 봐야 함.

### 초기 설계에서 유효했던 것 / 바꿔야 할 것

| ✅ 유지 | ❌ 변경 |
|---------|---------|
| L1→L2→L3→L4 계층 | L2×50=L3 균일 fan-out |
| SQLite checkpoint + `--resume` | L3 1-call 대량 생성만 |
| `dedup_l3_topics.py` 후처리 | 생성 시점 stem 검증 없음 |
| Phase1 우선순위 CSV | 전 L2 동일 cap 50 |

---

## 4. 채택 아키텍처 — 3트랙 + 계층

```
[L1 14] ── 고정 메인 카테고리
    │
[L2 600~800] ── 블로그 서브메뉴 (타입별: geo, howto, hub, cuisine…)
    │
    ├── [L3-A 허브]     5~12 / L2   발행·내부링크 중심
    ├── [L3-B 시드]    10~20 / L2   L4 부모
    └── [L3-C 프로그램]  0 LLM      지역·엔티티 lookup
    │
[L4 fan-out] ── 창고 KW 본체 (program | llm | hybrid)
    │
[KW] ── primary 1:1, variant 0~2 (SERP 분리 시만)
    │
[tier] publish | edit | hold | dropped
    │
[Gemini 본문] ── publish 큐만
```

### L2 타입별 L3 cap (전역 규칙)

| l2_type | L3 cap | L4 엔진 | L4/L3 (평균) |
|---------|--------|---------|---------------|
| `geo`, `geo_local` | 12~15 | **program** | 50~300 |
| `geo_intl` | 12~15 | hybrid | 40~80 |
| `hub`, `howto` | 10~15 | llm | 10~30 |
| `cuisine`, `ingredient` | 15~20 | hybrid | 20~80 |
| `compare`, `concern` | 10~12 | program+llm | 20~50 |
| `topic` (연예·경제 hold) | 5~10 | llm | 5~15 |

---

## 5. L2 확장 (210 → 603)

### 파이프라인

**Scout** (slug·description 생성) + **Gemini 3.1 flash-lite** (기존 L2와 의미 중복 검증)

```
data2/seed/l2_expansion_plan.csv  (힌트)
    → scripts/expand_taxonomy_l2.py
    → data2/topics_l2.csv (merge)
```

### Wave 요약

| Wave | 추가 L2 | 누적 | Scout | Gemini |
|------|---------|------|-------|--------|
| 시작 | — | 210 | — | — |
| Wave 1 (Phase1 L1) | +47 | 257 | 6 | 5 |
| Wave 2 | +47 | 304 | 7 | 5 |
| Wave 3 (전 L1) | +299 | **603** | 34 | 14 |
| **합계** | **+393** | **603** | **47** | **24** |

Gemini reject **39건** — 기존 L2와 의미 중복 (의도된 필터).

### Wave 1 대상 L1

09 맛집, 11 여행, 10 푸드, 08 뷰티, 13 펫 — Phase1 런치와 정합.

### L2 merge 보류 (`deferred`)

| L2 | 처리 |
|----|------|
| `11-domestic-travel` + `11-domestic-travel-destinations` | L3 이관 후 통합 |
| `11-overseas-travel` + `11-overseas-travel-destinations` | 동일 |

### 재실행

```bash
python scripts/build_l2_wave23_plan.py    # 계획 행 생성
python scripts/expand_taxonomy_l2.py --wave 2
python scripts/expand_taxonomy_l2.py --wave 3 --max-scout 40 --max-gemini 20
```

---

## 6. L3 생성 전략 — 슬롯 2단계

### 기존 방식 (`generate_taxonomy_l3.py`)

- L2당 `--count 50` (기본)
- 배치 15, avoid 최근 40키워드
- provider: Nemotron (본생산) / Scout (품질)

**현재 603 L2에 `--resume --count 50` 실행 시:**

- 210 L2 스킵 (이미 50+)
- **393 L2 × 50 ≈ 19,650 신규 L3**
- API **~1,572회** — 토큰·중복 리스크 큼 → **비권장**

### 채택 방식: slot_2step

```
1. Scout — 의도 슬롯 N개 (l3_slot_axes, 서로 다른 축)
2. Gemini — stem·기간변형·허브 중복 제거
3. Scout — 슬롯 1개당 L3 1개 (배치 5)
4. 코드 — stem / suffix cap / 허브반복 / intent_angle cap
5. (선택) dedup_l3_topics.py
```

### L3 생성 금지·쿼터 규칙

| 규칙 | 내용 |
|------|------|
| 기간·계절 | `단기/장기/여름/겨울` L2당 **1슬롯** |
| suffix | `~추천` L3 **≤2개/L2** |
| 허브 반복 | 같은 허브 stem **≤5개/L2** |
| stem | 동일 stem → **즉시 reject** |
| intent×angle | 조합당 상한 (15개 기준 cap 6) |

### L3 필드 (기존 유지 + 권장 추가)

기존: `l3_id`, `l2_id`, `focus_keyword`, `title_ko`, `search_intent`, `topic_angle`, `description`

권장 추가 (향후):

- `l3_tier`: `hub` | `seed` | `warehouse`
- `stem`: 후처리·중복 판정용
- `canonical_key`: `(l2_id, stem, intent)` 해시

---

## 7. L3 다양성 A/B 테스트 결과

**스크립트**: `scripts/test_l3_diversity.py`  
**산출물**: `data2/test/l3_diversity/l3_diversity_report.json`

### 테스트 L2 (3개 × 15목표)

- `11-travel-essentials` (hub, 중복 문제 L2)
- `09-gangnam-food` (신규 geo)
- `10-air-fryer-recipes` (신규 howto)

### 비교 요약

| 방식 | stem 유일 | 체감 주제 다양성 | 비고 |
|------|-----------|------------------|------|
| **Nemotron 50** (기존) | 96% | ❌ 추천 70% | `여행용 휴대용 X 추천` 반복 |
| **Scout baseline 15** | 100% | △ | `여행 준비물 체크리스트/목록/예산` 허브 반복 |
| **슬롯+검증 15** | 100% | ✅ | 보험·아기·짐싸기·분실방지 등 **의도 축 분리** |

### diverse 파이프라인 예시 (`여행준비물`)

```
해외여행 보험 가입
아기 여행 준비물
여행자 보험 비교
여행 준비물 포장
여행 준비물 분실 방지
```

### 테스트에서 확인된 파이프라인 이슈

- 슬롯 15개 → 한 번에 L3 채우기 시 **수락 4~7개**만 통과 (fill 누락 + intent_angle cap)
- **해결**: 슬롯 1개당 1 API 호출 또는 배치 5×3, intent/angle 슬롯 단계에서 분산

### 재실행

```bash
python scripts/test_l3_diversity.py
python scripts/test_l3_diversity.py --l2 09-gangnam-food
```

---

## 8. L4 fan-out 및 100만 KW 경로

### 왜 L3만으로 100만이 안 되는가 / 되는가

| 경로 | 가능? | 설명 |
|------|-------|------|
| L2×L3 50 균일 | ❌ 품질 | 템플릿 창고 |
| L3 1만 × L4 100 | ✅ | **권장** |
| L3만 100만 | ❌ | LLM·토큰·중복 폭발 |
| L4 program (geo) | ✅ | 토큰 0, 진짜 롱테일 |

### 규모 시뮬레이션 (채택)

```
L2 650
× L3 시드 18 (평균)
× L4 85 (평균, geo는 150+)
≈ 995,000 ~ 1,050,000 KW
```

| L2 타입 | L2 수(예) | L3 | L4/L3 | 소계 KW |
|---------|-----------|-----|-------|---------|
| geo | ~120 | 15 | 150 | ~270,000 |
| howto/hub | ~150 | 12 | 20 | ~36,000 |
| cuisine | ~180 | 20 | 50 | ~180,000 |
| compare | ~120 | 15 | 60 | ~108,000 |
| 기타+hold | ~80 | 10 | 25 | ~20,000 |
| **ES 엔티티** (`keyword/es`) | — | — | — | **+200,000~400,000** |

### L4 엔진 3종

| 엔진 | 용도 | 토큰 |
|------|------|------|
| **program** | 지역·역·메뉴·견종·브랜드 lookup 곱셈 | 0 |
| **llm** | howto·hub·info 시드 확장 | 소량 |
| **hybrid** | LLM 시드 + 코드 fan-out | 중간 |

### Phase1 창고 추정

**~11,396 KW** (25 L2, `phase1_l3_l4_plan.csv` 합계) — 품질·fan-out **검증용**.

---

## 9. Phase1 파일럿 — L3 cap · L4 축 (25 L2)

**계획 CSV**: `data2/pilot/phase1_l3_l4_plan.csv`  
**요약 JSON**: `data2/pilot/phase1_l3_l4_summary.json`

### Phase1 합계

| 항목 | 값 |
|------|-----|
| L2 | 25 |
| L3 cap 합 | **304** |
| L4 창고 KW 추정 | **~11,396** |
| 발행 L3 cap 합 | **186** |
| M1 발행 URL 목표 | **286** |

### L1별

| L1 | L2 | L3 cap | L4 창고 | 발행 M1 |
|----|-----|--------|---------|---------|
| 11 여행 | 5 | 69 | ~3,630 | 70 |
| 09 맛집/카페 | 5 | 59 | ~4,130 | 57 |
| 10 푸드 | 5 | 64 | ~1,236 | 55 |
| 08 뷰티 | 5 | 56 | ~820 | 52 |
| 13 펫 | 5 | 56 | ~1,116 | 52 |

### Phase1 L2 전체 계획표

| l2_id | l2_name | type | L3 cap | L4 엔진 | L4 추정 | 발행 L3 | slot_axes 요약 |
|-------|---------|------|--------|---------|---------|---------|----------------|
| 11-travel-essentials | 여행준비물 | hub | 12 | llm | 180 | 8 | 대상·상황·목적·방법·비교 |
| 11-accommodation-recommendations | 숙소추천 | geo | 15 | hybrid | 1200 | 10 | 지역·숙소유형·상황·예산 |
| 11-domestic-travel | 국내여행 | geo | 12 | program | 720 | 10 | 지역·여행유형·동반 |
| 11-overseas-travel | 해외여행 | geo_intl | 15 | hybrid | 750 | 10 | 국가·준비·여행유형 |
| 11-travel-tips | 여행꿀팁 | howto | 15 | llm | 180 | 8 | 문제·장소·앱 |
| 09-seoul-food | 서울 맛집 | geo_local | 15 | program | 1800 | 12 | 구동·메뉴·상황 |
| 09-busan-food | 부산 맛집 | geo | 12 | program | 960 | 10 | 명소·메뉴·상황 |
| 09-cafe-recommend | 카페 추천 | cafe | 12 | program | 720 | 8 | 지역·카페유형·상황 |
| 09-solo-food | 혼밥 맛집 | situation | 10 | program | 400 | 6 | 지역·메뉴·좌석 |
| 09-date-course | 데이트 코스 | composite | 10 | hybrid | 250 | 5 | 지역·코스유형·예산 |
| 10-korean-recipe | 한식 레시피 | cuisine | 18 | hybrid | 540 | 12 | 요리명·난이도·인분 |
| 10-cooking-tips | 요리 팁 | howto | 12 | llm | 180 | 8 | 기법·재료·실수방지 |
| 10-baking-guide | 베이킹 가이드 | howto | 12 | llm | 216 | 8 | 종류·도구·실패원인 |
| 10-dessert-making | 디저트 만들기 | cuisine | 12 | llm | 180 | 6 | 디저트·재료·노오븐 |
| 10-healthy-convenience-food | 건강 간편식 | meal | 10 | llm | 120 | 5 | 식단·상황·조리시간 |
| 08-skin-type-care | 피부 타입별 케어 | concern | 12 | hybrid | 240 | 8 | 타입·고민·루틴단계 |
| 08-skincare-routine | 스킨케어 루틴 | hub | 10 | llm | 120 | 6 | 연령·시간대·고민 |
| 08-hair-care-tips | 헤어 케어 팁 | howto | 12 | llm | 180 | 8 | 고민·모발타입 |
| 08-makeup-tutorial | 메이크업 튜토리얼 | howto | 12 | llm | 180 | 8 | 부위·상황·피부톤 |
| 08-cosmetic-ingredient-analysis | 화장품 성분 분석 | info | 10 | llm | 100 | 5 | 성분·고민·제품유형 |
| 13-dog-health | 강아지 건강 | concern | 12 | hybrid | 216 | 8 | 증상·견종·생애주기 |
| 13-cat-food | 고양이 사료 | compare | 12 | program | 300 | 8 | 연령·유형·브랜드 |
| 13-dog-training | 강아지 훈련 | howto | 12 | llm | 180 | 8 | 행동·연령·환경 |
| 13-pet-care-tips | 펫 케어 팁 | howto | 10 | llm | 120 | 6 | 일상·계절·다묘 |
| 13-pet-hospital | 반려동물 병원 | geo_local | 10 | program | 300 | 5 | 지역·동물종·응급 |

### Phase1 L3 API 추정

| 항목 | 값 |
|------|-----|
| Scout / L2 | 4~8회 |
| Gemini / L2 | 1회 |
| **Phase1 Scout 합** | **100~200회** |
| **Phase1 Gemini 합** | **25회** |

---

## 10. 토큰·API 한도 전략

### 프로바이더 역할 (2026-06 기준)

| 용도 | 모델 | 한도(참고) |
|------|------|------------|
| L2 확장 | Scout + Gemini flash lite | Scout 600~1000/일, Gemini 500/일 |
| L3 슬롯·채우기 | Scout | TPD 50만 (Groq Free) |
| L3 본생산 (대량) | Nemotron | Groq TPD와 별도 |
| L3 슬롯 dedup | Gemini 3.1 flash-lite | 500/일 |
| L4 howto | Scout / Nemotron | 소량 |
| L4 geo | **코드** | 0 |
| 본문 | Gemini flash / pro | 별도 트랙 |

### 시나리오별 토큰 (Scout ~5K/호출 가정)

| 시나리오 | API 호출 | 토큰(추정) | 소요(TPD 50만) |
|----------|----------|------------|----------------|
| Phase1 L3 (25 L2) | ~150 | ~75만 | **1일** |
| 신규 393 L2 × 50 (구방식) | ~1,572 | ~780만 | **2~3주** |
| 신규 393 L2 × 18 (cap) | ~800 | ~400만 | **1~2주** |

### 한도 해결 원칙

1. **단계 실행** — Phase1 → geo L2 → 나머지, `--resume` 일일 분산  
2. **L2 타입별 cap** — 50 고정 금지  
3. **L4 program** — geo·맛집·병원·사료는 LLM 없이 fan-out  
4. **키 로테이션** — `GROQ_API_KEY`, `GROK_API_KEY_2`  
5. **발행 vs 창고 분리** — LLM 예산은 publish L3에 우선

---

## 11. 발행 vs 창고 vs hold

```
topics_l3.csv (전체)
    │
    ├─► topics_l3_curated.csv   tier=publish|edit
    ├─► topics_l3_hold.csv      tier=hold (연도·뉴스·YMYL)
    └─► topics_l3_dropped.csv   merge·cap 탈락
            │
            ▼
    phase1_body_pilot.csv → Gemini 본문
            │
            ▼
    data2/answers/body/*.md
```

| tier | 용도 |
|------|------|
| **publish** | M1 발행 큐 (`blog_launch_priority` L2) |
| **edit** | 품질 보완 후 발행 |
| **hold** | 창고만 (연예·스포츠·연도 키워드) |
| **dropped** | stem 중복·병합·cap 초과 |

**L4/KW 창고**는 `tier=warehouse` (향후) — 발행 비율 20~30% 목표.

---

## 12. 파일·스크립트 인덱스

### 시드·계획

| 파일 | 설명 |
|------|------|
| `data2/seed/topics_l1.csv` | L1 14 |
| `data2/seed/blog_launch_priority.csv` | Phase1~3 L2 우선순위 |
| `data2/seed/l2_expansion_plan.csv` | L2 Wave1~3 계획 (434행) |
| `data2/pilot/phase1_l3_l4_plan.csv` | Phase1 L3/L4 상세 |
| `data2/pilot/phase1_l3_l4_summary.json` | Phase1 합계 |
| `data2/pilot/phase1_body_pilot.csv` | 본문 파일럿 286행 |
| `data2/brief.txt` | LLM 기획 힌트 |

### 택소노미 산출물

| 파일 | 설명 |
|------|------|
| `data2/topics_l2.csv` | **L2 603** |
| `data2/topics_l2_expanded.csv` | Wave별 신규 L2만 |
| `data2/topics_l3.csv` | L3 11,099 (구 210 L2) |
| `data2/topics_l3_curated.csv` | 후처리 발행 큐 |
| `data2/state/taxonomy.db` | L3 checkpoint |

### 리포트·테스트

| 파일 | 설명 |
|------|------|
| `data2/reports/l2_expansion_report.json` | L2 Wave3 API 통계 |
| `data2/reports/l3_curate_report.json` | L3 dedup 통계 |
| `data2/test/l3_diversity/l3_diversity_report.json` | L3 A/B 테스트 |
| `data2/test/l3_review_pilot/l3_review_pilot.csv` | L3 파일럿 리뷰용 (277행) |
| `data2/test/l3_review_pilot/l3_review_pilot_report.json` | 파일럿 API·수락 집계 |

### 스크립트

| 스크립트 | 용도 |
|----------|------|
| `scripts/expand_taxonomy_l2.py` | L2 Wave 확장 (Scout+Gemini) |
| `scripts/build_l2_wave23_plan.py` | L2 Wave2~3 계획 행 생성 |
| `scripts/pilot_l3_review_batch.py` | **L3 slot_2step 파일럿·리뷰 CSV** |
| `scripts/generate_taxonomy_l3.py` | L3 생성 (구 방식, slot_2step 통합 예정) |
| `scripts/test_l3_diversity.py` | L3 다양성 A/B 테스트 |
| `scripts/dedup_l3_topics.py` | L3 후처리 (LLM 없음) |
| `scripts/export_phase1_pilot.py` | Phase1 본문 큐 추출 |
| `scripts/generate_data2_articles.py` | Gemini 본문 생성 |
| `scripts/curate_l2_publish_plan.py` | L2별 publish/merge/hold |

### 문서

| 파일 | 설명 |
|------|------|
| `docs/data2-taxonomy-handoff.md` | 구현 인수인계 (2026-06-02) |
| **`docs/data2-taxonomy-master-plan.md`** | **본 문서** — 설계·확장·L3/L4·100만 |
| **`docs/data2-l3-slot2step-runbook.md`** | **L3 실행 런북** — 다른 Cursor 재개·명령·한도 |

---

## 13. 로드맵·체크리스트

### Phase 0 — 규칙 고정 ⏳

- [x] L2 603 확장
- [x] Phase1 L3/L4 계획 CSV
- [x] L3 다양성 A/B 테스트
- [ ] `generate_taxonomy_l3.py` — slot_2step + `--plan-csv` 연동
- [ ] `fanout_l4_geo.py` (program) 프로토타입
- [ ] L2 merge (`국내여행`/`해외여행` 중복)

### Phase 1 — 파일럿 (0~3개월)

- [ ] Phase1 25 L2 L3 생성 (slot_2step, cap 304)
- [ ] geo L2 L4 program fan-out (~11k 창고)
- [ ] `dedup_l3_topics.py` → curated
- [ ] 본문 286편 파일럿 (L2 분산 샘플링)
- [ ] GSC/네이버 샘플로 fan-out 수요 검증

### Phase 2 — L3 시드 전면 (2~4주×N)

- [ ] 신규 393 L2 L3 (cap 18 평균, 일 50~100 L2)
- [ ] 구 210 L2 hub 재생성 (선별)

### Phase 3 — L4 + 100만 창고

- [ ] L4 LLM (howto·compare)
- [ ] L4 program (전 geo·recipe)
- [ ] `keyword/es` 엔티티 연계
- [ ] 창고 **80만~120만**, 발행 **10만~30만** 큐

### 명령어 치트시트

```bash
# L3 다양성 테스트
python scripts/test_l3_diversity.py

# L3 Phase1 (구현 후)
python scripts/generate_taxonomy_l3.py --plan-csv data2/pilot/phase1_l3_l4_plan.csv --resume

# L3 후처리
python scripts/dedup_l3_topics.py

# Phase1 본문 큐
python scripts/export_phase1_pilot.py

# 본문 생성
python scripts/generate_data2_articles.py
```

---

## 부록 A — SEO 원칙 (기존 handoff 유지)

- **L4 1개 = primary KW 1개** (intent×angle 무차별 곱 금지)
- variant URL은 SERP/의도가 **실제 분리**될 때만 0~2개
- intent×angle 테이블은 **분류용**, URL 기계 곱셈 금지
- YMYL (건강·경제): 면책·hold 강화

## 부록 B — 본문 모델 (2-track)

| 용도 | 모델 |
|------|------|
| 택소노미 L2/L3 | Scout, Nemotron, Gemini (검증) |
| 제목·메타 | `gemini-3.1-flash-lite` |
| 본문 초안 | `gemini-2.5-flash` |
| 도입 폴리시 (소량) | `gemini-2.5-pro` |

---

*문서 끝. 갱신 시 상단 작성일과 §1 스냅샷을 함께 업데이트할 것.*
