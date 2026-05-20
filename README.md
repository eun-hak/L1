# L1 — 블로그 SEO 카테고리

블로그형 **L1 72개** + **L2 시드(니치)** 카테고리 데이터입니다.

## 파일

| 파일 | 설명 |
|------|------|
| `data/topics_l1.csv` | L1 마스터 (72행) |
| `data/topics_l2.csv` | L2 시드 (L1당 8개, 576행) |
| `data/topics_l3.csv` | L3 고유 주제 (~10,368행, L2당 18개) |
| `data/questions.csv` | SEO 제목 1:1 (~10,368행) |
| `scripts/focus_phrases.py` | 메가그룹별 focus 문장 풀 |
| `scripts/generate_categories.py` | L1·L2 CSV 재생성 |
| `scripts/generate_l3_questions.py` | L3·질문 CSV 생성 |

## CSV 스키마

### topics_l1.csv

```text
l1_code   # 01-72
l1_slug   # URL용 영문 slug
name_ko   # 표시명
mega_group # 라이프|머니|홈|푸드|스타일|바디|패밀리|트립|플레이|테크|인포
sort_order
```

### topics_l2.csv

```text
l2_id     # 예: 24-pasta
l1_code   # 부모 L1
l2_slug
name_ko
sort_order
```

## 재생성

```bash
# L1, L2
python3 scripts/generate_categories.py

# L3, 질문 (L2 CSV 필요)
python3 scripts/generate_l3_questions.py
```

### topics_l3.csv

- **L2당 18개** 서로 다른 `focus_keyword` (TARGET 1만 기준)
- 같은 L2에 SEO 꼬리만 바꾼 중복 없음
- `focus_keyword` = 실제 글 주제, `question_text` = SEO 제목

개수 조절: `scripts/generate_l3_questions.py` 의 `TARGET_QUESTIONS = 10000`

```text
l3_id, l2_id, l1_code, l3_slug, title_ko, topic_angle
```

### questions.csv

- **L3와 1:1** (`question_id` = `l3_id`)
- `question_text` = SEO 블로그 제목 (검색형)

**제목 패턴 (`seo_format`)**

| 형식 | 예시 |
|------|------|
| `summary` | `일기·기록, 효과와 실제 변화 총정리` |
| `vs` | `양식 vs 파스타 비용, 어떤 것이 더 나을까` |
| `pipe` | `일기·기록 완벽 정리 \| 초보자 가이드와 체크리스트` |
| `ask` | `재테크·절약, 비용·가격 얼마나 드나?` |

```text
question_id, l3_id, l2_id, l1_code, question_type, seo_format, question_text
```

## 다음 단계

- L2 추가·수정: `L2_SEEDS` 편집 → `generate_categories.py` → `generate_l3_questions.py`
- L3 확장: `TOPIC_BANK`·`L3_PER_L2` 수정 (`generate_l3_questions.py`)
## 답변 (블로그 본문)

| 경로 | 설명 |
|------|------|
| `data/answers/body/{question_id}.md` | 마크다운 본문 (~1500~1700자, template) |
| `data/answers/answers_index.csv` | 메타·글자수·파일 경로 |

```bash
# 샘플 10개
python3 scripts/generate_answers.py --limit 10

# 전체 (~10,368개, API 없음)
python3 scripts/generate_answers.py --all
```

- **용량(전체):** 약 **17MB** 원문 / **~6MB** gzip 예상
- **품질:** `template` = 구조화 초안 / 고품질은 `llm` 모드(API) 예정
