#!/usr/bin/env python3
"""data2/ 일회성 정리: 레거시·staging·로그·테스트를 archive/로 이동."""

from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA2 = ROOT / "data2"
ARCHIVE = DATA2 / "archive"

MOVES: list[tuple[Path, Path]] = [
    # legacy nemotron
    (DATA2 / "topics_l3.csv", ARCHIVE / "legacy_nemotron" / "topics_l3.csv"),
    (DATA2 / "topics_l3.json", ARCHIVE / "legacy_nemotron" / "topics_l3.json"),
    (DATA2 / "topics_l3_curated.csv", ARCHIVE / "legacy_nemotron" / "topics_l3_curated.csv"),
    (DATA2 / "topics_l3_dropped.csv", ARCHIVE / "legacy_nemotron" / "topics_l3_dropped.csv"),
    (DATA2 / "topics_l3_hold.csv", ARCHIVE / "legacy_nemotron" / "topics_l3_hold.csv"),
    (DATA2 / "manifest_l2.json", ARCHIVE / "legacy_nemotron" / "manifest_l2.json"),
    (DATA2 / "manifest_l3.json", ARCHIVE / "legacy_nemotron" / "manifest_l3.json"),
    (DATA2 / "state" / "taxonomy.db", ARCHIVE / "legacy_nemotron" / "taxonomy.db"),
    (DATA2 / "reports" / "l3_curate_report.json", ARCHIVE / "legacy_nemotron" / "l3_curate_report.json"),
    # legacy scout
    (DATA2 / "state" / "l3_slot2step_prod_checkpoint.json", ARCHIVE / "legacy_scout" / "l3_slot2step_prod_checkpoint.json"),
    (DATA2 / "state" / "l3_slot2step_topup_checkpoint.json", ARCHIVE / "legacy_scout" / "l3_slot2step_topup_checkpoint.json"),
    (ARCHIVE / "topics_l3_slot2step_dropped.csv", ARCHIVE / "legacy_scout" / "topics_l3_slot2step_dropped.csv"),
    (DATA2 / "reports" / "l3_slot2step_curate_report.json", ARCHIVE / "legacy_scout" / "l3_slot2step_curate_report.json"),
    (DATA2 / "reports" / "l3_slot2step_prod_report.json", ARCHIVE / "legacy_scout" / "l3_slot2step_prod_report.json"),
    # l2 expansion (중간본)
    (DATA2 / "topics_l2_expanded.csv", ARCHIVE / "l2_expansion" / "topics_l2_expanded.csv"),
    (DATA2 / "topics_l2_expanded.json", ARCHIVE / "l2_expansion" / "topics_l2_expanded.json"),
    (DATA2 / "topics_l2.json", ARCHIVE / "l2_expansion" / "topics_l2.json"),
    (DATA2 / "manifest_l2_expansion.json", ARCHIVE / "l2_expansion" / "manifest_l2_expansion.json"),
    (DATA2 / "seed" / "l2_expansion_plan.csv", ARCHIVE / "l2_expansion" / "l2_expansion_plan.csv"),
    (DATA2 / "reports" / "l2_expansion_report.json", ARCHIVE / "l2_expansion" / "l2_expansion_report.json"),
    # root logs
    (DATA2 / "l3_generation.log", ARCHIVE / "logs" / "l3_generation.log"),
    (DATA2 / "l3_run.log", ARCHIVE / "logs" / "l3_run.log"),
]

KEEP_INCOMING = {"generation_batch_plan.csv", "l2_redesign_map.csv"}
KEEP_REPORTS = {"claude_l3_integration_report.json"}


def move_file(src: Path, dst: Path, log: list[dict]) -> None:
    if not src.exists():
        log.append({"action": "skip_missing", "src": str(src.relative_to(ROOT))})
        return
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        log.append({"action": "skip_exists", "src": str(src.relative_to(ROOT)), "dst": str(dst.relative_to(ROOT))})
        return
    shutil.move(str(src), str(dst))
    log.append({"action": "moved", "src": str(src.relative_to(ROOT)), "dst": str(dst.relative_to(ROOT))})


def move_dir_contents(src_dir: Path, dst_dir: Path, log: list[dict]) -> None:
    if not src_dir.exists():
        return
    dst_dir.mkdir(parents=True, exist_ok=True)
    for item in sorted(src_dir.iterdir()):
        dst = dst_dir / item.name
        if dst.exists():
            log.append({"action": "skip_exists", "src": str(item.relative_to(ROOT)), "dst": str(dst.relative_to(ROOT))})
            continue
        shutil.move(str(item), str(dst))
        log.append({"action": "moved", "src": str(item.relative_to(ROOT)), "dst": str(dst.relative_to(ROOT))})


def main() -> None:
    log: list[dict] = []

    for src, dst in MOVES:
        move_file(src, dst, log)

    incoming = DATA2 / "incoming" / "claude_l3"
    incoming_dst = ARCHIVE / "incoming_processed" / "claude_l3"
    if incoming.exists():
        incoming_dst.mkdir(parents=True, exist_ok=True)
        for item in sorted(incoming.iterdir()):
            if item.name in KEEP_INCOMING:
                continue
            dst = incoming_dst / item.name
            if not item.exists():
                continue
            if dst.exists():
                log.append({"action": "skip_exists", "src": str(item.relative_to(ROOT)), "dst": str(dst.relative_to(ROOT))})
                continue
            shutil.move(str(item), str(dst))
            log.append({"action": "moved", "src": str(item.relative_to(ROOT)), "dst": str(dst.relative_to(ROOT))})

    move_dir_contents(DATA2 / "logs", ARCHIVE / "logs", log)
    move_dir_contents(DATA2 / "test", ARCHIVE / "test", log)

    reports = DATA2 / "reports"
    reports_dst = ARCHIVE / "reports"
    if reports.exists():
        reports_dst.mkdir(parents=True, exist_ok=True)
        for item in sorted(reports.iterdir()):
            if item.name in KEEP_REPORTS:
                continue
            dst = reports_dst / item.name
            if dst.exists():
                log.append({"action": "skip_exists", "src": str(item.relative_to(ROOT)), "dst": str(dst.relative_to(ROOT))})
                continue
            shutil.move(str(item), str(dst))
            log.append({"action": "moved", "src": str(item.relative_to(ROOT)), "dst": str(dst.relative_to(ROOT))})

    # backup: 최신 2세트만 유지
    backup = ROOT / "backup"
    if backup.exists():
        backups = sorted(backup.glob("topics_l3_slot2step.*.csv"), reverse=True)
        backup_dst = ARCHIVE / "backups"
        for old in backups[2:]:
            dst = backup_dst / old.name
            dst.parent.mkdir(parents=True, exist_ok=True)
            if not dst.exists():
                shutil.move(str(old), str(dst))
                log.append({"action": "moved", "src": str(old.relative_to(ROOT)), "dst": str(dst.relative_to(ROOT))})

    manifest = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "moves": log,
        "canonical": [
            "data2/seed/topics_l1.csv",
            "data2/topics_l2.csv",
            "data2/topics_l3_slot2step.csv",
            "data2/topics_l3_slot2step.json",
            "data2/manifest_l3_slot2step.json",
            "data2/state/taxonomy_slot2step.db",
            "data2/brief.txt",
        ],
    }
    out = ARCHIVE / "REORG_MANIFEST.json"
    out.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    moved = sum(1 for x in log if x["action"] == "moved")
    skipped = sum(1 for x in log if x["action"].startswith("skip"))
    print(f"정리 완료: 이동 {moved}, 스킵 {skipped}")
    print(f"매니페스트: {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
