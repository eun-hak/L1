#!/usr/bin/env python3
"""L3 대량생성 모델 실험 — keyword→메타 변환 태스크.

기존 compare 스크립트와 차이:
  - 기존: L2 주어짐 → LLM이 새 키워드 창작 (생성)
  - 이번: 키워드 이미 있음 → LLM이 title/intent/angle/desc만 생성 (변환)

대상 모델:
  A. gemini-3.1-flash-lite   (Gemini Flash Lite)
  B. llama-4-scout-17b       (Groq Scout)
  C. llama-3.1-8b-instant    (Groq 8B, 10K RPD)

실험 조건:
  - 도메인: L1 02 금융·재테크 / 09 맛집·카페 / 07 IT·테크
  - 배치크기: 10개/콜, 25개/콜
  - 각 도메인×배치크기 당 50개 샘플

출력: outputs/experiment_l3_models.json + 콘솔 요약
"""

from __future__ import annotations

import csv
import json
import logging
import re
import sys
import time
from collections import Counter
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

logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────
# 모델 상수
# ─────────────────────────────────────────────
MODEL_8B = "llama-3.1-8b-instant"

VALID_INTENTS = frozenset({"info", "howto", "compare", "cost", "checklist", "review", "news"})
VALID_ANGLES = frozenset({"start", "howto", "compare", "tip", "review", "issue", "local"})

CLICKBAIT_RE = re.compile(
    r"완벽\s*가이드|놓치면\s*후회|꼭\s*알아야|총정리\s*필독|100%\s*|충격|대박|필수\s*정리",
    re.IGNORECASE,
)

# 실험 도메인 설정
EXPERIMENT_DOMAINS = [
    {"l1_code": "02", "l1_name": "금융·재테크", "ymyl": True,  "label": "finance"},
    {"l1_code": "09", "l1_name": "맛집·카페",   "ymyl": False, "label": "food"},
    {"l1_code": "07", "l1_name": "IT·테크",     "ymyl": False, "label": "it"},
]
SAMPLE_PER_DOMAIN = 50
BATCH_SIZES = [10, 25]

CANDIDATES_CSV = ROOT / "outputs" / "l2_candidates_gte100_depth1.csv"
OUT_DIR = ROOT / "outputs"
OUT_JSON = OUT_DIR / "experiment_l3_models.json"


# ─────────────────────────────────────────────
# 프롬프트 빌더
# ─────────────────────────────────────────────

SYSTEM_PROMPT = (
    "너는 한국어 네이버 블로그 SEO 콘텐츠 기획자야. "
    "주어진 검색 키워드를 블로그 콘텐츠 소재로 가공한다. "
    "반드시 유효한 JSON 배열만 출력한다. 설명·마크다운·코드블록 금지."
)

YMYL_FINANCE_GUARD = (
    "⚠ 금융 콘텐츠 주의: 투자 권유·수익 보장 단정 금지. "
    "'전망·정보·비교·분석' 톤으로만. "
    "예) '지금 사라' X → '전망과 분석 포인트' O"
)

YMYL_HEALTH_GUARD = (
    "⚠ 건강 콘텐츠 주의: 치료 효능·진단 단정 금지. "
    "'정보·관리·예방' 톤. 필요시 '전문가 상담 권유' 뉘앙스."
)


