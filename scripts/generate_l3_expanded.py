#!/usr/bin/env python3
"""L3 대량 생성 — 50K 키워드 → L3 콘텐츠 메타데이터 변환.

입력: outputs/l2_candidates_gte100_depth1.csv
출력:
  outputs/l3_expanded_hitier.csv    고티어(월 1,000+)
  outputs/l3_expanded_longtail.csv  롱테일(월 100-999)
  outputs/l3_expanded_all.csv       통합 (실행 후 별도 merge 명령)

실행 예시:
  python scripts/generate_l3_expanded.py              # 전체 실행
  python scripts/generate_l3_expanded.py --tier hi    # 고티어만
  python scripts/generate_l3_expanded.py --tier lt    # 롱테일만
  python scripts/generate_l3_expanded.py --resume     # 체크포인트 이어서

모델 배정 (실험 결과 후 --model 옵션으로 변경 가능):
  고티어:  Gemini Flash Lite (기본) → Groq Scout (폴백)
  롱테일:  Groq Scout (기본) → Groq 8B (--longtail-model 8b 옵션)

YMYL 안전 프롬프트: l1_code 02(금융), 03(건강) 자동 적용
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import re
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))

from gemini_client import (  # noqa: E402
    MODEL_FAST as GEMINI_FLASH_LITE,
    chat_full as gemini_chat_full,
    parse_json_array,
)
from groq_client import MODEL_FAST as SCOUT_MODEL, chat_full as groq_chat_full  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────
# 상수
# ─────────────────────────────────────────────
MODEL_8B = "llama-3.1-8b-instant"
HITIER_THRESHOLD = 1000  # 월 검색량 기준

VALID_INTENTS = frozenset({"info", "howto", "compare", "cost", "checklist", "review", "news"})
VALID_ANGLES = frozenset({"start", "howto", "compare", "tip", "review", "issue", "local"})
CLICKBAIT_RE = re.compile(
    r"완벽\s*가이드|놓치면\s*후회|꼭\s*알아야|총정리\s*필독|100%\s*|충격|대박|필수\s*정리",
    re.IGNORECASE,
)

L3_FIELDNAMES = [
    "l3_id", "l2_id", "l1_code", "l3_slug",
    "focus_keyword", "title_ko",
    "search_intent", "topic_angle",
    "description", "model", "generated_at",
    "monthly_total", "tier",
]

CANDIDATES_CSV = ROOT / "outputs" / "l2_candidates_gte100_depth1.csv"
TOPICS_L2_CSV = ROOT / "data" / "topics_l2.csv"
OUT_DIR = ROOT / "outputs"
CHECKPOINT_FILE = OUT_DIR / "l3_checkpoint.json"
RETRY_QUEUE_FILE = OUT_DIR / "l3_retry_queue.jsonl"
HITIER_CSV = OUT_DIR / "l3_expanded_hitier.csv"
LONGTAIL_CSV = OUT_DIR / "l3_expanded_longtail.csv"

YMYL_CODES = {"02", "03"}
YMYL_FINANCE_GUARD = (
    "⚠ 금융 콘텐츠 주의: 투자 권유·수익 보장 단정 금지. "
    "'전망·정보·비교·분석' 톤으로만."
)
YMYL_HEALTH_GUARD = (
    "⚠ 건강 콘텐츠 주의: 치료 효능·진단 단정 금지. "
    "'정보·관리·예방' 톤. 필요시 전문가 상담 권유."
)

SYSTEM_PROMPT = (
    "너는 한국어 네이버 블로그 SEO 콘텐츠 기획자야. "
    "주어진 검색 키워드를 블로그 콘텐츠 소재로 가공한다. "
    "반드시 유효한 JSON 배열만 출력한다. 설명·마크다운·코드블록 금지."
)


# ─────────────────────────────────────────────
# 유틸리티
# ─────────────────────────────────────────────


def slugify_ko(text: str) -> str:
    """한국어 키워드 → 영문 슬러그 (간단 romanize 없이 해시 기반)."""
    import hashlib
    h = hashlib.md5(text.encode("utf-8")).hexdigest()[:8]
    # 영어/숫자가 있으면 앞에 붙임
    en_part = re.sub(r"[^a-z0-9]", "", text.lower())[:12]
    return f"{en_part}-{h}" if en_part else f"kw-{h}"


def norm_keyword(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lower())


def load_l2_map() -> dict[str, dict[str, str]]:
    if not TOPICS_L2_CSV.exists():
        return {}
    return {r["l2_id"]: r for r in csv.DictReader(TOPICS_L2_CSV.open(encoding="utf-8-sig"))}


def assign_l2_id(row: dict[str, str], l2_map: dict) -> str:
    """source_seed에서 유효한 l2_id 추출. 없으면 l1_code 기반 폴백."""
    seeds = [s.strip() for s in row.get("source_seed", "").split("|") if s.strip()]
    for seed in seeds:
        if seed in l2_map:
            return seed
    # 폴백: l1_code가 같은 L2 중 첫 번째
    l1 = row["l1_code"]
    for l2_id, l2_row in l2_map.items():
        if l2_row.get("l1_code") == l1:
            return l2_id
    return f"{l1}-unknown"


def build_l3_slug(l2_id: str, keyword: str, existing_slugs: set[str]) -> str:
    base = slugify_ko(keyword)
    slug = base
    n = 2
    while f"{l2_id}-{slug}" in existing_slugs:
        slug = f"{base}-{n}"
        n += 1
    existing_slugs.add(f"{l2_id}-{slug}")
    return slug


# ─────────────────────────────────────────────
# 프롬프트 빌더
# ─────────────────────────────────────────────


def build_prompt(
    items: list[dict[str, Any]],
    *,
    l1_name: str,
    l1_code: str,
) -> str:
    kw_lines = "\n".join(
        f"{item['idx']}. {item['keyword']}  (월 {item['monthly_total']:,}회)"
        for item in items
    )
    ymyl_section = ""
    if l1_code == "02":
        ymyl_section = f"\n\n{YMYL_FINANCE_GUARD}\n"
    elif l1_code == "03":
        ymyl_section = f"\n\n{YMYL_HEALTH_GUARD}\n"

    return f"""다음은 '{l1_name}' 분야의 실제 검색 키워드 {len(items)}개다.
