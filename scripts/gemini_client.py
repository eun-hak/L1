"""
Gemini API 클라이언트.

모델 라우팅 (일일 무료 한도 기준):
- MODEL_FAST      gemini-3.1-flash-lite   제목·메타·키워드
- MODEL_DRAFT     gemini-2.5-flash        본문 초안
- MODEL_QUALITY   gemini-2.5-pro          도입부 폴리시 (소량)
- *_FALLBACK      한도 초과 시 자동 전환

환경변수: GEMINI_API_KEY2 (L1 bulk 기본) / GEMINI_API_KEY_2 / GEMINI_API_KEY
"""

from __future__ import annotations

import json
import logging
import os
import random
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from google import genai
from google.genai import types

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")
load_dotenv(ROOT / ".env.local")

logger = logging.getLogger(__name__)

# ============================================================
# 모델 라우팅
# ============================================================
MODEL_FAST = "gemini-3.1-flash-lite"
MODEL_FAST_FALLBACK = "gemini-2.5-flash-lite"
MODEL_DRAFT = "gemini-2.5-flash"
MODEL_DRAFT_FALLBACK = "gemini-3.5-flash"
MODEL_QUALITY = "gemini-2.5-pro"
MODEL_QUALITY_FALLBACK = "gemini-2.5-flash"
MODEL_LONG = MODEL_DRAFT

MODEL_FALLBACKS: dict[str, str] = {
    MODEL_FAST: MODEL_FAST_FALLBACK,
    MODEL_DRAFT: MODEL_DRAFT_FALLBACK,
    MODEL_QUALITY: MODEL_QUALITY_FALLBACK,
}

# ============================================================
# 본문 톤 가이드 (example.jsonl 기준)
# ============================================================
AIQA_BODY_SYSTEM = (
    "너는 한국어 정보 Q&A 답변 작가다. "
    "블로그형 SEO 글이지만 딱딱한 템플릿·기계적 목차는 쓰지 않는다. "
    "친절하고 설명적인 존댓말(~합니다, ~하세요)로, 독자가 궁금해하는 내용을 풀어쓴다. "
    "추론 과정 없이 최종 답변만 출력한다."
)

AIQA_BODY_STYLE_RULES = """
[말투]
- "~하시는 분들이 많습니다", "결론부터 말씀드리자면", "이 글에서는 ~알아보겠습니다" 같은 자연스러운 도입
- 과장·clickbait·AI 티 나는 문장 금지 ("완벽한 가이드", "놓치면 후회", "꼭 알아야 할" 등)
- 인물 언급 시 ~씨, 정보는 단정적·실용적으로

[구조]
- H1(#) 제목은 쓰지 말 것 (제목은 별도 필드)
- 도입: 2~3개 일반 문단으로 바로 시작
- 본문: 소제목은 **굵은 한 줄** 형식 (예: **출연 무산의 배경**)
- ## 본문1, ## FAQ, ## 체크리스트, ## 마무리 같은 템플릿 제목 절대 금지
- 필요하면 ### 소제목·번호 목록·불릿 사용
- 마지막은 **결론** 또는 **결론적으로** 로 자연스럽게 마무리
- --- 구분선, "본문 1:", "FAQ:" 같은 메타 라벨 금지
"""

# ============================================================
# 클라이언트
# ============================================================

_client: genai.Client | None = None


def get_api_key() -> str:
    for name in ("GEMINI_API_KEY2", "GEMINI_API_KEY_2", "GEMINI_API_KEY"):
        key = os.getenv(name, "").strip()
        if key:
            return key
    raise RuntimeError("GEMINI_API_KEY2 (또는 GEMINI_API_KEY) 환경변수가 필요합니다.")


def create_client() -> genai.Client:
    global _client
    if _client is None:
        _client = genai.Client(api_key=get_api_key())
    return _client


# ============================================================
# 호출 래퍼: 재시도 + 모델 폴백
# ============================================================


@dataclass
class ChatResult:
    text: str
    model: str
    attempts: int