def build_conversion_prompt(
    keywords: list[dict[str, str]],
    *,
    l1_name: str,
    ymyl: bool = False,
    ymyl_type: str = "finance",
) -> str:
    """keywords 리스트(idx, keyword, monthly_total 포함) → 변환 프롬프트."""
    kw_lines = "\n".join(
        f"{item['idx']}. {item['keyword']}  (월 {item['monthly_total']:,}회)"
        for item in keywords
    )
    ymyl_section = ""
    if ymyl:
        guard = YMYL_FINANCE_GUARD if ymyl_type == "finance" else YMYL_HEALTH_GUARD
        ymyl_section = f"\n\n{guard}\n"

    return f"""다음은 '{l1_name}' 분야의 실제 검색 키워드 {len(keywords)}개다.
각 키워드를 한국어 블로그 콘텐츠 소재로 가공해라.{ymyl_section}

[규칙]
- focus_keyword: 원본 그대로 (절대 수정 금지)
- title_ko: 키워드를 자연스럽게 포함한 12~60자 제목. 클릭하고 싶게, 단 과장·낚시 금지.
- search_intent: info / howto / compare / cost / checklist / review / news 중 1개
- topic_angle: start / howto / compare / tip / review / issue / local 중 1개
- description: 이 글이 무엇을 다루는지 1문장 요약

[키워드 목록]
{kw_lines}

JSON 배열만 출력. 예시 포맷:
[
  {{"idx": 1, "focus_keyword": "삼성전자주가", "title_ko": "삼성전자 주가 전망과 핵심 투자 포인트", "search_intent": "info", "topic_angle": "issue", "description": "삼성전자 주가 흐름과 투자자가 알아야 할 분석 포인트를 정리한다."}}
]"""


# ─────────────────────────────────────────────
# LLM 호출 래퍼 (모델별 분기)
# ─────────────────────────────────────────────


def call_gemini(prompt: str, *, model: str = GEMINI_FLASH_LITE) -> tuple[str, float, bool]:
    """(raw_text, elapsed_sec, truncated) 반환."""
    t0 = time.time()
    try:
        result = gemini_chat_full(prompt, system=SYSTEM_PROMPT, model=model, temperature=0.55)
        elapsed = time.time() - t0
        truncated = len(result.text) > 3800
        return result.text, elapsed, truncated
    except Exception as exc:
        return f"ERROR:{exc}", time.time() - t0, False


def call_groq(prompt: str, *, model: str) -> tuple[str, float, bool]:
    """(raw_text, elapsed_sec, truncated) 반환."""
    t0 = time.time()
    try:
        result = groq_chat_full(prompt, system=SYSTEM_PROMPT, model=model, temperature=0.55, max_tokens=4096)
        elapsed = time.time() - t0
        truncated = result.text.strip().endswith("...") or len(result.text) > 3800
        return result.text, elapsed, truncated
    except Exception as exc:
        return f"ERROR:{exc}", time.time() - t0, False


def call_model(
    model_key: str,
    prompt: str,
) -> tuple[str, float, bool]:
    """model_key: 'gemini' | 'scout' | '8b'"""
    if model_key == "gemini":
        return call_gemini(prompt, model=GEMINI_FLASH_LITE)
    elif model_key == "scout":
        return call_groq(prompt, model=SCOUT_MODEL)
    elif model_key == "8b":
        return call_groq(prompt, model=MODEL_8B)
    raise ValueError(f"알 수 없는 모델 키: {model_key}")


# ─────────────────────────────────────────────
# 배치 실험
# ─────────────────────────────────────────────


