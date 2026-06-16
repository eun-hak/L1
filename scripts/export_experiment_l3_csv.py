#!/usr/bin/env python3
"""experiment_l3_models.json → CSV 변환.

출력:
  outputs/experiment_l3_summary.csv   도메인×모델×배치 품질 요약
  outputs/experiment_l3_results.csv   키워드별 생성 결과 전체
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
IN_JSON = ROOT / "outputs" / "experiment_l3_models.json"
OUT_SUMMARY = ROOT / "outputs" / "experiment_l3_summary.csv"
OUT_RESULTS = ROOT / "outputs" / "experiment_l3_results.csv"

SUMMARY_FIELDS = [
    "domain_label", "l1_code", "l1_name", "ymyl",
    "model_key", "model_label", "batch_size",
    "total_input", "total_output", "retry_count",
    "parse_success_pct", "total_calls", "parse_ok_calls",
    "title_len_ok_pct", "title_len_avg", "title_len_min", "title_len_max",
    "valid_intent_pct", "valid_angle_pct", "info_ratio_pct",
    "clickbait_count", "kw_in_title_pct",
    "intent_dist", "angle_dist",
]

RESULT_FIELDS = [
    "domain_label", "l1_code", "l1_name", "ymyl",
    "model_key", "model_label", "batch_size",
    "focus_keyword", "monthly_total",
    "title_ko", "search_intent", "topic_angle", "description",
]


def main() -> None:
    if not IN_JSON.exists():
        raise SystemExit(f"입력 파일 없음: {IN_JSON}")

    data = json.loads(IN_JSON.read_text(encoding="utf-8"))
    model_labels: dict[str, str] = data.get("models", {})

    summary_rows: list[dict] = []
    result_rows: list[dict] = []

    for domain_label, dom in data.get("domains", {}).items():
        l1_code = dom.get("l1_code", "")
        l1_name = dom.get("l1_name", "")
        ymyl = dom.get("ymyl", False)

        for exp in dom.get("experiments", []):
            model_key = exp["model_key"]
            model_label = model_labels.get(model_key, model_key)
            batch_exp = exp["batch_exp"]
            quality = exp.get("quality", {})
            batch_size = batch_exp["batch_size"]
            call_stats = batch_exp.get("call_stats", [])
            parse_ok = sum(1 for c in call_stats if c.get("parse_ok"))
            total_calls = len(call_stats)
            parse_pct = round(parse_ok / max(total_calls, 1) * 100, 1)

            summary_rows.append({
                "domain_label": domain_label,
                "l1_code": l1_code,
                "l1_name": l1_name,
                "ymyl": ymyl,
                "model_key": model_key,
                "model_label": model_label,
                "batch_size": batch_size,
                "total_input": batch_exp.get("total_input", 0),
                "total_output": quality.get("count", batch_exp.get("total_output", 0)),
                "retry_count": batch_exp.get("retry_count", 0),
                "parse_success_pct": parse_pct,
                "total_calls": total_calls,
                "parse_ok_calls": parse_ok,
                "title_len_ok_pct": quality.get("title_len_ok_pct", ""),
                "title_len_avg": quality.get("title_len_avg", ""),
                "title_len_min": quality.get("title_len_min", ""),
                "title_len_max": quality.get("title_len_max", ""),
                "valid_intent_pct": quality.get("valid_intent_pct", ""),
                "valid_angle_pct": quality.get("valid_angle_pct", ""),
                "info_ratio_pct": quality.get("info_ratio_pct", ""),
                "clickbait_count": quality.get("clickbait_count", ""),
                "kw_in_title_pct": quality.get("kw_in_title_pct", ""),
                "intent_dist": json.dumps(quality.get("intent_dist", {}), ensure_ascii=False),
                "angle_dist": json.dumps(quality.get("angle_dist", {}), ensure_ascii=False),
            })

            for r in batch_exp.get("results", []):
                result_rows.append({
                    "domain_label": domain_label,
                    "l1_code": l1_code,
                    "l1_name": l1_name,
                    "ymyl": ymyl,
                    "model_key": model_key,
                    "model_label": model_label,
                    "batch_size": batch_size,
                    "focus_keyword": r.get("focus_keyword", ""),
                    "monthly_total": r.get("monthly_total", ""),
                    "title_ko": r.get("title_ko", ""),
                    "search_intent": r.get("search_intent", ""),
                    "topic_angle": r.get("topic_angle", ""),
                    "description": r.get("description", ""),
                })

    OUT_SUMMARY.parent.mkdir(parents=True, exist_ok=True)

    for path, fields, rows in (
        (OUT_SUMMARY, SUMMARY_FIELDS, summary_rows),
        (OUT_RESULTS, RESULT_FIELDS, result_rows),
    ):
        with open(path, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, fieldnames=fields)
            w.writeheader()
            w.writerows(rows)
        print(f"저장: {path} ({len(rows)}행)")

    print(f"\n입력: {IN_JSON}")


if __name__ == "__main__":
    main()
