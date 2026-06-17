#!/usr/bin/env python3
"""L3 대량 생성 — 5만 키워드 → L3 메타 (8b + YMYL Gemini).

모델 배정:
  - L1 02·03 (YMYL 금융·건강): Gemini batch 10
  - 나머지 ~4.3만: 8b batch 10 → 실패 시 5 → single → Scout

일일 한도 (cron용):
  - 8b 10,000 / Gemini 500 / Scout 800 calls (기본)
  - 한도 도달 시 checkpoint 저장 후 종료 → 다음 cron에서 --resume

사용:
  # 매일 cron (로컬 Mac/Linux)
  ./scripts/run_l3_daily.sh

  # 수동
  python3 scripts/generate_l3_expanded.py --resume
  python3 scripts/generate_l3_expanded.py --resume --max-batches 5   # 테스트

  # 재시도 큐만
  python3 scripts/generate_l3_expanded.py --resume --retry-only

출력:
  outputs/l3_expanded_hitier.csv
  outputs/l3_expanded_longtail.csv
  outputs/l3_bulk_checkpoint.json
  outputs/l3_bulk_retry_queue.jsonl
  outputs/l3_daily_budget.json
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
from l3_bulk_config import (  # noqa: E402
    CANDIDATES_CSV,
    CHECKPOINT_FILE,
    DAILY_BUDGET_FILE,
    DEFAULT_DAILY_LIMITS,
    HITIER_CSV,
    HITIER_THRESHOLD,
    L1_NAMES,
    L3_FIELDNAMES,
    LONGTAIL_CSV,
    OUT_DIR,
    RETRY_PLAN_DEFAULT,
    RETRY_PLAN_YMYL,
    RETRY_QUEUE_FILE,
    RUN_LOG_FILE,
    TOPICS_L2_CSV,
    YMYL_L1_CODES,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

MODEL_8B = "llama-3.1-8b-instant"
VALID_INTENTS = frozenset({"info", "howto", "compare", "cost", "checklist", "review", "news"})
VALID_ANGLES = frozenset({"start", "howto", "compare", "tip", "review", "issue", "local"})
CLICKBAIT_RE = re.compile(
    r"완벽\s*가이드|놓치면\s*후회|꼭\s*알아야|총정리\s*필독|100%\s*|충격|대박|필수\s*정리",
    re.IGNORECASE,
)

SYSTEM_PROMPT = (
    "너는 한국어 네이버 블로그 SEO 콘텐츠 기획자야. "
    "주어진 검색 키워드를 블로그 콘텐츠 소재로 가공한다. "
    "반드시 유효한 JSON 배열만 출력한다. 설명·마크다운·코드블록 금지."
)
YMYL_FINANCE_GUARD = (
    "⚠ 금융 콘텐츠 주의: 투자 권유·수익 보장 단정 금지. "
    "'전망·정보·비교·분석' 톤으로만."
)
YMYL_HEALTH_GUARD = (
    "⚠ 건강 콘텐츠 주의: 치료 효능·진단 단정 금지. "
    "'정보·관리·예방' 톤. 필요시 전문가 상담 권유."
)


# ─── 유틸 ────────────────────────────────────────────────────────────────────


def norm_keyword(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lower())


def slugify_ko(text: str) -> str:
    import hashlib
    h = hashlib.md5(text.encode("utf-8")).hexdigest()[:8]
    en_part = re.sub(r"[^a-z0-9]", "", text.lower())[:12]
    return f"{en_part}-{h}" if en_part else f"kw-{h}"


def is_ymyl(l1_code: str) -> bool:
    return l1_code in YMYL_L1_CODES


def primary_model(l1_code: str) -> str:
    return "gemini" if is_ymyl(l1_code) else "8b"


def tier_for(total: int) -> str:
    return "hi" if total >= HITIER_THRESHOLD else "lt"


def model_label(model_key: str) -> str:
    if model_key == "gemini":
        return GEMINI_FLASH_LITE
    if model_key == "scout":
        return SCOUT_MODEL
    return MODEL_8B


def load_l2_map() -> dict[str, dict[str, str]]:
    if not TOPICS_L2_CSV.exists():
        logger.warning("L2 CSV 없음: %s", TOPICS_L2_CSV)
        return {}
    return {r["l2_id"]: r for r in csv.DictReader(TOPICS_L2_CSV.open(encoding="utf-8-sig"))}


def assign_l2_id(row: dict[str, str], l2_map: dict) -> str:
    for seed in (s.strip() for s in row.get("source_seed", "").split("|") if s.strip()):
        if seed in l2_map:
            return seed
    l1 = row["l1_code"]
    for l2_id, l2_row in l2_map.items():
        if l2_row.get("l1_code") == l1:
            return l2_id
    return f"{l1}-unknown"


def build_l3_slug(l2_id: str, keyword: str, existing: set[str]) -> str:
    base = slugify_ko(keyword)
    slug = base
    n = 2
    while f"{l2_id}-{slug}" in existing:
        slug = f"{base}-{n}"
        n += 1
    existing.add(f"{l2_id}-{slug}")
    return slug


def build_prompt(items: list[dict], *, l1_name: str, l1_code: str) -> str:
    kw_lines = "\n".join(
        f"{item['idx']}. {item['keyword']}  (월 {item['monthly_total']:,}회)"
        for item in items
    )
    ymyl = ""
    if l1_code == "02":
        ymyl = f"\n\n{YMYL_FINANCE_GUARD}\n"
    elif l1_code == "03":
        ymyl = f"\n\n{YMYL_HEALTH_GUARD}\n"
    return f"""다음은 '{l1_name}' 분야의 실제 검색 키워드 {len(items)}개다.