def run_batch_experiment(
    model_key: str,
    samples: list[dict[str, str]],
    batch_size: int,
    *,
    l1_name: str,
    ymyl: bool,
    ymyl_type: str = "finance",
) -> dict[str, Any]:
    """한 모델 × 한 배치크기 실험 실행 후 결과 dict 반환."""
    results: list[dict] = []
    call_stats: list[dict] = []
    retry_queue: list[dict[str, str]] = []

    for i in range(0, len(samples), batch_size):
        chunk = samples[i : i + batch_size]
        items = [
            {"idx": j + 1, "keyword": c["candidate_keyword"], "monthly_total": int(c["monthly_total"])}
            for j, c in enumerate(chunk)
        ]
        prompt = build_conversion_prompt(items, l1_name=l1_name, ymyl=ymyl, ymyl_type=ymyl_type)

        raw, elapsed, truncated = call_model(model_key, prompt)
        is_error = raw.startswith("ERROR:")

        parsed: list[dict] = []
        parse_ok = False
        if not is_error:
            parsed = parse_json_array(raw, raise_on_fail=False)
            parse_ok = len(parsed) >= len(chunk) * 0.7  # 70% 이상 파싱되면 성공

        call_stats.append({
            "batch_start": i,
            "batch_size": len(chunk),
            "elapsed_sec": round(elapsed, 2),
            "truncated": truncated,
            "parse_ok": parse_ok,
            "parsed_count": len(parsed),
            "error": raw[:120] if is_error else None,
        })

        if parse_ok:
            idx_map = {str(item["idx"]): orig for item, orig in zip(items, chunk)}
            for p in parsed:
                idx_key = str(p.get("idx", ""))
                orig = idx_map.get(idx_key)
                if orig:
                    results.append({
                        "focus_keyword": orig["candidate_keyword"],
                        "monthly_total": int(orig["monthly_total"]),
                        "title_ko": p.get("title_ko", ""),
                        "search_intent": p.get("search_intent", ""),
                        "topic_angle": p.get("topic_angle", ""),
                        "description": p.get("description", ""),
                        "raw_idx": idx_key,
                    })
        else:
            retry_queue.extend(chunk)

        time.sleep(1.5)

    return {
        "model": model_key,
        "batch_size": batch_size,
        "total_input": len(samples),
        "total_calls": len(call_stats),
        "total_output": len(results),
        "retry_count": len(retry_queue),
        "call_stats": call_stats,
        "results": results,
    }


# ─────────────────────────────────────────────
# 품질 분석
# ─────────────────────────────────────────────


def analyze_results(results: list[dict]) -> dict[str, Any]:
    """생성 결과 품질 지표 계산."""
    if not results:
        return {"count": 0}

    titles = [r["title_ko"] for r in results]
    intents = Counter(r["search_intent"] for r in results)
    angles = Counter(r["topic_angle"] for r in results)

    title_lengths = [len(t) for t in titles if t]
    valid_len = sum(1 for tl in title_lengths if 12 <= tl <= 60)
    clickbait = sum(1 for t in titles if CLICKBAIT_RE.search(t))
    valid_intents = sum(1 for r in results if r["search_intent"] in VALID_INTENTS)
    valid_angles = sum(1 for r in results if r["topic_angle"] in VALID_ANGLES)
    info_ratio = intents.get("info", 0) / max(len(results), 1)

    # 제목에 focus_keyword 포함 여부
    kw_in_title = sum(
        1 for r in results
        if r["focus_keyword"] and r["focus_keyword"] in r["title_ko"]
    )

    return {
        "count": len(results),
        "title_len_ok_pct": round(valid_len / max(len(title_lengths), 1) * 100, 1),
        "title_len_avg": round(sum(title_lengths) / max(len(title_lengths), 1), 1),
        "title_len_min": min(title_lengths) if title_lengths else 0,
        "title_len_max": max(title_lengths) if title_lengths else 0,
        "clickbait_count": clickbait,
        "valid_intent_pct": round(valid_intents / len(results) * 100, 1),
        "valid_angle_pct": round(valid_angles / len(results) * 100, 1),
        "info_ratio_pct": round(info_ratio * 100, 1),
        "intent_dist": dict(intents.most_common()),
        "angle_dist": dict(angles.most_common()),
        "kw_in_title_pct": round(kw_in_title / len(results) * 100, 1),
        "sample_pairs": [
            {"keyword": r["focus_keyword"], "title": r["title_ko"], "intent": r["search_intent"]}
            for r in results[:8]
        ],
    }


# ─────────────────────────────────────────────
# 콘솔 출력 헬퍼
# ─────────────────────────────────────────────