각 키워드를 한국어 블로그 콘텐츠 소재로 가공해라.{ymyl_section}

[규칙]
- focus_keyword: 원본 그대로 (절대 수정 금지)
- title_ko: 키워드를 자연스럽게 포함한 12~60자 제목. 클릭유도O, 과장·낚시X
- search_intent: info / howto / compare / cost / checklist / review / news 중 1개
- topic_angle: start / howto / compare / tip / review / issue / local 중 1개
- description: 이 글이 무엇을 다루는지 한 문장 요약

[키워드 목록]
{kw_lines}

JSON 배열만 출력:
[{{"idx": 1, "focus_keyword": "원본키워드그대로", "title_ko": "...", "search_intent": "info", "topic_angle": "issue", "description": "..."}}]"""


# ─────────────────────────────────────────────
# LLM 호출
# ─────────────────────────────────────────────


def call_model(model_key: str, prompt: str, *, max_tokens: int = 4096) -> tuple[list[dict], bool]:
    """(parsed_items, success) 반환. success = JSON 파싱 충분히 됐으면 True."""
    try:
        if model_key == "gemini":
            result = gemini_chat_full(
                prompt, system=SYSTEM_PROMPT, model=GEMINI_FLASH_LITE,
                temperature=0.55, max_tokens=max_tokens,
            )
            raw = result.text
        elif model_key == "scout":
            result = groq_chat_full(
                prompt, system=SYSTEM_PROMPT, model=SCOUT_MODEL,
                temperature=0.55, max_tokens=max_tokens,
            )
            raw = result.text
        elif model_key == "8b":
            result = groq_chat_full(
                prompt, system=SYSTEM_PROMPT, model=MODEL_8B,
                temperature=0.55, max_tokens=max_tokens,
            )
            raw = result.text
        else:
            raise ValueError(f"Unknown model key: {model_key}")

        parsed = parse_json_array(raw, raise_on_fail=False)
        return parsed, True
    except Exception as exc:
        logger.warning("LLM 호출 실패 (model=%s): %s", model_key, exc)
        return [], False


# ─────────────────────────────────────────────
# 검증
# ─────────────────────────────────────────────


def validate_item(item: dict, *, orig_keyword: str) -> tuple[bool, list[str]]:
    """(ok, warnings) 반환."""
    warns: list[str] = []
    title = item.get("title_ko", "")
    intent = item.get("search_intent", "")
    angle = item.get("topic_angle", "")

    if not title:
        return False, ["제목 없음"]
    tl = len(title)
    if tl < 12:
        warns.append(f"제목 너무 짧음({tl}자)")
    elif tl > 60:
        warns.append(f"제목 너무 긺({tl}자)")
    if CLICKBAIT_RE.search(title):
        warns.append("클릭베이트 감지")
    if intent not in VALID_INTENTS:
        item["search_intent"] = "info"
        warns.append(f"intent 교정: {intent!r}→info")
    if angle not in VALID_ANGLES:
        item["topic_angle"] = "tip"
        warns.append(f"angle 교정: {angle!r}→tip")

    fk = item.get("focus_keyword", "")
    if norm_keyword(fk) != norm_keyword(orig_keyword):
        item["focus_keyword"] = orig_keyword  # 원본으로 복원
        warns.append(f"focus_keyword 원본 복원: {fk!r}→{orig_keyword!r}")

    return True, warns


# ─────────────────────────────────────────────
# 체크포인트
# ─────────────────────────────────────────────


class Checkpoint:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._done: set[str] = set()
        self._load()

    def _load(self) -> None:
        if self.path.exists():
            data = json.loads(self.path.read_text(encoding="utf-8"))
            self._done = set(data.get("done_norms", []))
            logger.info("체크포인트 로드: %d개 완료됨", len(self._done))

    def done(self, norm_kw: str) -> bool:
        return norm_kw in self._done

    def mark_done(self, norms: list[str]) -> None:
        self._done.update(norms)

    def save(self) -> None:
        self.path.write_text(
            json.dumps({"done_norms": sorted(self._done), "count": len(self._done)},
                       ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    @property
    def count(self) -> int:
        return len(self._done)


# ─────────────────────────────────────────────
# CSV 출력 헬퍼
# ─────────────────────────────────────────────


class CsvAppender:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._exists = path.exists()

    def append(self, rows: list[dict]) -> None:
        mode = "a" if self._exists else "w"
        with open(self.path, mode, encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, fieldnames=L3_FIELDNAMES, extrasaction="ignore")
            if not self._exists:
                w.writeheader()
                self._exists = True
            w.writerows(rows)


# ─────────────────────────────────────────────
# 배치 처리
# ─────────────────────────────────────────────


def process_batch(
    batch: list[dict[str, str]],
    *,
    model_key: str,
    l1_code: str,
    l1_name: str,
    l2_map: dict,
    existing_slugs: set[str],
    now_str: str,
    tier: str,
) -> tuple[list[dict], list[dict[str, str]]]:
    """배치 처리. (성공 rows, 실패 원본 rows) 반환."""
    items = [
        {"idx": j + 1, "keyword": r["candidate_keyword"], "monthly_total": int(r["monthly_total"])}
        for j, r in enumerate(batch)
    ]
    prompt = build_prompt(items, l1_name=l1_name, l1_code=l1_code)
    parsed, _ = call_model(model_key, prompt)

    # idx → 원본 매핑
    idx_map = {str(item["idx"]): orig for item, orig in zip(items, batch)}

    success_rows: list[dict] = []
    failed: list[dict[str, str]] = []

    for p in parsed:
        idx_key = str(p.get("idx", ""))
        orig = idx_map.get(idx_key)
        if not orig:
            continue

        ok, warns = validate_item(p, orig_keyword=orig["candidate_keyword"])
        if warns:
            logger.debug("검증 경고 [%s]: %s", orig["candidate_keyword"], warns)
        if not ok:
            failed.append(orig)
            continue

        l2_id = assign_l2_id(orig, l2_map)
        slug = build_l3_slug(l2_id, orig["candidate_keyword"], existing_slugs)
        l3_id = f"{l2_id}-{slug}"

        success_rows.append({
            "l3_id": l3_id,
            "l2_id": l2_id,
            "l1_code": l1_code,
            "l3_slug": slug,
            "focus_keyword": p["focus_keyword"],
            "title_ko": p["title_ko"],
            "search_intent": p["search_intent"],
            "topic_angle": p["topic_angle"],
            "description": p.get("description", ""),
            "model": f"{model_key}_{MODEL_8B if model_key=='8b' else SCOUT_MODEL if model_key=='scout' else GEMINI_FLASH_LITE}",
            "generated_at": now_str,
            "monthly_total": orig["monthly_total"],
            "tier": tier,
        })

    # 파싱되지 않은 나머지는 failed
    parsed_orig_keywords = {
        norm_keyword(p.get("focus_keyword", "")) for p in parsed if p.get("focus_keyword")
    }
    for orig in batch:
        if norm_keyword(orig["candidate_keyword"]) not in parsed_orig_keywords:
            already = any(r["focus_keyword"] == orig["candidate_keyword"] for r in success_rows)
            if not already:
                failed.append(orig)

    return success_rows, failed


# ─────────────────────────────────────────────
# 메인 실행
# ─────────────────────────────────────────────


def load_all_rows(tier_filter: str | None) -> list[dict[str, str]]:
    rows = list(csv.DictReader(CANDIDATES_CSV.open(encoding="utf-8-sig")))
    if tier_filter == "hi":
        rows = [r for r in rows if int(r["monthly_total"]) >= HITIER_THRESHOLD]
    elif tier_filter == "lt":
        rows = [r for r in rows if int(r["monthly_total"]) < HITIER_THRESHOLD]
    # 검색량 내림차순 (고가치 먼저)
    rows.sort(key=lambda r: int(r["monthly_total"]), reverse=True)
    return rows


def select_model(tier: str, args: argparse.Namespace) -> str:
    if tier == "hi":
        return getattr(args, "hitier_model", "gemini") or "gemini"
    else:
        return getattr(args, "longtail_model", "scout") or "scout"


def run(args: argparse.Namespace) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    now_str = datetime.now(timezone.utc).isoformat()

    l2_map = load_l2_map()
    logger.info("L2 맵 로드: %d개", len(l2_map))

    checkpoint = Checkpoint(CHECKPOINT_FILE)
    existing_slugs: set[str] = set()

    # 기존 출력 파일에서 slug 복원
    for csv_path in (HITIER_CSV, LONGTAIL_CSV):
        if csv_path.exists():
            for r in csv.DictReader(csv_path.open(encoding="utf-8-sig")):
                existing_slugs.add(r.get("l3_id", ""))

    all_rows = load_all_rows(args.tier)
    pending = [r for r in all_rows if not checkpoint.done(norm_keyword(r["candidate_keyword"]))]
    logger.info(
        "전체 %d개 / 완료 %d개 / 처리 대상 %d개",
        len(all_rows), checkpoint.count, len(pending),
    )

    # L1별로 그룹핑 (같은 카테고리끼리 묶어 프롬프트 품질↑)
    by_l1: dict[str, list[dict[str, str]]] = defaultdict(list)
    for r in pending:
        by_l1[r["l1_code"]].append(r)

    # L1명 맵
    l1_name_map: dict[str, str] = {
        "01": "엔터·미디어", "02": "금융·재테크", "03": "건강",
        "04": "패션", "05": "스포츠", "06": "생활정보",
        "07": "IT·테크", "08": "교육", "09": "맛집·카페",
        "10": "취미", "11": "여행", "12": "반려동물",
        "13": "부동산", "14": "육아", "15": "자동차",
        "16": "환경", "17": "법률", "18": "기타",
    }

    hitier_writer = CsvAppender(HITIER_CSV)
    longtail_writer = CsvAppender(LONGTAIL_CSV)
    retry_queue: list[dict[str, str]] = []

    total_success = 0
    total_fail = 0
    checkpoint_interval = 50  # 50배치마다 저장

    batch_count = 0
    for l1_code, l1_rows in sorted(by_l1.items()):
        l1_name = l1_name_map.get(l1_code, f"L1-{l1_code}")
        logger.info("L1 %s (%s): %d개", l1_code, l1_name, len(l1_rows))

        # L1 내에서도 고티어/롱테일 분리
        hi_rows = [r for r in l1_rows if int(r["monthly_total"]) >= HITIER_THRESHOLD]
        lt_rows = [r for r in l1_rows if int(r["monthly_total"]) < HITIER_THRESHOLD]

        tier_groups = []
        if args.tier in (None, "hi") and hi_rows:
            tier_groups.append(("hi", hi_rows, select_model("hi", args)))
        if args.tier in (None, "lt") and lt_rows:
            tier_groups.append(("lt", lt_rows, select_model("lt", args)))

        for tier, tier_rows, model_key in tier_groups:
            bs = args.hitier_batch if tier == "hi" else args.longtail_batch
            logger.info(
                "  [%s] tier=%s model=%s batch=%d 건수=%d",
                l1_code, tier, model_key, bs, len(tier_rows),
            )

            for i in range(0, len(tier_rows), bs):
                batch = tier_rows[i : i + bs]
                success_rows, failed = process_batch(
                    batch,
                    model_key=model_key,
                    l1_code=l1_code,
                    l1_name=l1_name,
                    l2_map=l2_map,
                    existing_slugs=existing_slugs,
                    now_str=now_str,
                    tier=tier,
                )

                if success_rows:
                    writer = hitier_writer if tier == "hi" else longtail_writer
                    writer.append(success_rows)
                    checkpoint.mark_done([norm_keyword(r["focus_keyword"]) for r in success_rows])
                    total_success += len(success_rows)

                if failed:
                    retry_queue.extend(failed)
                    total_fail += len(failed)

                batch_count += 1
                if batch_count % checkpoint_interval == 0:
                    checkpoint.save()
                    logger.info(
                        "  체크포인트 저장: 성공 %d, 실패 %d (재시도 큐: %d)",
                        total_success, total_fail, len(retry_queue),
                    )

                # 진행 로그
                done_in_l1 = i + len(batch)
                pct = done_in_l1 / len(tier_rows) * 100
                logger.info(
                    "  L1=%s tier=%s %d/%d (%.0f%%) 성공+%d 실패+%d",
                    l1_code, tier, done_in_l1, len(tier_rows), pct,
                    len(success_rows), len(failed),
                )

                time.sleep(args.sleep)

    # 재시도 (배치 크기 절반으로)
    if retry_queue:
        logger.info("\n재시도 큐: %d개 (배치 크기 절반)", len(retry_queue))
        RETRY_QUEUE_FILE.write_text(
            "\n".join(json.dumps(r, ensure_ascii=False) for r in retry_queue),
            encoding="utf-8",
        )

        for l1_code, l1_name in l1_name_map.items():
            l1_retry = [r for r in retry_queue if r["l1_code"] == l1_code]
            if not l1_retry:
                continue
            hi_retry = [r for r in l1_retry if int(r["monthly_total"]) >= HITIER_THRESHOLD]
            lt_retry = [r for r in l1_retry if int(r["monthly_total"]) < HITIER_THRESHOLD]

            for tier, tier_rows in (("hi", hi_retry), ("lt", lt_retry)):
                if not tier_rows:
                    continue
                model_key = select_model(tier, args)
                bs = max(1, (args.hitier_batch if tier == "hi" else args.longtail_batch) // 2)
                for i in range(0, len(tier_rows), bs):
                    batch = tier_rows[i : i + bs]
                    success_rows, still_failed = process_batch(
                        batch,
                        model_key=model_key,
                        l1_code=l1_code,
                        l1_name=l1_name,
                        l2_map=l2_map,
                        existing_slugs=existing_slugs,
                        now_str=now_str,
                        tier=tier,
                    )
                    if success_rows:
                        writer = hitier_writer if tier == "hi" else longtail_writer
                        writer.append(success_rows)
                        checkpoint.mark_done([norm_keyword(r["focus_keyword"]) for r in success_rows])
                        total_success += len(success_rows)
                    if still_failed:
                        logger.warning("재시도 후에도 실패: %d개", len(still_failed))
                    time.sleep(args.sleep)

    checkpoint.save()

    print(f"\n{'='*60}")
    print(f"완료: 성공 {total_success:,}개 / 실패 {total_fail:,}개")
    print(f"고티어 출력: {HITIER_CSV}")
    print(f"롱테일 출력: {LONGTAIL_CSV}")
    if retry_queue:
        print(f"재시도 큐 저장: {RETRY_QUEUE_FILE}")
    print("통합 파일 생성은: python scripts/merge_l3_expanded.py")
    print(f"{'='*60}")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="L3 대량 생성 (keyword → L3 메타)")
    p.add_argument("--tier", choices=["hi", "lt"], default=None,
                   help="처리 티어. 기본: 전체 (고티어→롱테일 순)")
    p.add_argument("--resume", action="store_true",
                   help="체크포인트 이어서 실행")
    p.add_argument("--hitier-model", choices=["gemini", "scout", "8b"], default="gemini",
                   help="고티어 모델 (기본: gemini)")
    p.add_argument("--longtail-model", choices=["gemini", "scout", "8b"], default="scout",
                   help="롱테일 모델 (기본: scout). 실험 후 8b로 변경 권장")
    p.add_argument("--hitier-batch", type=int, default=10,
                   help="고티어 배치 크기 (기본: 10)")
    p.add_argument("--longtail-batch", type=int, default=25,
                   help="롱테일 배치 크기 (기본: 25)")
    p.add_argument("--sleep", type=float, default=1.5,
                   help="배치 간 대기 초 (기본: 1.5)")
    p.add_argument("--dry-run", action="store_true",
                   help="처음 3배치만 실행 (테스트용)")
    return p.parse_args()


if __name__ == "__main__":
    args = parse_args()
    if not args.resume and CHECKPOINT_FILE.exists():
        print(f"체크포인트 파일이 있습니다: {CHECKPOINT_FILE}")
        print("이어서 실행하려면 --resume, 처음부터 하려면 체크포인트 파일 삭제 후 재실행.")
        sys.exit(0)
    run(args)