각 키워드를 한국어 블로그 콘텐츠 소재로 가공해라.{ymyl}

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


def call_model(model_key: str, prompt: str) -> tuple[list[dict], bool]:
    try:
        if model_key == "gemini":
            raw = gemini_chat_full(
                prompt, system=SYSTEM_PROMPT, model=GEMINI_FLASH_LITE,
                temperature=0.55, max_tokens=4096,
            ).text
        elif model_key == "scout":
            raw = groq_chat_full(
                prompt, system=SYSTEM_PROMPT, model=SCOUT_MODEL,
                temperature=0.55, max_tokens=4096,
            ).text
        elif model_key == "8b":
            raw = groq_chat_full(
                prompt, system=SYSTEM_PROMPT, model=MODEL_8B,
                temperature=0.55, max_tokens=4096,
            ).text
        else:
            raise ValueError(model_key)
        return parse_json_array(raw, raise_on_fail=False), True
    except Exception as exc:
        logger.warning("LLM 실패 model=%s: %s", model_key, exc)
        return [], False


def validate_item(item: dict, *, orig_keyword: str) -> tuple[bool, list[str]]:
    warns: list[str] = []
    title = item.get("title_ko", "")
    if not title:
        return False, ["제목 없음"]
    tl = len(title)
    if tl < 12:
        warns.append(f"제목 짧음({tl})")
    elif tl > 60:
        warns.append(f"제목 김({tl})")
    if CLICKBAIT_RE.search(title):
        warns.append("클릭베이트")
    if item.get("search_intent") not in VALID_INTENTS:
        item["search_intent"] = "info"
        warns.append("intent→info")
    if item.get("topic_angle") not in VALID_ANGLES:
        item["topic_angle"] = "tip"
        warns.append("angle→tip")
    if norm_keyword(item.get("focus_keyword", "")) != norm_keyword(orig_keyword):
        item["focus_keyword"] = orig_keyword
        warns.append("focus_keyword 복원")
    return True, warns


# ─── 상태: checkpoint / daily budget / retry ───────────────────────────────