def print_comparison_table(domain_label: str, experiments: list[dict]) -> None:
    """배치크기별 실험 결과를 가로로 비교 출력."""
    print(f"\n{'='*70}")
    print(f"  도메인: {domain_label}")
    print(f"{'='*70}")

    # 배치크기별 그룹
    by_batch: dict[int, list[dict]] = {}
    for exp in experiments:
        bs = exp["batch_exp"]["batch_size"]
        by_batch.setdefault(bs, []).append(exp)

    for bs, exps in sorted(by_batch.items()):
        print(f"\n  [배치크기 {bs}개/콜]")
        header = f"  {'모델':<10} {'입력':>5} {'출력':>5} {'재시도':>5} {'파싱성공%':>8} {'제목길이OK%':>10} {'Info쏠림%':>10} {'클릭베이트':>10}"
        print(header)
        print("  " + "-" * 68)
        for exp in exps:
            bs_data = exp["batch_exp"]
            qa = exp["quality"]
            call_ok = sum(1 for c in bs_data["call_stats"] if c["parse_ok"])
            total_calls = len(bs_data["call_stats"])
            parse_pct = round(call_ok / max(total_calls, 1) * 100, 0)
            row = (
                f"  {exp['model_key']:<10}"
                f" {bs_data['total_input']:>5}"
                f" {qa['count']:>5}"
                f" {bs_data['retry_count']:>5}"
                f" {parse_pct:>7.0f}%"
                f" {qa.get('title_len_ok_pct', 0):>9.1f}%"
                f" {qa.get('info_ratio_pct', 0):>9.1f}%"
                f" {qa.get('clickbait_count', 0):>10}"
            )
            print(row)

    print()
    print("  [샘플 제목 비교 — 배치10, 동일 키워드]")
    batch10_exps = by_batch.get(10, [])
    if batch10_exps:
        for exp in batch10_exps:
            pairs = exp["quality"].get("sample_pairs", [])[:3]
            print(f"  << {exp['model_key']} >>")
            for p in pairs:
                print(f"    키워드: {p['keyword']}")
                print(f"    제목  : {p['title']}  [{p['intent']}]")


# ─────────────────────────────────────────────
# 메인
# ─────────────────────────────────────────────


