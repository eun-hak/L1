# Claude 검토용 프롬프트 — data2 택소노미 시스템

> **용도**: Claude(또는 다른 LLM)에 붙여넣어 **현재 시스템 검토 + 개선 설계 + 앞으로 설계 방향**을 받을 때 사용  
> **작성일**: 2026-06-10

---

## 사용 방법

1. 아래 **「전체 프롬프트」** 블록을 복사해 Claude에 붙여넣기
2. **첨부 파일** 섹션에 나온 파일을 함께 업로드 (가능한 만큼)
3. Claude 응답이 **§ 출력 형식**을 따르는지 확인. 빠지면 «출력 형식대로 다시» 요청

**단일 소스**: 시스템 현황은 `docs/data2-system.md` (2026-06-10) 기준.  
`data2-taxonomy-master-plan.md` §1·handoff·runbook의 84 L2 스냅샷은 **구식** — 무시할 것.

---

## 첨부 파일 (우선순위)

### 필수

| 파일 | 이유 |
|------|------|
| `docs/data2-system.md` | 현재 구현·상태·정책 |
| `docs/data2-taxonomy-master-plan.md` | 100만 KW·L4 설계 (§4, §6, §8, §9) |
| `data2/pilot/phase1_l3_l4_plan.csv` | L4 엔진·축·규모 계획 |
| `data2/pilot/phase1_l3_l4_summary.json` | Phase1 합계·규칙 |

### 권장 (코드)

| 파일 | 이유 |
|------|------|
| `scripts/pilot_l3_review_batch.py` | L3 오케스트레이션·merge·topup |
| `scripts/test_l3_diversity.py` | slot_2step 코어·filter |
| `scripts/curate_l3_slot2step.py` | L3 후처리 |
| `scripts/taxonomy_state.py` | DB·스키마 |

### 권장 (데이터 샘플 — 전체 CSV는 너무 크면 샘플만)

| 파일 | 이유 |
|------|------|
| `data2/topics_l3_slot2step.csv` | L3 메인 5,805행 (또는 L2 5~10개 발췌) |
| `data2/topics_l2.csv` | L2 603 |
| `data2/archive/topics_l3_slot2step_dropped.csv` | curate 제거 499건·사유 |

### 선택 (비교·맥락)

| 파일 | 이유 |
|------|------|
| `data2/topics_l3.csv` | 구본 Nemotron 210 L2 (2트랙 정책 검토) |
| `data2/brief.txt` | L3 생성 기획 힌트 |
| `scripts/groq_client.py` | API 한도·키 로테이션 |

---

## 전체 프롬프트 (복사용)

