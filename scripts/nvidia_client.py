"""
NVIDIA NIM API 클라이언트 (OpenAI-compatible).

모델: meta/llama-3.1-8b-instruct
환경변수: NVIDIA_API_KEY
"""

from __future__ import annotations

import logging
import os
import random
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from openai import OpenAI

from gemini_client import parse_json_array  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")
load_dotenv(ROOT / ".env.local")

logger = logging.getLogger(__name__)

BASE_URL = "https://integrate.api.nvidia.com/v1"
MODEL_FAST = "meta/llama-3.1-8b-instruct"
MODEL_NEMOTRON = "mistralai/mistral-nemotron"

TAXONOMY_SYSTEM = (
    "너는 한국어 네이버 블로그 SEO 카테고리 기획자야. "
    "검색 수요가 있는 구체적이고 실용적인 카테고리를 설계한다. "
    "반드시 유효한 JSON 배열만 출력한다. 설명·주석·마크다운 코드블록 금지."
)


def create_client() -> OpenAI:
    api_key = os.environ.get("NVIDIA_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("NVIDIA_API_KEY 가 .env 에 없습니다.")
    return OpenAI(base_url=BASE_URL, api_key=api_key)


@dataclass
class ChatResult:
    text: str
    model: str
    attempts: int


class RateLimitExhausted(RuntimeError):
    """NVIDIA API rate limit — 재시도 소진."""


def _is_rate_or_quota_error(exc: Exception) -> bool:
    msg = str(exc).lower()
    return any(k in msg for k in ("429", "rate limit", "too many requests", "quota"))


def _is_transient_error(exc: Exception) -> bool:
    msg = str(exc).lower()
    return any(k in msg for k in ("503", "502", "504", "timeout", "unavailable"))


def chat_full(
    prompt: str,
    *,
    system: str,
    model: str = MODEL_FAST,
    temperature: float = 0.6,
    retries: int = 4,
    max_tokens: int | None = 4096,
) -> ChatResult:
    client = create_client()
    last_err: Exception | None = None

    for attempt in range(retries):
        try:
            kwargs: dict[str, Any] = {
                "model": model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": prompt},
                ],
                "temperature": temperature,
            }
            if max_tokens is not None:
                kwargs["max_tokens"] = max_tokens

            response = client.chat.completions.create(**kwargs)
            text = (response.choices[0].message.content or "").strip()
            return ChatResult(text=text, model=model, attempts=attempt + 1)

        except Exception as exc:
            last_err = exc
            if _is_rate_or_quota_error(exc) or _is_transient_error(exc):
                wait = (2 ** attempt) * 2 + random.uniform(0, 1.5)
                logger.warning("Retry in %.1fs (attempt %d): %s", wait, attempt + 1, exc)
                time.sleep(wait)
                continue
            raise

    assert last_err is not None
    if _is_rate_or_quota_error(last_err):
        raise RateLimitExhausted(str(last_err)) from last_err
    raise last_err


def chat(
    prompt: str,
    *,
    system: str = TAXONOMY_SYSTEM,
    model: str = MODEL_FAST,
    temperature: float = 0.6,
    retries: int = 4,
    max_tokens: int | None = 4096,
) -> str:
    return chat_full(
        prompt,
        system=system,
        model=model,
        temperature=temperature,
        retries=retries,
        max_tokens=max_tokens,
    ).text


def slugify_en(text: str) -> str:
    text = text.strip().lower()
    text = re.sub(r"[^a-z0-9]+", "-", text)
    return text.strip("-") or "topic"


def _compact_brief(brief: str, *, max_lines: int = 12) -> str:
    lines = [ln.strip() for ln in brief.splitlines() if ln.strip() and not ln.strip().startswith("#")]
    return "\n".join(lines[:max_lines])


VALID_INTENTS = frozenset({"info", "howto", "compare", "cost", "checklist", "review", "news"})
VALID_ANGLES = frozenset({"start", "howto", "compare", "tip", "review", "issue", "local"})

CLICKBAIT_RE = re.compile(
    r"완벽\s*가이드|놓치면\s*후회|꼭\s*알아야|총정리\s*필독|100%\s*|충격|대박|필수\s*정리",
    re.IGNORECASE,
)


def _valid_focus_keyword(text: str) -> bool:
    if not text or not re.search(r"[가-힣]", text):
        return False
    if re.search(r"[\u4e00-\u9fff\u0100-\u024f\u1e00-\u1eff]", text):
        return False
    if CLICKBAIT_RE.search(text):
        return False
    words = text.split()
    return 1 <= len(words) <= 12


def _norm_keyword(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lower())


def _is_similar_keyword(a: str, b: str) -> bool:
    na, nb = _norm_keyword(a), _norm_keyword(b)
    if na == nb:
        return True
    if len(na) >= 8 and (na in nb or nb in na):
        return True
    return False


def _keyword_seen(keyword: str, existing: list[str]) -> bool:
    return any(_is_similar_keyword(keyword, k) for k in existing)


