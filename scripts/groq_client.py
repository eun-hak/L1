"""
Groq API 클라이언트 (OpenAI 호환).

환경변수: GROQ_API_KEY (또는 .env의 GROK_API_KEY)
"""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")
load_dotenv(ROOT / ".env.local")

GROQ_BASE_URL = "https://api.groq.com/openai/v1"

# 용도별 모델
MODEL_FAST = "llama-3.1-8b-instant"  # 대량 제목/키워드
MODEL_DRAFT = "qwen/qwen3-32b"  # 본문 초안/요약
MODEL_QUALITY = "llama-3.3-70b-versatile"  # 고품질 글
MODEL_LONG = "meta-llama/llama-4-scout-17b-16e-instruct"  # 긴 문서


def get_api_key() -> str:
    key = os.getenv("GROQ_API_KEY") or os.getenv("GROK_API_KEY")
    if not key:
        raise RuntimeError("GROQ_API_KEY 환경변수가 필요합니다.")
    return key


def create_client() -> OpenAI:
    return OpenAI(api_key=get_api_key(), base_url=GROQ_BASE_URL)


def chat(
    prompt: str,
    *,
    system: str = "너는 한국어 SEO 블로그 글을 잘 쓰는 도우미야.",
    model: str = MODEL_FAST,
    temperature: float = 0.7,
    retries: int = 4,
) -> str:
    client = create_client()
    last_err: Exception | None = None
    for attempt in range(retries):
        try:
            response = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": prompt},
                ],
                temperature=temperature,
            )
            return response.choices[0].message.content or ""
        except Exception as exc:
            last_err = exc
            if "429" in str(exc) or "rate_limit" in str(exc).lower():
                time.sleep(4 * (attempt + 1))
                continue
            raise
    raise last_err  # type: ignore[misc]


def parse_json_array(text: str) -> list[dict]:
    """Groq 응답에서 JSON 배열 추출."""
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)

    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", " ", text)

    for candidate in (text,):
        try:
            data = json.loads(candidate)
            if isinstance(data, list):
                return [item for item in data if isinstance(item, dict)]
        except json.JSONDecodeError:
            pass

    match = re.search(r"\[[\s\S]*\]", text)
    if match:
        chunk = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", " ", match.group())
        for fixed in (chunk, re.sub(r",\s*]", "]", chunk)):
            try:
                data = json.loads(fixed)
                if isinstance(data, list):
                    return [item for item in data if isinstance(item, dict)]
            except json.JSONDecodeError:
                # 여러 JSON이 붙은 경우 첫 배열만 추출
                decoder = json.JSONDecoder()
                try:
                    data, _ = decoder.raw_decode(fixed)
                    if isinstance(data, list):
                        return [item for item in data if isinstance(item, dict)]
                except json.JSONDecodeError:
                    pass

    raise ValueError(f"JSON 배열을 파싱하지 못했습니다: {text[:200]}...")