def load_samples(l1_code: str, n: int) -> list[dict[str, str]]:
    """L1 코드 기준으로 상위 검색량 n개 샘플링 (고티어·롱테일 균형)."""
    rows = [
        r for r in csv.DictReader(CANDIDATES_CSV.open(encoding="utf-8-sig"))
        if r["l1_code"] == l1_code
    ]
    rows.sort(key=lambda r: int(r["monthly_total"]), reverse=True)
    hi = [r for r in rows if int(r["monthly_total"]) >= 1000]
    lt = [r for r in rows if int(r["monthly_total"]) < 1000]
    hi_n = min(n // 2, len(hi))
    lt_n = min(n - hi_n, len(lt))
    return hi[:hi_n] + lt[:lt_n]


MODELS_TO_TEST = [
    {"key": "gemini", "label": "Gemini Flash Lite"},
    {"key": "scout",  "label": "Groq Scout 17B"},
    {"key": "8b",     "label": "Groq Llama 3.1 8B"},
]


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc).isoformat()

    print(f"L3 모델 실험 시작: {now}")
    print(f"도메인: {[d['label'] for d in EXPERIMENT_DOMAINS]}")
    print(f"모델: {[m['key'] for m in MODELS_TO_TEST]}")
    print(f"배치크기: {BATCH_SIZES}")
    print(f"도메인당 샘플: {SAMPLE_PER_DOMAIN}개")
    total_calls = len(EXPERIMENT_DOMAINS) * len(MODELS_TO_TEST) * len(BATCH_SIZES) * (SAMPLE_PER_DOMAIN // min(BATCH_SIZES))
    print(f"예상 API 콜 수 (최대): ~{total_calls}회\n")

    report: dict[str, Any] = {
        "generated_at": now,
        "models": {m["key"]: m["label"] for m in MODELS_TO_TEST},
        "gemini_model": GEMINI_FLASH_LITE,
        "scout_model": SCOUT_MODEL,
        "llama8b_model": MODEL_8B,
        "batch_sizes": BATCH_SIZES,
        "sample_per_domain": SAMPLE_PER_DOMAIN,
        "domains": {},
    }

    for domain in EXPERIMENT_DOMAINS:
        l1_code = domain["l1_code"]
        l1_name = domain["l1_name"]
        label = domain["label"]
        ymyl = domain["ymyl"]
        ymyl_type = "finance" if l1_code == "02" else "health"

        print(f"\n{'#'*60}")
        print(f"  도메인: {l1_name} (l1={l1_code}, ymyl={ymyl})")
        print(f"{'#'*60}")

        samples = load_samples(l1_code, SAMPLE_PER_DOMAIN)
        print(f"  샘플 {len(samples)}개 로드: 고티어={sum(1 for r in samples if int(r['monthly_total'])>=1000)}, 롱테일={sum(1 for r in samples if int(r['monthly_total'])<1000)}")

        domain_exps: list[dict] = []

        for bs in BATCH_SIZES:
            for model_info in MODELS_TO_TEST:
                model_key = model_info["key"]
                print(f"\n  [{model_key}] 배치크기={bs} ... ", end="", flush=True)

                batch_result = run_batch_experiment(
                    model_key,
                    samples,
                    bs,
                    l1_name=l1_name,
                    ymyl=ymyl,
                    ymyl_type=ymyl_type,
                )
                quality = analyze_results(batch_result["results"])
                print(
                    f"완료 (출력 {quality['count']}/{len(samples)}, "
                    f"파싱OK: {sum(1 for c in batch_result['call_stats'] if c['parse_ok'])}/{len(batch_result['call_stats'])}콜)"
                )

                domain_exps.append({
                    "model_key": model_key,
                    "batch_exp": batch_result,
                    "quality": quality,
                })

                time.sleep(2)  # 모델 간 쿨다운

        print_comparison_table(f"{l1_name} (L1={l1_code})", domain_exps)
        report["domains"][label] = {
            "l1_code": l1_code,
            "l1_name": l1_name,
            "ymyl": ymyl,
            "experiments": domain_exps,
        }

    # 최종 요약
    print(f"\n{'='*70}")
    print("  최종 권고 기준 (참고값)")
    print(f"{'='*70}")
    print("  JSON 파싱성공률 >= 85%, 제목길이OK >= 90%, Info쏠림 <= 60%")
    print("  → 위 기준 충족 모델을 롱테일(31,368개) 배정 후보로 권장")
    print()

    # 모델별 평균 스코어
    model_scores: dict[str, list[dict]] = {}
    for dom_data in report["domains"].values():
        for exp in dom_data["experiments"]:
            mk = exp["model_key"]
            qa = exp["quality"]
            bs = exp["batch_exp"]["batch_size"]
            if bs != 10:
                continue
            model_scores.setdefault(mk, []).append(qa)

    for mk, qas in model_scores.items():
        avg_parse = sum(
            sum(1 for c in exp["batch_exp"]["call_stats"] if c["parse_ok"]) / max(len(exp["batch_exp"]["call_stats"]), 1)
            for exp in [e for dom in report["domains"].values() for e in dom["experiments"]
                        if e["model_key"] == mk and e["batch_exp"]["batch_size"] == 10]
        ) / max(len(qas), 1) * 100
        avg_title_ok = sum(q.get("title_len_ok_pct", 0) for q in qas) / len(qas)
        avg_info = sum(q.get("info_ratio_pct", 0) for q in qas) / len(qas)
        avg_cb = sum(q.get("clickbait_count", 0) for q in qas) / len(qas)
        label = MODELS_TO_TEST[[m["key"] for m in MODELS_TO_TEST].index(mk)]["label"]
        recommend = "✓ 권장" if avg_parse >= 85 and avg_title_ok >= 90 and avg_info <= 60 else "✗ 재검토"
        print(f"  {label:<25} 파싱:{avg_parse:.0f}%  제목OK:{avg_title_ok:.0f}%  Info쏠림:{avg_info:.0f}%  클릭베이트:{avg_cb:.1f}  → {recommend}")

    OUT_JSON.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n상세 결과: {OUT_JSON}")
    print("\n다음 단계: 실험 결과 확인 후 generate_l3_expanded.py 실행")


if __name__ == "__main__":
    main()