```
당신은 대량 SEO·키워드 택소노미·콘텐츠 창고 설계 전문가입니다.
아래 프로젝트의 **현재 구현을 비판적으로 검토**하고, **더 나은 설계가 있으면 제안**하며, **앞으로 6~12개월 설계 원칙과 로드맵**을 구체적으로 작성해 주세요.

---

## 프로젝트 한 줄

네이버형 대량 SEO 블로그용 **L1→L2→L3 시드→L4 롱테일 창고(~100만 KW)→발행(10~30만 URL)** 파이프라인.
Python 스크립트 + CSV/SQLite. LLM은 L3·시드·본문에만 쓰고, L4 geo 등은 **program fan-out**(토큰 0)을 권장.

---

## 현재 상태 (2026-06-10, 첨부 docs/data2-system.md 기준)

| 항목 | 상태 |
|------|------|
| L1 | 14개 고정 |
| L2 | 603개 |
| L3 메인 | `topics_l3_slot2step.csv` — **5,805행 / 393 L2**, model=slot_2step, **L2당 cap 15** |
| L3 구본 | `topics_l3.csv` — 11,099행 / 210 L2 (Nemotron, **메인과 merge 안 함**) |
| L3 생성 | **완료** (missing L2 = 0). 62 L2만 15 미만 (topup ~90건 가능) |
| L3 후처리 | `curate_l3_slot2step.py`로 dedup+cap (499건 dropped) |
| L4 | **미시작** (fanout 스크립트 없음) |
| 본문 | 10건 파일럿 |

### L3 파이프라인 (slot_2step)

1. Groq Scout: L2당 검색 의도 **슬롯** 생성
2. Gemini lite: 슬롯 stem 중복 제거
3. Groq Scout: 슬롯 1개당 L3 1개 (focus_keyword, title_ko, slug, intent, angle)
4. 코드: `filter_diverse()` — stem·suffix·intent×angle cap

메인 스크립트: `pilot_l3_review_batch.py`

### 확정 정책 (깨지 말 것)

- L3 **메인 트랙은 slot2step CSV만** — 구본 210 L2와 합치지 않음
- L2당 L3 **최대 15**
- L4 **1개 = primary KW 1개** (intent×angle 기계 곱으로 URL만 늘리기 금지)
- YMYL (02 경제, 03 건강): hold·면책 강화
- **API 한도**: Groq Scout TPD 50만 토큰/키/일, 키 2개 로테이션. 429는 TPD 소진이 흔함
- **계정 쪼개기·한도 우회·탐지 회피는 제안하지 말 것**

### Phase1 파일럿 (다음 검증 단위)

- 25 L2 (`phase1_l3_l4_plan.csv`)
- L3 cap 합 304, L4 창고 추정 ~1.1만, M1 발행 URL 목표 286
- L4 엔진: program (geo) / llm (howto) / hybrid

### 알려진 이슈

- 문서 일부 구식 (84 L2 시점)
- 과거 merge 중복 → curate로 해소 (over-cap 0)
- `generate_taxonomy_l3.py`(Nemotron) vs `pilot_l3_review_batch.py`(메인) 이원화
- L4 미구현이라 100만 KW 경로는 설계만 있음

---

## 당신에게 요청하는 것

### 1) 현재 시스템 검토 (비판 포함)

다음 관점에서 **구체적으로** 평가해 주세요. 각 항목마다 **유지 / 수정 / 폐기** 중 하나와 근거를 적어 주세요.

- **계층 설계**: L1→L2→L3(15 cap)→L4→본문 구조가 100만 창고·10~30만 발행에 맞는가?
- **2트랙 L3** (393 slot2step + 210 Nemotron 분리): 장기적으로 맞는가? 통합·재생성·폐기 중 무엇?
- **slot_2step 파이프라인**: Scout 슬롯→Gemini dedup→Scout fill→코드 검증 — 비용·품질·확장성
- **중복 제어**: `filter_diverse` + `curate_l3_slot2step` 이중 구조, stem 유사도
- **데이터 모델**: l3_id, search_intent, topic_angle, CSV+SQLite 이중 관리
- **운영**: checkpoint, resume, merge idempotency, API 429 대응
- **Phase1→전체 스케일**: 25 L2 검증 후 603 L2 fan-out 경로가 타당한가?

**반드시 포함**: 치명적 리스크 3개, 설계상 허점 3개, 잘한 점 3개.

### 2) 더 나은 설계 제안 (있으면)

「현재보다 낫다」고 판단되는 부분만 **대안 아키텍처**를 제시해 주세요.

- 전면 교체가 아니라 **점진적 마이그레이션** 가능한 형태로
- 각 대안마다: **장점 / 단점 / 마이그레이션 비용(대·중·소) / 우리 상황에 맞는지**
- 특히 다음을 다루어 주세요:
  - L3 생성 방식 (slot_2step 유지 vs 개선 vs 대체)
  - L4 fan-out (program / llm / hybrid) 스키마·파이프라인
  - **창고 vs 발행** 분리 (tier, publish_l3_cap, curated 큐)
  - 구본 210 L2 처리 전략
  - `keyword/es` 엔티티와의 연계 (100만 경로)

가능하면 **간단한 다이어그램**(텍스트/mermaid)과 **L4 행 스키마 초안**을 포함해 주세요.

### 3) 앞으로 설계해야 하는 방식 (6~12개월)

「앞으로 이렇게 설계하라」는 **원칙·규칙·단계**를 문서화해 주세요.

- **설계 원칙** 5~10개 (예: program 우선, LLM은 시드만, …)
- **단계별 로드맵**: Phase1(검증) → Phase2(L4 본격) → Phase3(100만·발행 큐)
- **각 단계 산출물** (파일명·스키마·대략 규모)
- **의사결정 트리**: L2 타입별 L4 엔진 선택, hold/publish 분기
- **안 하면 안 되는 것** (anti-patterns) 5개
- **지금 당장 다음 2주 작업** 우선순위 5개 (구체적)

### 4) L4 설계 심화 (핵심)

L4가 아직 없습니다. 다음을 **구체 설계**해 주세요.

- L4 1행 스키마 제안 (필드·예시 1행)
- program fan-out 알고리즘 (geo: 구×메뉴 등) 의사코드 수준
- hybrid/llm은 **어디까지** LLM을 쓸지 (시드만 vs variant 생성)
- Phase1 25 L2 기준 **예상 L4 건수**와 검증 방법
- slot2step 393 L2 전체 fan-out 시 **현실적 상한·하한** (우리 L3 5,805 기준)

---

## 출력 형식 (이 구조를 반드시 따를 것)

### A. Executive Summary (5~10문장)

### B. 현재 시스템 평가

| 영역 | 판정 (유지/수정/폐기) | 요약 | 근거 |
|------|----------------------|------|------|
| … | … | … | … |

### C. 리스크·강점

- 치명적 리스크 3
- 설계 허점 3
- 잘한 점 3

### D. 대안 설계 (현재보다 나은 경우만)

#### D-1. [대안 이름]
- 개요
- 장단점
- 마이그레이션
- 권장 여부 (예/아니오/조건부)

(필요 시 mermaid 다이어그램)

### E. 권장 target 아키텍처 (최종안 1개)

- 계층·파일·스크립트 구조
- L3/L4/발행 큐 흐름

### F. L4 스키마·파이프라인 초안

- 필드 정의
- fan-out 규칙
- Phase1 예시

### G. 로드맵 (6~12개월)

| 단계 | 기간 | 목표 | 산출물 | 성공 기준 |
|------|------|------|--------|-----------|

### H. 즉시 실행 액션 (2주)

1. …
2. …
(우선순위 순, 각 1~2문장)

### I. 열린 질문 (우리가 결정해야 할 것)

- …

---

## 제약·가정

- 한국어 네이버 SEO 롱테일 대상
- 소규모 팀, API Free/low tier (Groq TPD, Gemini 일 한도)
- 품질·중복 최소화 > 무조건 건수
- Python + CSV/SQLite 스택 유지 (전면 리플랫폼 비권장)
- 첨부 파일 내용을 인용할 때는 파일명·컬럼명을 명시할 것
- 확실하지 않은 부분은 **가정**이라고 표시할 것

첨부한 파일을 읽은 뒤, 위 출력 형식으로 답변해 주세요.
```