def _is_rate_or_quota_error(exc: Exception) -> bool:
    msg = str(exc).lower()
    return any(k in msg for k in ("429", "resource_exhausted", "quota", "rate limit", "too many requests"))


def _is_transient_error(exc: Exception) -> bool:
    msg = str(exc).lower()
    return any(k in msg for k in ("503", "502", "504", "timeout", "unavailable"))


def chat(
    prompt: str,
    *,
    system: str = "너는 한국어 SEO 블로그 글을 잘 쓰는 도우미야.",
    model: str = MODEL_FAST,
    temperature: float = 0.6,
    retries: int = 4,
    max_tokens: int | None = None,
) -> str:
    return chat_full(
        prompt,
        system=system,
        model=model,
        temperature=temperature,
        retries=retries,
        max_tokens=max_tokens,
    ).text


def chat_full(
    prompt: str,
    *,
    system: str,
    model: str,
    temperature: float = 0.6,
    retries: int = 4,
    max_tokens: int | None = None,
) -> ChatResult:
    client = create_client()
    last_err: Exception | None = None
    current_model = model
    used_fallback = False

    for attempt in range(retries):
        try:
            config_kwargs: dict[str, Any] = {
                "system_instruction": system,
                "temperature": temperature,
            }
            if max_tokens is not None:
                config_kwargs["max_output_tokens"] = max_tokens

            response = client.models.generate_content(
                model=current_model,
                contents=prompt,
                config=types.GenerateContentConfig(**config_kwargs),
            )
            text = (response.text or "").strip()
            return ChatResult(text=text, model=current_model, attempts=attempt + 1)

        except Exception as exc:
            last_err = exc
            if _is_rate_or_quota_error(exc) and not used_fallback:
                fallback = MODEL_FALLBACKS.get(model)
                if fallback and current_model != fallback:
                    logger.warning(
                        "Quota/rate limit on %s → fallback %s",
                        current_model,
                        fallback,
                    )
                    current_model = fallback
                    used_fallback = True
                    time.sleep(1)
                    continue
            if _is_rate_or_quota_error(exc) or _is_transient_error(exc):
                wait = (2 ** attempt) * 2 + random.uniform(0, 1.5)
                logger.warning("Retry in %.1fs (attempt %d, model=%s)", wait, attempt + 1, current_model)
                time.sleep(wait)
                continue
            raise

    assert last_err is not None
    raise last_err


# ============================================================
# JSON 파서
# ============================================================


def _strip_code_fence(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json|markdown)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    return text.strip()


def parse_json_array(text: str, *, raise_on_fail: bool = True) -> list[dict]:
    text = _strip_code_fence(text)
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", " ", text)

    try:
        data = json.loads(text)
        if isinstance(data, list):
            return [x for x in data if isinstance(x, dict)]
    except json.JSONDecodeError:
        pass

    match = re.search(r"\[[\s\S]*\]", text)
    if match:
        chunk = match.group()
        chunk = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", " ", chunk)
        for candidate in (chunk, re.sub(r",\s*]", "]", chunk), re.sub(r",\s*}", "}", chunk)):
            try:
                data = json.loads(candidate)
                if isinstance(data, list):
                    return [x for x in data if isinstance(x, dict)]
            except json.JSONDecodeError:
                try:
                    data, _ = json.JSONDecoder().raw_decode(candidate)
                    if isinstance(data, list):
                        return [x for x in data if isinstance(x, dict)]
                except json.JSONDecodeError:
                    continue

    if raise_on_fail:
        raise ValueError(f"JSON 배열 파싱 실패: {text[:200]}...")
    logger.warning("JSON array parse failed, returning []: %s", text[:120])
    return []


def parse_json_object(text: str, *, raise_on_fail: bool = True) -> dict:
    text = _strip_code_fence(text)
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            return data
    except json.JSONDecodeError:
        pass

    match = re.search(r"\{[\s\S]*\}", text)
    if match:
        try:
            data = json.loads(match.group())
            if isinstance(data, dict):
                return data
        except json.JSONDecodeError:
            pass

    if raise_on_fail:
        raise ValueError(f"JSON 객체 파싱 실패: {text[:200]}...")
    return {}