class Checkpoint:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._done: set[str] = set()
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            self._done = set(data.get("done_norms", []))
            logger.info("checkpoint: %d 완료", len(self._done))

    def done(self, nk: str) -> bool:
        return nk in self._done

    def mark(self, norms: list[str]) -> None:
        self._done.update(norms)

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps({"done_norms": sorted(self._done), "count": len(self._done)}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    @property
    def count(self) -> int:
        return len(self._done)


class DailyBudget:
    def __init__(self, path: Path, limits: dict[str, int]) -> None:
        self.path = path
        self.limits = limits
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        self.date = today
        self.calls = {"8b": 0, "gemini": 0, "scout": 0}
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            if data.get("date") == today:
                self.calls = {**self.calls, **data.get("calls", {})}

    def can_call(self, model_key: str, n: int = 1) -> bool:
        return self.calls.get(model_key, 0) + n <= self.limits.get(model_key, 999_999)

    def record(self, model_key: str, n: int = 1) -> None:
        self.calls[model_key] = self.calls.get(model_key, 0) + n

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps({"date": self.date, "calls": self.calls, "limits": self.limits}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def summary(self) -> str:
        parts = [f"{k}:{self.calls[k]}/{self.limits[k]}" for k in ("8b", "gemini", "scout")]
        return " ".join(parts)


def load_retry_queue() -> list[dict]:
    if not RETRY_QUEUE_FILE.exists():
        return []
    out = []
    for line in RETRY_QUEUE_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            out.append(json.loads(line))
    return out


def save_retry_queue(items: list[dict]) -> None:
    RETRY_QUEUE_FILE.parent.mkdir(parents=True, exist_ok=True)
    if not items:
        if RETRY_QUEUE_FILE.exists():
            RETRY_QUEUE_FILE.unlink()
        return
    RETRY_QUEUE_FILE.write_text(
        "\n".join(json.dumps(x, ensure_ascii=False) for x in items) + "\n",
        encoding="utf-8",
    )


class CsvAppender:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._exists = path.exists()

    def append(self, rows: list[dict]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        mode = "a" if self._exists else "w"
        with open(self.path, mode, encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, fieldnames=L3_FIELDNAMES, extrasaction="ignore")
            if not self._exists:
                w.writeheader()
                self._exists = True
            w.writerows(rows)


# ─── 배치 처리 ───────────────────────────────────────────────────────────────


def process_batch(
    batch: list[dict],
    *,
    model_key: str,
    batch_size: int,
    l2_map: dict,
    existing_slugs: set[str],
    now_str: str,
    meta_source: str,
) -> tuple[list[dict], list[dict]]:
    """(success_rows, failed_items with retry_stage)."""
    items = [
        {"idx": j + 1, "keyword": r["candidate_keyword"], "monthly_total": int(r["monthly_total"])}
        for j, r in enumerate(batch)
    ]
    l1_code = batch[0]["l1_code"]
    l1_name = L1_NAMES.get(l1_code, f"L1-{l1_code}")
    parsed, ok = call_model(model_key, build_prompt(items, l1_name=l1_name, l1_code=l1_code))
    if not ok or len(parsed) < max(1, len(batch) * 7 // 10):
        failed = [{**r, "retry_stage": r.get("retry_stage", 0) + 1} for r in batch]
        return [], failed

    idx_map = {str(it["idx"]): orig for it, orig in zip(items, batch)}
    success_rows: list[dict] = []
    parsed_kws: set[str] = set()

    for p in parsed:
        orig = idx_map.get(str(p.get("idx", "")))
        if not orig:
            continue
        valid, _ = validate_item(p, orig_keyword=orig["candidate_keyword"])
        if not valid:
            continue
        parsed_kws.add(norm_keyword(orig["candidate_keyword"]))
        l2_id = assign_l2_id(orig, l2_map)
        slug = build_l3_slug(l2_id, orig["candidate_keyword"], existing_slugs)
        tier = tier_for(int(orig["monthly_total"]))
        success_rows.append({
            "l3_id": f"{l2_id}-{slug}",
            "l2_id": l2_id,
            "l1_code": l1_code,
            "l3_slug": slug,
            "focus_keyword": p["focus_keyword"],
            "title_ko": p["title_ko"],
            "search_intent": p["search_intent"],
            "topic_angle": p["topic_angle"],
            "description": p.get("description", ""),
            "model": f"{model_key}:{model_label(model_key)}",
            "generated_at": now_str,
            "monthly_total": orig["monthly_total"],
            "tier": tier,
            "meta_source": meta_source,
        })

    failed: list[dict] = []
    for orig in batch:
        nk = norm_keyword(orig["candidate_keyword"])
        if nk not in parsed_kws:
            stage = int(orig.get("retry_stage", 0)) + 1
            failed.append({**orig, "retry_stage": stage})

    return success_rows, failed


def retry_plan_for(item: dict) -> tuple[str, int] | None:
    stage = int(item.get("retry_stage", 1))
    plan = RETRY_PLAN_YMYL if is_ymyl(item["l1_code"]) else RETRY_PLAN_DEFAULT
    if stage not in plan:
        return None
    return plan[stage]


# ─── 메인 루프 ───────────────────────────────────────────────────────────────


class Runner:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.checkpoint = Checkpoint(CHECKPOINT_FILE)
        self.budget = DailyBudget(DAILY_BUDGET_FILE, {
            "8b": args.max_calls_8b,
            "gemini": args.max_calls_gemini,
            "scout": args.max_calls_scout,
        })
        self.l2_map = load_l2_map()
        self.existing_slugs: set[str] = set()
        for p in (HITIER_CSV, LONGTAIL_CSV):
            if p.exists():
                for r in csv.DictReader(p.open(encoding="utf-8-sig")):
                    self.existing_slugs.add(r.get("l3_id", ""))
        self.hitier_w = CsvAppender(HITIER_CSV)
        self.longtail_w = CsvAppender(LONGTAIL_CSV)
        self.now_str = datetime.now(timezone.utc).isoformat()
        self.stats = defaultdict(int)
        self.batch_count = 0
        self.limit_hit = False

    def write_rows(self, rows: list[dict]) -> None:
        for r in rows:
            w = self.hitier_w if r["tier"] == "hi" else self.longtail_w
            w.append([r])
        self.checkpoint.mark([norm_keyword(r["focus_keyword"]) for r in rows])
        self.stats["success"] += len(rows)

    def run_batch(self, batch: list[dict], model_key: str, batch_size: int, meta_source: str) -> list[dict]:
        if not self.budget.can_call(model_key):
            self.limit_hit = True
            logger.info("일일 한도 도달 model=%s (%s)", model_key, self.budget.summary())
            return [{**r, "retry_stage": r.get("retry_stage", 0)} for r in batch]

        success, failed = process_batch(
            batch,
            model_key=model_key,
            batch_size=batch_size,
            l2_map=self.l2_map,
            existing_slugs=self.existing_slugs,
            now_str=self.now_str,
            meta_source=meta_source,
        )
        self.budget.record(model_key)
        self.batch_count += 1

        if success:
            self.write_rows(success)
        if failed:
            self.stats["failed"] += len(failed)

        time.sleep(self.args.sleep)
        return failed

    def process_primary(self, rows: list[dict]) -> list[dict]:
        retry: list[dict] = []
        by_l1: dict[str, list[dict]] = defaultdict(list)
        for r in rows:
            by_l1[r["l1_code"]].append(r)

        for l1_code in sorted(by_l1):
            if self.limit_hit or (self.args.max_batches and self.batch_count >= self.args.max_batches):
                break
            chunk_rows = by_l1[l1_code]
            model = primary_model(l1_code)
            bs = self.args.batch_size
            logger.info("L1=%s %s model=%s n=%d", l1_code, L1_NAMES.get(l1_code, ""), model, len(chunk_rows))
            for i in range(0, len(chunk_rows), bs):
                if self.limit_hit or (self.args.max_batches and self.batch_count >= self.args.max_batches):
                    retry.extend(chunk_rows[i:])
                    break
                batch = chunk_rows[i:i + bs]
                failed = self.run_batch(batch, model, bs, f"primary_{model}")
                retry.extend(failed)
                if self.batch_count % 20 == 0:
                    self.checkpoint.save()
                    self.budget.save()
        return retry

    def process_retry(self, items: list[dict]) -> list[dict]:
        remaining: list[dict] = []
        for item in items:
            if self.limit_hit or (self.args.max_batches and self.batch_count >= self.args.max_batches):
                remaining.append(item)
                continue
            plan = retry_plan_for(item)
            if plan is None:
                logger.warning("재시도 포기 stage=%s kw=%s", item.get("retry_stage"), item.get("candidate_keyword"))
                self.stats["abandoned"] += 1
                continue
            model_key, bs = plan
            failed = self.run_batch([item], model_key, bs, f"retry_s{item['retry_stage']}_{model_key}")
            if failed:
                if int(failed[0].get("retry_stage", 99)) > 3:
                    self.stats["abandoned"] += 1
                else:
                    remaining.extend(failed)
        return remaining

    def run(self) -> None:
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        if not CANDIDATES_CSV.exists():
            sys.exit(f"입력 없음: {CANDIDATES_CSV}")

        all_rows = list(csv.DictReader(CANDIDATES_CSV.open(encoding="utf-8-sig")))
        pending = [r for r in all_rows if not self.checkpoint.done(norm_keyword(r["candidate_keyword"]))]
        retry_q = load_retry_queue()

        logger.info(
            "전체 %d / 완료 %d / pending %d / retry큐 %d / budget [%s]",
            len(all_rows), self.checkpoint.count, len(pending), len(retry_q), self.budget.summary(),
        )

        new_retry: list[dict] = []
        if not self.args.retry_only and pending:
            new_retry = self.process_primary(pending)
        if retry_q or new_retry:
            merged = retry_q + new_retry
            still = self.process_retry(merged)
            save_retry_queue(still)
        else:
            save_retry_queue([])

        self.checkpoint.save()
        self.budget.save()

        msg = (
            f"success={self.stats['success']} failed={self.stats['failed']} "
            f"abandoned={self.stats['abandoned']} batches={self.batch_count} "
            f"budget=[{self.budget.summary()}] limit_hit={self.limit_hit}"
        )
        logger.info("완료 %s", msg)
        with open(RUN_LOG_FILE, "a", encoding="utf-8") as f:
            f.write(f"{datetime.now(timezone.utc).isoformat()} {msg}\n")

        print(f"\n{'='*60}")
        print(f"  L3 bulk run 완료")
        print(f"  성공: {self.stats['success']:,}  실패: {self.stats['failed']:,}  포기: {self.stats['abandoned']:,}")
        print(f"  checkpoint: {self.checkpoint.count:,} / {len(all_rows):,}")
        print(f"  budget: {self.budget.summary()}")
        if self.limit_hit:
            print("  → 일일 한도 도달. 내일 cron --resume 으로 이어서 실행.")
        print(f"  출력: {HITIER_CSV}")
        print(f"        {LONGTAIL_CSV}")
        print(f"{'='*60}")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="L3 bulk: 8b + YMYL Gemini")
    p.add_argument("--resume", action="store_true", help="checkpoint 이어서 (cron 필수)")
    p.add_argument("--retry-only", action="store_true", help="재시도 큐만 처리")
    p.add_argument("--batch-size", type=int, default=10, help="1차 배치 크기 (기본 10, 25 금지)")
    p.add_argument("--sleep", type=float, default=1.5, help="배치 간 대기(초)")
    p.add_argument("--max-batches", type=int, default=0, help="이번 실행 배치 상한 (0=무제한, 테스트용)")
    p.add_argument("--max-calls-8b", type=int, default=DEFAULT_DAILY_LIMITS["8b"])
    p.add_argument("--max-calls-gemini", type=int, default=DEFAULT_DAILY_LIMITS["gemini"])
    p.add_argument("--max-calls-scout", type=int, default=DEFAULT_DAILY_LIMITS["scout"])
    return p.parse_args()


def main() -> None:
    args = parse_args()
    if not args.resume and CHECKPOINT_FILE.exists():
        print(f"checkpoint 존재: {CHECKPOINT_FILE}")
        print("cron/재실행: python3 scripts/generate_l3_expanded.py --resume")
        print("처음부터: checkpoint·retry·budget 파일 삭제 후 실행")
        sys.exit(0)
    Runner(args).run()


if __name__ == "__main__":
    main()