---

## 짧은 버전 (토큰 절약)

Claude 컨텍스트가 부족할 때:

```
첨부 `docs/data2-system.md` + `phase1_l3_l4_plan.csv` 기준으로:

1) L3 slot_2step + 2트랙 + cap15 시스템 검토 (유지/수정/폐기 표)
2) 더 나은 대안 설계 (있으면, 마이그레이션 포함)
3) L4 스키마·fan-out·Phase1→100만 로드맵
4) 2주 액션 5개

정책: L4=primary KW 1개, 구본210과 merge 금지, API우회 제안 금지.
출력: Executive Summary → 평가표 → 리스크3/허점3/강점3 → target 아키텍처 → L4 초안 → 로드맵 → 액션.
```

---

## Claude 응답 후 우리가 할 일

| Claude 답변 섹션 | 활용 |
|------------------|------|
| **E. target 아키텍처** | `docs/data2-system.md` · master-plan 갱신 |
| **F. L4 스키마** | `fanout_l4_geo.py` 구현 스펙 |
| **G. 로드맵** | §13 체크리스트 교체 |
| **H. 2주 액션** | Cursor 작업 백로그 |
| **I. 열린 질문** | 사용자 결정 후 rule 문서화 |

---

## 프롬프트 커스터마이즈 팁

검토 초점을 바꿀 때 프롬프트 **「당신에게 요청하는 것」** 앞에 한 줄 추가:

| 초점 | 추가 문장 |
|------|-----------|
| L4만 | «§1·2는 간략히, §4 L4 설계를 최대한 구체적으로» |
| L3 품질만 | «L3 5,805 샘플의 stem 다양성·cannibalization 위주로» |
| 발행 전략 | «창고 100만 vs 발행 10~30만 큐 설계를 최우선» |
| 구본 210 | «Nemotron 210 L2를 폐기/통합/유지 중 무엇을 권하는지 명확히» |
| API 비용 | «Groq TPD 2키 기준 월간 처리량·배치 전략 포함» |

---

*이 프롬프트는 `docs/data2-system.md`와 쌍으로 유지할 것.*