# ============================================================
# 카테고리(택소노미) 생성
# ============================================================

TAXONOMY_SYSTEM = (
    "너는 한국어 네이버 블로그 SEO 카테고리 기획자야. "
    "검색 수요가 있는 구체적이고 실용적인 카테고리를 설계한다. "
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
    prompt = f"""아래 기획 방향에 맞는 블로그 L1(대분류) {count}개를 새로 설계해줘.

[기획 방향]
{brief}

[이미 사용 중인 L1 이름 — 절대 중복 금지]
{avoid}

[좋은 L1 기준]
- 월 검색 1000회 이상 예상되는 구체 영역
- 서로 다른 검색 의도/타깃을 가짐
- 블로그 메뉴에 바로 쓸 이름(2~8자)

규칙:
1. name_ko: 2~8자
2. theme: 한 줄 요약 (10~30자)
3. description: 어떤 글을 쓸 분류인지 1~2문장
4. JSON 배열만 출력

[
  {{"name_ko": "...", "theme": "...", "description": "..."}}
]
"""
    raw = chat(prompt, system=TAXONOMY_SYSTEM, model=model, temperature=0.85)
    items = parse_json_array(raw, raise_on_fail=False)
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in items:
        name = str(item.get("name_ko", "")).strip()
        if not name or name in seen:
            continue
        seen.add(name)
        out.append({
            "name_ko": name,
            "theme": str(item.get("theme", "")).strip(),
            "description": str(item.get("description", "")).strip(),
        })
    return out[:count]


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
    prompt = f"""L1 대분류 아래 L2(중분류) {count}개를 만들어줘.

[기획 방향]
{brief}

[L1] {l1_name}
- theme: {l1_theme}
- description: {l1_description}

[중복 금지 L2 이름 — 다른 L1에 있는 것도 금지]
{avoid}

규칙:
1. L1 범위 안에서만, 실제 블로그 메뉴처럼
2. name_ko: 2~12자, 서로 다른 하위 주제
3. description: 이 L2에서 다룰 글 유형 1문장
4. JSON 배열만 출력

[
  {{"name_ko": "...", "description": "..."}}
]
"""
    raw = chat(prompt, system=TAXONOMY_SYSTEM, model=model, temperature=0.85)
    items = parse_json_array(raw, raise_on_fail=False)
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in items:
        name = str(item.get("name_ko", "")).strip()
        if not name or name in seen:
            continue
        seen.add(name)
        out.append({
            "name_ko": name,
            "description": str(item.get("description", "")).strip(),
        })
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
    prompt = f"""L2 중분류 아래 L3(실제 글 주제/검색 키워드) {count}개를 만들어줘.

[기획 방향]
{brief}

[L1] {l1_name}
[L2] {l2_name} — {l2_description}

[중복 금지 키워드]
{avoid}

규칙:
1. focus_keyword: 네이버 검색에 걸릴 2~12단어 한국어 (의도가 서로 달라야 함)
2. seo_title: 클릭 유도 SEO 제목
3. search_intent: info|howto|compare|cost|checklist|review|news 중 하나
4. topic_angle: start|howto|compare|tip|review|issue|local 중 하나
5. JSON 배열만 출력

[
  {{
    "focus_keyword": "...",
    "seo_title": "...",
    "search_intent": "howto",
    "topic_angle": "tip"
  }}
]
"""
    raw = chat(prompt, system=TAXONOMY_SYSTEM, model=model, temperature=0.88)
    items = parse_json_array(raw, raise_on_fail=False)
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in items:
        focus = str(item.get("focus_keyword", "")).strip()
        if not focus or focus in seen:
            continue
        seen.add(focus)
        out.append({
            "focus_keyword": focus,
            "seo_title": str(item.get("seo_title", "")).strip() or focus,
            "search_intent": str(item.get("search_intent", "info")).strip() or "info",
            "topic_angle": str(item.get("topic_angle", "tip")).strip() or "tip",
        })
    return out[:count]


# ============================================================
# SEO 제목
# ============================================================

SEO_FORMATS = ["natural", "comma", "colon", "pipe", "question"]

SEO_TITLE_EXAMPLES = [
    "애정의 상처, 친구에게 매몰찼던 순간의 후회와 화해 방법",
    "뮤지컬배우 김소현 재혼 남편 손준호와 러브스토리",
    "스타원랜디 징베 조합법 총정리",
    "구글 결제 제한 30분 해제 방법 및 주의사항 총정리",
    "천정명 진짜사나이 출연 불발 이유와 후일담",
    "위디스크 모바일 무료 이용 가능한 다른 플랫폼은?",
    "밍키넷 대체 사이트: 다양한 영상 콘텐츠 즐기는 방법",
    "on~ing, by~ing, in~ing 뜻 차이점 비교",
    "카카오톡 로그아웃 방법: PC 버전 및 모바일 완벽 정리",
    "파워블로그 애슐리 인더 월드 논란 총정리",
    "위디스크 중복 쿠폰 및 10만 포인트 쿠폰 발급 정보",
    "스타크래프트 테란 핵 제조 방법 | 초보자를 위한 가이드",
]

SEO_TITLE_SYSTEM = (
    "너는 네이버 블로그 SEO 제목 작가다. "
    "실제 검색어·키워드를 앞에 두고, 독자가 클릭하고 싶을 정보만 담은 제목을 쓴다. "
    "구분자(| , : 등)는 키워드에 맞을 때만 쓰고, 억지로 붙이지 않는다. "
    "반드시 유효한 JSON 배열만 출력한다."
)

SEO_TITLE_STYLE_RULES = """
[제목 작성 원칙 — example.jsonl question 톤]
- 핵심 키워드를 앞쪽에 배치 (검색어 그대로 또는 자연스럽게)
- 명사형·설명형 제목 ("~방법", "~정보", "~총정리", "~이유와 후일담") — "~알아보세요", "~해보세요" 같은 구어체 금지
- 키워드 설명(desc)이 있으면 반드시 참고해 주제를 정확히 반영 (키워드 의미를 바꾸지 말 것)
- 검색 의도(방법·정보·이유·비교·총정리·논란·후기 등)가 제목만 봐도 드러나게
- 구분자는 키워드에 맞게 선택 (강제하지 않음):
  · 구분자 없음: "천정명 진짜사나이 출연 불발 이유와 후일담"
  · 쉼표(,): "애정의 상처, 친구에게 매몰찼던 순간의 후회와 화해 방법"
  · 콜론(:): "밍키넷 대체 사이트: 다양한 영상 콘텐츠 즐기는 방법"
  · 파이프(|): "스타크래프트 테란 핵 제조 방법 | 초보자를 위한 가이드" — 방법·가이드류에 적합할 때만
  · 물음표(?): 원래 질문·확인형 검색일 때만 ("~은?", "~인가요?")
- '총정리'는 방법·목록·논란·문제해결·정보 모음에 자연스럽게 사용
- ' 및 ', '와 ', ' 방법', ' 정보', ' 이유', ' 비교' 등으로 부가 가치 표현
- 25~55자, 한국어

[금지]
- '완벽 가이드', '놓치면 후회', '꼭 알아야 할', '충격' 같은 clickbait
- 키워드와 무관한 수식어, 뒷부분이 '핵심 정리'만 덜렁 있는 경우
- 모든 제목에 | 또는 총정리를 기계적으로 붙이기
- 키워드와 다른 주제로 제목 작성
"""


def _build_seo_batch_prompt(batch: list[dict[str, str]]) -> str:
    items_text = "\n".join(
        f"- qid: {it['qid']} / 키워드: {it['keyword_ko']}"
        + (f" / 설명: {it.get('desc_ko', '')}" if it.get("desc_ko") else "")
        for it in batch
    )
    examples = "\n".join(f"- {ex}" for ex in SEO_TITLE_EXAMPLES)

    return f"""아래 키워드 {len(batch)}개 각각에 대해 네이버 블로그 SEO 제목(seo_question)을 만들어줘.
각 키워드마다 가장 자연스럽고 검색에 유리한 형식을 스스로 선택해.

{SEO_TITLE_STYLE_RULES}

[참고 예시 — 이 톤과 밀도로 작성]
{examples}

[입력 키워드]
{items_text}

[출력 규칙]
1. seo_question: 25~55자
2. search_intent: info|howto|compare|checklist|review 중 하나
3. JSON 배열만 출력 (다른 텍스트 없이)

[
  {{"qid": "...", "seo_question": "...", "search_intent": "howto"}}
]
"""


def _detect_seo_format(title: str) -> str:
    if "?" in title:
        return "question"
    if "|" in title:
        return "pipe"
    if ":" in title:
        return "colon"
    if "," in title:
        return "comma"
    return "natural"


def _fallback_title(keyword: str, desc: str = "") -> str:
    suffix = desc.strip()[:20] if desc else ""
    pool = [
        f"{keyword} 총정리",
        f"{keyword} 방법 및 주의사항",
        f"{keyword} 이유와 관련 정보",
        f"{keyword}, {suffix}" if suffix else f"{keyword}, 알아두면 좋은 핵심 정보",
        f"{keyword} | 실전에서 바로 쓰는 팁",
        f"{keyword}: 궁금한 점 정리",
    ]
    return pool[hash(keyword) % len(pool)]


def generate_seo_questions_batch(
    items: list[dict[str, str]],
    *,
    model: str = MODEL_FAST,
    batch_size: int = 8,
    sleep_between: float = 0.5,
) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []

    for i in range(0, len(items), batch_size):
        batch = items[i : i + batch_size]
        prompt = _build_seo_batch_prompt(batch)

        try:
            raw = chat(
                prompt,
                system=SEO_TITLE_SYSTEM,
                model=model,
                temperature=0.55,
            )
            parsed = parse_json_array(raw, raise_on_fail=False)
        except Exception as exc:
            logger.warning("SEO batch failed (i=%d): %s", i, exc)
            parsed = []

        parsed_by_qid = {str(p.get("qid", "")).strip(): p for p in parsed}

        for it in batch:
            qid = it["qid"]
            gen = parsed_by_qid.get(qid, {})

            question = str(gen.get("seo_question", "")).strip()
            if not question or len(question) < 8 or len(question) > 80:
                question = _fallback_title(it["keyword_ko"], it.get("desc_ko", ""))

            out.append({
                "question_id": f"{qid}-seo",
                "qid": qid,
                "keyword_ko": it["keyword_ko"],
                "keyword_en": it.get("keyword_en", ""),
                "popularity": str(it.get("popularity", "")),
                "seo_question": question,
                "search_intent": str(gen.get("search_intent", "info")).strip() or "info",
                "seo_format": _detect_seo_format(question),
                "model": model,
            })

        time.sleep(sleep_between)

    return out


def generate_l2_keywords(
    *,
    l1_name: str,
    l2_name: str,
    mega_group: str,
    existing_keywords: list[str],
    count: int = 10,
    model: str = MODEL_FAST,
) -> list[dict[str, str]]:
    avoid = "\n".join(f"- {kw}" for kw in existing_keywords[:30]) or "- (없음)"
    prompt = f"""다음 카테고리에 맞는 네이버 블로그 SEO 키워드 {count}개를 만들어줘.

[L1] {l1_name} ({mega_group})
[L2] {l2_name}

[중복 금지]
{avoid}

규칙:
1. focus_keyword: 실제 검색할 2~10단어 (상황·대상·방법·비교 등 의도가 다양해야 함)
2. seo_title: 클릭 유도 SEO 제목 — 포맷은 pipe/comma/dash/natural 중 자유롭게 섞기
3. search_intent: info|howto|compare|cost|checklist|review
4. JSON 배열만 출력

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
    items = parse_json_array(raw, raise_on_fail=False)
    cleaned: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in items:
        focus = str(item.get("focus_keyword", "")).strip()
        if not focus or focus in seen:
            continue
        seen.add(focus)
        cleaned.append({
            "focus_keyword": focus,
            "seo_title": str(item.get("seo_title", "")).strip() or focus,
            "search_intent": str(item.get("search_intent", "info")).strip() or "info",
        })
    return cleaned[:count]


# ============================================================
# 본문 생성
# ============================================================


def strip_model_artifacts(text: str) -> str:
    for tag in ("think", "redacted_thinking", "reasoning"):
        open_tag, close_tag = f"<{tag}>", f"</{tag}>"
        while open_tag in text:
            start = text.find(open_tag)
            end = text.find(close_tag, start)
            if end == -1:
                text = text[:start]
                break
            text = text[:start] + text[end + len(close_tag):]

    lines = text.strip().splitlines()
    cleaned: list[str] = []
    seen_h1 = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("# ") and not stripped.startswith("##"):
            if seen_h1:
                continue
            seen_h1 = True
        cleaned.append(line)
    return "\n".join(cleaned).strip()


META_SECTION_HEADING_RE = re.compile(
    r"^#{2,3}\s+(?:\*{0,2})?"
    r"(?:도입(?:\s*[:：].*)?|핵심\s+한\s+줄|본문\s*\d+|체크리스트|FAQ|마무리|결론\s+요약)"
    r"(?:\*{0,2})?\s*$",
    re.MULTILINE | re.IGNORECASE,
)


def normalize_body_structure(body: str) -> str:
    lines = body.splitlines()
    cleaned: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped in {"---", "***", "___"}:
            continue
        if META_SECTION_HEADING_RE.match(stripped):
            continue
        if re.match(r"^#{2,6}\s+", stripped):
            title = re.sub(r"^#{2,6}\s+", "", stripped).strip().strip("*").strip()
            if title:
                cleaned.append(f"**{title}**")
            continue
        cleaned.append(line)

    text = "\n".join(cleaned)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def generate_blog_body(
    question_text: str,
    focus_keyword: str,
    *,
    intro_paragraphs: int = 3,
    body_sections: int = 4,
    sentences_per_section: int = 4,
    model: str = MODEL_DRAFT,
    min_chars: int | None = None,
    max_chars: int | None = None,
) -> str:
    _ = (min_chars, max_chars)
    prompt = f"""다음 Q&A 주제에 대한 답변 본문을 작성해줘.

- 제목(별도, 본문에 H1 쓰지 말 것): {question_text}
- 핵심 키워드: {focus_keyword}

[분량 — 구조 단위]
- 도입 일반 문단 {intro_paragraphs}개
- 본문 소제목 {body_sections}개 (각 소제목당 {sentences_per_section}~{sentences_per_section + 2}문장)
- 결론 1문단

{AIQA_BODY_STYLE_RULES}

[추가]
- 독자가 검색해서 들어온 것처럼, 궁금증을 풀어주는 정보글로 작성
- 사실 관계는 단정적으로, 모르면 일반적 설명·주의사항으로 처리
- 핵심 키워드는 도입과 본문에 자연스럽게 2~3회 포함
- 마크다운만 출력 (코드블록·추론 과정 없이)
"""
    raw = chat(
        prompt,
        system=AIQA_BODY_SYSTEM,
        model=model,
        temperature=0.65,
        max_tokens=8192,
    )
    return normalize_body_structure(strip_model_artifacts(raw))


def _split_intro(body: str) -> tuple[str, str]:
    lines = body.strip().splitlines()
    if not lines:
        return "", ""

    rest_idx = len(lines)
    seen_content = False
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("##") or re.fullmatch(r"\*\*.+\*\*", stripped):
            rest_idx = i
            break
        if stripped and not stripped.startswith("#"):
            seen_content = True
        elif seen_content and not stripped and i + 1 < len(lines):
            nxt = lines[i + 1].strip()
            if nxt.startswith("##") or re.fullmatch(r"\*\*.+\*\*", nxt):
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
    intro, rest = _split_intro(body)
    if len(intro) < 80:
        return body

    prompt = f"""아래 Q&A 답변의 도입부(첫 2~3문단)만 다듬어줘.

- 제목: {seo_title}
- 핵심 키워드: {focus_keyword}

[규칙]
- H1(#) 금지, 일반 문단 2~3개만 출력
- "~하시는 분들이 많습니다", "결론부터 말씀드리자면", "이 글에서는" 같은 자연스러운 도입 OK
- 과장·clickbait 금지
- 키워드 1~2회 자연스럽게 포함
- 분량은 기존과 비슷하거나 약간 짧게
- 추론 과정 없이 다듬은 결과만 출력

[현재 도입부]
{intro}
"""
    polished = chat(
        prompt,
        system=AIQA_BODY_SYSTEM,
        model=model,
        temperature=0.4,
    ).strip()

    polished = _strip_code_fence(polished)
    polished = normalize_body_structure(strip_model_artifacts(polished))
    if not polished:
        return body
    merged = f"{polished}\n\n{rest}" if rest else polished
    return normalize_body_structure(merged)


# ============================================================
# 메타 태그
# ============================================================


def generate_meta_tags(
    seo_title: str,
    focus_keyword: str,
    *,
    model: str = MODEL_FAST,
) -> dict[str, str]:
    result = generate_meta_tags_batch(
        [{"seo_title": seo_title, "focus_keyword": focus_keyword}],
        model=model,
    )
    return result[0] if result else {"meta_description": "", "tags": ""}


def generate_meta_tags_batch(
    items: list[dict[str, str]],
    *,
    model: str = MODEL_FAST,
    batch_size: int = 5,
    sleep_between: float = 0.5,
) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []

    for i in range(0, len(items), batch_size):
        batch = items[i : i + batch_size]
        items_text = "\n".join(
            f"{idx + 1}. 제목: {it['seo_title']} / 키워드: {it['focus_keyword']}"
            for idx, it in enumerate(batch)
        )

        prompt = f"""아래 {len(batch)}개 글의 SEO 메타 정보를 JSON 배열로 만들어줘.

[입력]
{items_text}

[규칙]
- meta_description: 120~160자, 자연스럽고 검색 친화적, 과장 금지
- tags: 5개, 검색 관련 키워드
- JSON 배열만 출력 (다른 텍스트 없이)

[
  {{"meta_description": "...", "tags": ["태그1", "태그2", "태그3", "태그4", "태그5"]}}
]
"""
        try:
            raw = chat(
                prompt,
                system="너는 한국어 Q&A 메타 태그 작성 도우미야. JSON만 출력.",
                model=model,
                temperature=0.5,
            )
            parsed = parse_json_array(raw, raise_on_fail=False)
        except Exception as exc:
            logger.warning("Meta batch failed (i=%d): %s", i, exc)
            parsed = []

        for idx, it in enumerate(batch):
            meta = parsed[idx] if idx < len(parsed) else {}
            tags = meta.get("tags", [])
            if isinstance(tags, list):
                tags_str = ", ".join(str(t) for t in tags[:5])
            else:
                tags_str = str(tags)

            out.append({
                "meta_description": str(meta.get("meta_description", "")).strip(),
                "tags": tags_str or f"{it['focus_keyword']}",
            })

        time.sleep(sleep_between)

    return out


def generate_blog_titles(keyword: str, count: int = 10) -> str:
    return chat(
        f"{keyword} 관련 블로그 제목 {count}개 추천해줘. 번호 목록으로만 답해.",
        system="너는 한국어 블로그 제목과 키워드를 잘 뽑는 도우미야.",
        model=MODEL_FAST,
        temperature=0.7,
    )
