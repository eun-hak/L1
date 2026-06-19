#!/usr/bin/env python3
"""Gemini batch-40 검증 — L1·tier별 소량 테스트 후 요약."""

from __future__ import annotations

import csv
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))

from l3_bulk_config import (  # noqa: E402
    CANDIDATES_CSV,
    CHECKPOINT_FILE,
    DAILY_BUDGET_FILE,
    HITIER_CSV,
    HITIER_THRESHOLD,
    LONGTAIL_CSV,
    RETRY_QUEUE_FILE,
)

PY = ROOT / ".venv/bin/python"
GEN = SCRIPTS / "generate_l3_expanded.py"


def norm(s: str) -> str:
    return re.sub(r"\s+", " ", s.strip().lower())


def load_rows() -> list[dict]:
    return list(csv.DictReader(CANDIDATES_CSV.open(encoding="utf-8-sig")))


def reset_runtime() -> None:
    for p in (CHECKPOINT_FILE, RETRY_QUEUE_FILE, DAILY_BUDGET_FILE, HITIER_CSV, LONGTAIL_CSV):
        p.unlink(missing_ok=True)


def set_pending_only(target_rows: list[dict]) -> None:
    all_rows = load_rows()
    target_norms = {norm(r["candidate_keyword"]) for r in target_rows}
    done = sorted(norm(r["candidate_keyword"]) for r in all_rows if norm(r["candidate_keyword"]) not in target_norms)
    CHECKPOINT_FILE.parent.mkdir(parents=True, exist_ok=True)
    CHECKPOINT_FILE.write_text(
        json.dumps({"done_norms": done, "count": len(done)}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def pick_first(rows: list[dict], n: int) -> list[dict]:
    return rows[:n]


def run_batches(batch_size: int, max_batches: int) -> subprocess.CompletedProcess[str]:
    cmd = [
        str(PY), str(GEN),
        "--resume",
        "--batch-size", str(batch_size),
        "--max-batches", str(max_batches),
        "--max-calls-gemini", "500",
        "--max-calls-scout", "800",
        "--max-calls-8b", "0",
    ]
    return subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)


def read_output_rows() -> list[dict]:
    out: list[dict] = []
    for p in (HITIER_CSV, LONGTAIL_CSV):
        if p.exists():
            out.extend(csv.DictReader(p.open(encoding="utf-8-sig")))
    return out


def analyze(rows: list[dict]) -> dict:
    if not rows:
        return {"count": 0, "ok": False}
    kw_in = sum(1 for r in rows if norm(r["focus_keyword"]).replace(" ", "") in norm(r["title_ko"]).replace(" ", ""))
    short = sum(1 for r in rows if len(r["title_ko"]) < 12)
    long = sum(1 for r in rows if len(r["title_ko"]) > 60)
    models = set(r.get("model", "") for r in rows)
    tiers = {t: sum(1 for r in rows if r.get("tier") == t) for t in ("hi", "lt")}
    return {
        "count": len(rows),
        "kw_in_title_pct": round(100 * kw_in / len(rows), 1),
        "short_titles": short,
        "long_titles": long,
        "avg_title_len": round(sum(len(r["title_ko"]) for r in rows) / len(rows), 1),
        "models": sorted(models),
        "tiers": tiers,
        "samples": [
            {"kw": r["focus_keyword"], "title": r["title_ko"]}
            for r in rows[:3]
        ],
        "ok": len(rows) >= 70 and kw_in / len(rows) >= 0.9 and short == 0,
    }


def run_suite(name: str, target_rows: list[dict], *, batch_size: int = 40, max_batches: int = 2) -> dict:
    reset_runtime()
    set_pending_only(target_rows)
    proc = run_batches(batch_size, max_batches)
    rows = read_output_rows()
    retry_n = sum(1 for _ in RETRY_QUEUE_FILE.open(encoding="utf-8")) if RETRY_QUEUE_FILE.exists() else 0
    metrics = analyze(rows)
    return {
        "name": name,
        "target": len(target_rows),
        "batch_size": batch_size,
        "max_batches": max_batches,
        "exit_code": proc.returncode,
        "stdout_tail": proc.stdout[-800:] if proc.stdout else "",
        "stderr_tail": proc.stderr[-400:] if proc.stderr else "",
        "retry_queue": retry_n,
        **metrics,
    }


def main() -> None:
    rows = load_rows()
    suites = [
        ("L1-02-finance-YMYL", pick_first([r for r in rows if r["l1_code"] == "02"], 80)),
        ("L1-03-health-YMYL", pick_first([r for r in rows if r["l1_code"] == "03"], 80)),
        ("L1-04-fashion", pick_first([r for r in rows if r["l1_code"] == "04"], 80)),
        ("L1-01-entertainment", pick_first([r for r in rows if r["l1_code"] == "01"], 80)),
        ("longtail-lt-L11", pick_first(
            [r for r in rows if r["l1_code"] == "11" and int(r["monthly_total"]) < HITIER_THRESHOLD],
            80,
        )),
    ]

    results = []
    for name, target in suites:
        print(f"\n>>> Running {name} ({len(target)} keywords)...", flush=True)
        results.append(run_suite(name, target))

    print("\n" + "=" * 72)
    print("  Gemini batch-40 validation summary")
    print("=" * 72)
    all_ok = True
    for r in results:
        status = "PASS" if r.get("ok") else "FAIL"
        if not r.get("ok"):
            all_ok = False
        print(
            f"  [{status}] {r['name']}: saved={r['count']}/{r['target']} "
            f"kw_in_title={r.get('kw_in_title_pct', 0)}% retry_q={r['retry_queue']}"
        )
        for s in r.get("samples", []):
            print(f"       · {s['kw']} → {s['title'][:55]}")

    out = ROOT / "outputs" / "validation_batch40_report.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nReport: {out}")
    print("Overall:", "PASS" if all_ok else "FAIL")
    sys.exit(0 if all_ok else 1)


if __name__ == "__main__":
    main()