def _valid_name_ko(name: str) -> bool:
    if not re.search(r"[가-힣]", name):
        return False
    if re.search(r"[\u4e00-\u9fff\u0100-\u024f\u1e00-\u1eff]", name):
        return False
    return True


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
    brief_short = _compact_brief(brief)
    prompt = f"""L1 "{l1_name}" 아래 L2 중분류 {count}개를 설계해.

[L1 설명] {l1_description}
[L1 테마] {l1_theme}

[기획 힌트]
{brief_short}

[이미 사용된 L2 이름 — 절대 중복 금지]
{avoid}

[규칙]
- name_ko: 한국어 2~10자, 블로그 메뉴 이름처럼 구체적으로
- slug: 영문 kebab-case (예: k-pop, drama-review)
- description: 한국어 1문장
- L1 범위 안에서만, 서로 다른 하위 주제
- "종합", "기타", "전체", "정보" 같은 포괄명 금지
- 중국어·일본어 금지, 한국어만
- 출력: JSON 배열만, 설명·주석·마크다운 금지

[
  {{"name_ko": "K-POP", "slug": "k-pop", "description": "아이돌·음원·컴백·차트 관련 글"}},
  {{"name_ko": "드라마", "slug": "drama", "description": "드라마 줄거리·시청·OTT 관련 글"}}
]
"""
    raw = chat(prompt, system=TAXONOMY_SYSTEM, model=model, temperature=0.85)
    items = parse_json_array(raw, raise_on_fail=False)
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    used_slugs: set[str] = set()
    for item in items:
        name = str(item.get("name_ko", "")).strip()
        if not name or name in seen or not _valid_name_ko(name):
            continue
        if avoid_names and name in avoid_names:
            continue
        seen.add(name)
        slug = slugify_en(str(item.get("slug", "")).strip()) or slugify_en(name)
        base_slug = slug
        n = 2
        while slug in used_slugs:
            slug = f"{base_slug}-{n}"
            n += 1
        used_slugs.add(slug)
        out.append({
            "name_ko": name,
            "slug": slug,
            "description": str(item.get("description", "")).strip(),
        })
    return out[:count]


def generate_l3_topics(
    *,
    brief: str,
    l1_name: str,
    l1_description: str,
    l2_name: str,
    l2_description: str,
    count: int,
    avoid_keywords: list[str] | None = None,
    model: str = MODEL_NEMOTRON,
) -> list[dict[str, str]]:
    avoid = "\n".join(f"- {k}" for k in (avoid_keywords or [])[:60]) or "- (없음)"
    brief_short = _compact_brief(brief)
    prompt = f"""L2 "{l2_name}" 아래 L3 소분류(검색 롱테일 주제) {count}개를 설계해.

[L1] {l1_name} — {l1_description}
[L2] {l2_name} — {l2_description}

[기획 힌트]
{brief_short}

[이미 사용된 focus_keyword — 절대 중복 금지]
{avoid}

[규칙]
- focus_keyword: 네이버·구글 검색 2~12단어 한국어, 의도가 서로 달라야 함
- title_ko: SEO 제목 (25~55자, 명사형·설명형, clickbait 금지)
- slug: 영문 kebab-case 2~4단어
- search_intent: info|howto|compare|cost|checklist|review|news 중 하나
- topic_angle: start|howto|compare|tip|review|issue|local 중 하나
- description: 한국어 1문장
- L1·L2 범위 안에서만, 서로 다른 검색 의도
- 중첩 JSON·카테고리 트리 구조 금지 — flat JSON 배열만
- focus_keyword·title_ko에 영문 단어 금지 (Netflix·OTT 등 고유명사만 허용)
- JSON 배열만 출력

[
  {{
    "focus_keyword": "2026 Netflix 한국 드라마 신작",
    "title_ko": "2026 Netflix 한국 드라마 신작 일정과 기대작",
    "slug": "netflix-kdrama-2026",
    "search_intent": "info",
    "topic_angle": "issue",
    "description": "2026년 Netflix 한국 드라마 신작 라인업"
  }}
]
"""
    raw = chat(prompt, system=TAXONOMY_SYSTEM, model=model, temperature=0.75)
    items = parse_json_array(raw, raise_on_fail=False)
    out: list[dict[str, str]] = []
    used_slugs: set[str] = set()
    for item in items:
        focus = str(item.get("focus_keyword", "")).strip()
        if not focus:
            continue
        if any(_is_similar_keyword(focus, x["focus_keyword"]) for x in out):
            continue
        if avoid_keywords and _keyword_seen(focus, avoid_keywords):
            continue
        if not _valid_focus_keyword(focus):
            continue
        intent = str(item.get("search_intent", "info")).strip().lower()
        angle = str(item.get("topic_angle", "tip")).strip().lower()
        if intent not in VALID_INTENTS:
            intent = "info"
        if angle not in VALID_ANGLES:
            angle = "tip"
        title = str(item.get("title_ko", "") or item.get("seo_title", "")).strip() or focus
        slug = slugify_en(str(item.get("slug", "")).strip()) or slugify_en(focus)
        base_slug = slug
        n = 2
        while slug in used_slugs:
            slug = f"{base_slug}-{n}"
            n += 1
        used_slugs.add(slug)
        out.append({
            "focus_keyword": focus,
            "title_ko": title,
            "slug": slug,
            "search_intent": intent,
            "topic_angle": angle,
            "description": str(item.get("description", "")).strip(),
        })
    return out[:count]