def parse_json_object(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("JSON 객체가 아닙니다.")
    return data


TAXONOMY_SYSTEM = (
    "너는 한국어 네이버 블로그 SEO 카테고리 기획자야. "
    "뻔하고 넓은 대분류(일상·재테크·인테리어·자기계발 등)를 피하고, "
    "검색 수요가 있는 구체적·니치한 카테고리를 설계한다. "
    "반드시 유효한 JSON만 출력한다."
)


def generate_l1_categories(
    *,
    brief: str,
    count: int,
    avoid_names: list[str] | None = None,
    model: str = MODEL_FAST,
) -> list[dict[str, str]]:
    avoid = "\n".join(f"- {n}" for n in (avoid_names or [])) or "- (없음)"
    prompt = f"""아래 기획 방향에 맞는 블로그 L1(대분류) {count}개를 **처음부터 새로** 설계해줘.
기존 블로그 카테고리를 복사하지 말고, 기획 방향에 맞는 독창적인 축을 만들어.

[기획 방향]
{brief}

[이미 사용 중인 L1 이름 — 절대 중복 금지]
{avoid}

규칙:
1. name_ko: 2~8자, 블로그 메뉴에 바로 쓸 수 있는 이름
2. theme: L1을 한 줄로 설명 (10~30자)
3. description: 어떤 글을 쓸 분류인지 1~2문장
4. 서로 다른 검색/관심 영역이어야 함
5. JSON 배열만 출력

형식:
[
  {{"name_ko": "...", "theme": "...", "description": "..."}}
]
"""
    raw = chat(prompt, system=TAXONOMY_SYSTEM, model=model, temperature=0.9)
    items = parse_json_array(raw)
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in items:
        name = str(item.get("name_ko", "")).strip()
        if not name or name in seen:
            continue
        seen.add(name)
        out.append(
            {
                "name_ko": name,
                "theme": str(item.get("theme", "")).strip(),
                "description": str(item.get("description", "")).strip(),
            }
        )
    return out[:count]


SEO_PIPE_RIGHT_EXAMPLES = [
    "초보자를 위한 가이드",
    "퍼스트네임과 라스트네임 올바르게 쓰는 방법",
    "초보자를 위한 쉬운 표현",
    "얻는 방법과 순서 정리",
    "개념과 차이점 한눈에 보기",
    "실전에서 바로 쓰는 팁",
    "비교와 선택 기준",
    "자주 하는 실수와 해결법",
    "필요한 준비물과 절차",
    "원리와 적용 예시",
]


def _fallback_pipe_title(keyword: str, desc: str = "") -> str:
    tail = SEO_PIPE_RIGHT_EXAMPLES[hash(keyword) % len(SEO_PIPE_RIGHT_EXAMPLES)]
    if desc and 8 <= len(desc) <= 28:
        return f"{keyword} | {desc}"
    return f"{keyword} | {tail}"


def generate_seo_questions_batch(
    items: list[dict[str, str]],
    *,
    model: str = MODEL_FAST,
) -> list[dict[str, str]]:
    """엔티티 키워드 → 네이버 SEO pipe 제목 (키워드 | 총정리 형식)."""
    out: list[dict[str, str]] = []

    for item in items:
        desc = (item.get("desc_ko") or "").strip()
        kw = item["keyword_ko"]
        prompt = f"""키워드로 네이버 블로그 SEO 제목 1개를 만들어줘.

- qid: {item['qid']}
- 키워드: {kw}
- 설명: {desc or '(없음)'}

[형식 — 반드시 pipe 1개]
"앞: 검색 주제/키워드가 자연스럽게 들어간 제목 | 뒤: 구체적이고 자연스러운 SEO 꼬리"

[좋은 예시 — 이런 톤으로]
- 스타크래프트 테란 핵 제조 방법 | 초보자를 위한 가이드
- 영문 이름 작성법 | 퍼스트네임과 라스트네임 올바르게 쓰는 방법
- 어제 뭐 했는지 영어로 말하기 | 초보자를 위한 쉬운 표현
- 던파 녹슨 철조각 파밍 | 얻는 방법과 효율적인 루트
- 음속과 온도의 관계 | 소리 속도가 달라지는 이유

[나쁜 예시 — 금지]
- "~총정리"만 반복 (매번 총정리 X)
- "~인가요?", "~할까요?" 질문형
- 뒷부분이 너무 짧거나 뻔함 (예: "| 핵심 정리", "| 완벽 가이드"만 단독)

[규칙]
1. seo_question: "|" 1개, 30~55자
2. 앞부분: 사람이 검색할 주제 문장 (방법/가이드/이유/표현/비교 등)
3. 뒷부분: **총정리 없이** 구체적 가치 제시 (초보자 가이드, 올바르게 쓰는 방법, 쉬운 표현, 차이점, 실전 팁 등)
4. "총정리"는 10개 중 1~2개만 써도 됨. 남용 금지.
5. search_intent: info|howto|compare|checklist|review
6. seo_format: "pipe"
7. JSON 객체 1개만

{{"qid":"{item['qid']}","seo_question":"... | ...","search_intent":"howto","seo_format":"pipe"}}
"""
        raw = chat(
            prompt,
            system=(
                "너는 네이버 블로그 SEO 제목 작가다. "
                "'주제 | 자연스러운 SEO 꼬리' 형식. "
                "총정리 남용 금지. 질문형 금지. JSON만 출력."
            ),
            model=model,
            temperature=0.82,
        )
        text = raw.strip()
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*", "", text)
            text = re.sub(r"\s*```$", "", text)

        gen: dict = {}
        try:
            gen = json.loads(text)
        except json.JSONDecodeError:
            match = re.search(r"\{[\s\S]*\}", text)
            if match:
                try:
                    gen = json.loads(match.group())
                except json.JSONDecodeError:
                    pass

        question = str(gen.get("seo_question", "")).strip()
        if "|" not in question:
            question = _fallback_pipe_title(kw, desc)
        # 질문형 톤 제거
        question = question.rstrip("?").replace("?", "")

        out.append(
            {
                "question_id": f"{item['qid']}-seo",
                "qid": item["qid"],
                "keyword_ko": kw,
                "keyword_en": item.get("keyword_en", ""),
                "popularity": str(item.get("popularity", "")),
                "seo_question": question,
                "search_intent": str(gen.get("search_intent", "info")).strip() or "info",
                "seo_format": "pipe",
                "model": model,
            }
        )
        time.sleep(1.2)

    return out


def generate_l2_categories(
    *,
    brief: str,
    l1_name: str,
    l1_theme: str,
    l1_description: str,
    count: int,
    avoid_names: list[str] | None = None,
    model: str = MODEL_FAST,
) -> list[dict[str, str]]:
    avoid = "\n".join(f"- {n}" for n in (avoid_names or [])) or "- (없음)"
    prompt = f"""L1 대분류 아래 L2(중분류) {count}개를 **새로** 만들어줘.

[기획 방향]
{brief}

[L1] {l1_name}
- theme: {l1_theme}
- description: {l1_description}

[중복 금지 L2 이름 — 다른 L1에 이미 있는 이름도 절대 쓰지 말 것]
{avoid}

규칙:
1. L1 범위 안에서만, 실제 블로그 카테고리/메뉴처럼
2. name_ko: 2~12자, 서로 다른 하위 주제
3. description: 이 L2에서 다룰 글 유형 1문장
4. JSON 배열만 출력

형식:
[
  {{"name_ko": "...", "description": "..."}}
]
"""
    raw = chat(prompt, system=TAXONOMY_SYSTEM, model=model, temperature=0.88)
    items = parse_json_array(raw)
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in items:
        name = str(item.get("name_ko", "")).strip()
        if not name or name in seen:
            continue
        seen.add(name)
        out.append(
            {
                "name_ko": name,
                "description": str(item.get("description", "")).strip(),
            }
        )
    return out[:count]


def generate_l3_topics(
    *,
    brief: str,
    l1_name: str,
    l2_name: str,
    l2_description: str,
    count: int,
    avoid_keywords: list[str] | None = None,
    model: str = MODEL_FAST,
) -> list[dict[str, str]]:
    avoid = "\n".join(f"- {k}" for k in (avoid_keywords or [])[:40]) or "- (없음)"
    prompt = f"""L2 중분류 아래 L3(실제 글 주제/검색 키워드) {count}개를 **새로** 만들어줘.

[기획 방향]
{brief}

[L1] {l1_name}
[L2] {l2_name} — {l2_description}

[중복 금지 키워드]
{avoid}

규칙:
1. focus_keyword: 네이버 검색에 걸릴 2~12단어 한국어 (의도가 서로 달라야 함)
2. seo_title: 클릭 유도 SEO 제목 (물음표/VS/총정리/방법/후기 등 다양하게)
3. search_intent: info|howto|compare|cost|checklist|review|news 중 하나
4. topic_angle: start|howto|compare|tip|review|issue|local 중 하나
5. JSON 배열만 출력

형식:
[
  {{
    "focus_keyword": "...",
    "seo_title": "...",
    "search_intent": "howto",
    "topic_angle": "tip"
  }}
]
"""
    raw = chat(prompt, system=TAXONOMY_SYSTEM, model=model, temperature=0.9)
    items = parse_json_array(raw)
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in items:
        focus = str(item.get("focus_keyword", "")).strip()
        if not focus or focus in seen:
            continue
        seen.add(focus)
        out.append(
            {
                "focus_keyword": focus,
                "seo_title": str(item.get("seo_title", "")).strip() or focus,
                "search_intent": str(item.get("search_intent", "info")).strip() or "info",
                "topic_angle": str(item.get("topic_angle", "tip")).strip() or "tip",
            }
        )
    return out[:count]


def generate_l2_keywords(
    *,
    l1_name: str,
    l2_name: str,
    mega_group: str,
    existing_keywords: list[str],
    count: int = 10,
    model: str = MODEL_FAST,
) -> list[dict[str, str]]:
    """L1/L2 맥락으로 블로그 focus_keyword + SEO 제목 생성."""
    avoid = "\n".join(f"- {kw}" for kw in existing_keywords[:30]) or "- (없음)"
    prompt = f"""다음 카테고리에 맞는 네이버 블로그 SEO 키워드를 {count}개 만들어줘.

[L1] {l1_name} ({mega_group})
[L2] {l2_name}

[중복 금지 — 아래와 비슷한 표현 쓰지 말 것]
{avoid}

규칙:
1. focus_keyword: 실제 검색할 2~10단어 한국어 (상황·대상·방법·비교 등 의도가 서로 달라야 함)
2. seo_title: 클릭 유도 SEO 제목 (물음표/VS/총정리/가이드 등 다양하게)
3. search_intent: info|howto|compare|cost|checklist|review 중 하나
4. JSON 배열만 출력 (설명 없이)

형식:
[
  {{"focus_keyword": "...", "seo_title": "...", "search_intent": "howto"}}
]
"""
    raw = chat(
        prompt,
        system=(
            "너는 한국어 블로그 SEO 키워드 리서처야. "
            "검색 의도가 겹치지 않게 다양한 롱테일 키워드를 만든다. "
            "반드시 유효한 JSON 배열만 출력한다."
        ),
        model=model,
        temperature=0.85,
    )
    items = parse_json_array(raw)
    cleaned: list[dict[str, str]] = []
    seen: set[str] = set()

    for item in items:
        focus = str(item.get("focus_keyword", "")).strip()
        title = str(item.get("seo_title", "")).strip()
        intent = str(item.get("search_intent", "info")).strip() or "info"
        if not focus or focus in seen:
            continue
        seen.add(focus)
        cleaned.append(
            {
                "focus_keyword": focus,
                "seo_title": title or focus,
                "search_intent": intent,
            }
        )

    return cleaned[:count]


def generate_blog_titles(keyword: str, count: int = 10) -> str:
    return chat(
        f"{keyword} 관련 블로그 제목 {count}개 추천해줘. 번호 목록으로만 답해.",
        system="너는 한국어 블로그 제목과 키워드를 잘 뽑는 도우미야.",
        model=MODEL_FAST,
    )


def strip_model_artifacts(text: str) -> str:
    """모델 추론 블록·중복 H1 제거."""
    for tag in ("think", "redacted_thinking"):
        open_tag, close_tag = f"<{tag}>", f"</{tag}>"
        while open_tag in text:
            start = text.find(open_tag)
            end = text.find(close_tag, start)
            if end == -1:
                text = text[:start]
                break
            text = text[:start] + text[end + len(close_tag) :]

    lines = text.strip().splitlines()
    cleaned: list[str] = []
    seen_h1 = False
    for line in lines:
        if line.strip().startswith("# ") and not line.strip().startswith("##"):
            if seen_h1:
                continue
            seen_h1 = True
        cleaned.append(line)
    return "\n".join(cleaned).strip()


def generate_blog_body(
    question_text: str,
    focus_keyword: str,
    *,
    min_chars: int = 1500,
    max_chars: int = 3000,
    model: str = MODEL_DRAFT,
) -> str:
    prompt = f"""다음 SEO 블로그 글을 마크다운으로 작성해줘.

- 제목(H1): {question_text}
- 핵심 키워드: {focus_keyword}
- 분량: {min_chars}~{max_chars}자 (공백 포함)
- 구조: 핵심 한 줄 → 도입 → 본문 섹션 2~3개 → 체크리스트 → FAQ 1~2개 → 마무리
- 톤: 실용적, 입문자 친화, 과장 없이
- 마크다운만 출력 (코드블록·추론 과정 없이)
"""
    raw = chat(
        prompt,
        system="너는 한국어 SEO 블로그 본문을 작성하는 전문 작가야. 추론 과정은 출력하지 말고 최종 글만 작성해.",
        model=model,
        temperature=0.6,
    )
    return strip_model_artifacts(raw)


def _split_intro(body: str) -> tuple[str, str]:
    """H1·도입부와 나머지 본문 분리."""
    lines = body.strip().splitlines()
    if not lines:
        return "", ""

    rest_idx = len(lines)
    seen_content = False
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("##"):
            rest_idx = i
            break
        if stripped and not stripped.startswith("#"):
            seen_content = True
        elif seen_content and not stripped and i + 1 < len(lines) and lines[i + 1].strip().startswith("##"):
            rest_idx = i
            break

    intro = "\n".join(lines[:rest_idx]).strip()
    rest = "\n".join(lines[rest_idx:]).strip()
    return intro, rest


def polish_intro(
    seo_title: str,
    focus_keyword: str,
    body: str,
    *,
    model: str = MODEL_QUALITY,
) -> str:
    """도입부만 고품질 모델로 다듬어 본문에 반영 (TPD 절약)."""
    intro, rest = _split_intro(body)
    if len(intro) < 80:
        return body

    prompt = f"""아래 블로그 글의 도입부만 더 자연스럽고 읽기 좋게 다듬어줘.

- SEO 제목: {seo_title}
- 핵심 키워드: {focus_keyword}
- 규칙: 도입부만 출력, H1(#) 유지, 과장·clickbait 금지, 키워드 자연스럽게 1회 포함
- 분량: 기존과 비슷하거나 약간 짧게

[현재 도입부]
{intro}
"""
    polished = chat(
        prompt,
        system="너는 한국어 SEO 블로그 도입부를 다듬는 편집자야.",
        model=model,
        temperature=0.45,
    ).strip()

    if polished.startswith("```"):
        polished = re.sub(r"^```(?:markdown)?\s*", "", polished)
        polished = re.sub(r"\s*```$", "", polished).strip()

    polished = strip_model_artifacts(polished)
    if not polished:
        return body
    return f"{polished}\n\n{rest}" if rest else polished


def generate_meta_tags(
    seo_title: str,
    focus_keyword: str,
    *,
    model: str = MODEL_FAST,
) -> dict[str, str]:
    """메타 설명·태그 (8B 대량 작업)."""
    prompt = f"""SEO 블로그용 메타 정보를 JSON으로 만들어줘.

- 제목: {seo_title}
- 키워드: {focus_keyword}

출력 형식 (JSON만):
{{"meta_description": "120~160자", "tags": ["태그1", "태그2", "태그3", "태그4", "태그5"]}}
"""
    raw = chat(
        prompt,
        system="너는 한국어 SEO 메타 태그 작성 도우미야. JSON만 출력.",
        model=model,
        temperature=0.5,
    ).strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        match = re.search(r"\{[\s\S]*\}", raw)
        data = json.loads(match.group()) if match else {}
    tags = data.get("tags", [])
    if isinstance(tags, list):
        tags_str = ", ".join(str(t) for t in tags[:5])
    else:
        tags_str = str(tags)
    return {
        "meta_description": str(data.get("meta_description", "")).strip(),
        "tags": tags_str,
    }
