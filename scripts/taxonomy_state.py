"""
data2 taxonomy 생성용 SQLite checkpoint.

L2/L3 대량 생성 중 API 한도·중단 시 --resume 으로 이어갈 수 있게 상태를 저장한다.
"""

from __future__ import annotations

import csv
import json
import sqlite3
from collections import Counter
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

L3_FIELDNAMES = [
    "l3_id", "l2_id", "l1_code", "l3_slug", "focus_keyword", "title_ko",
    "search_intent", "topic_angle", "description", "model", "generated_at",
]

_SCHEMA = """
CREATE TABLE IF NOT EXISTS l3_topics (
    l3_id TEXT PRIMARY KEY,
    l2_id TEXT NOT NULL,
    l1_code TEXT NOT NULL,
    l3_slug TEXT NOT NULL,
    focus_keyword TEXT NOT NULL,
    title_ko TEXT NOT NULL,
    search_intent TEXT NOT NULL,
    topic_angle TEXT NOT NULL,
    description TEXT NOT NULL,
    model TEXT NOT NULL,
    generated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_l3_l2 ON l3_topics(l2_id);

CREATE TABLE IF NOT EXISTS l2_progress (
    l2_id TEXT PRIMARY KEY,
    status TEXT NOT NULL DEFAULT 'pending',
    target_count INTEGER NOT NULL,
    current_count INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS run_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def connect(db_path: Path) -> Iterator[sqlite3.Connection]:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        conn.executescript(_SCHEMA)
        conn.commit()
        yield conn
    finally:
        conn.close()


def init_db(db_path: Path) -> None:
    with connect(db_path):
        pass


def wipe_db(db_path: Path) -> None:
    if db_path.exists():
        db_path.unlink()
    init_db(db_path)


def set_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO run_meta(key, value) VALUES(?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )


def get_meta(conn: sqlite3.Connection, key: str, default: str = "") -> str:
    row = conn.execute("SELECT value FROM run_meta WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def sync_from_csv(conn: sqlite3.Connection, csv_path: Path, *, target_count: int = 50) -> int:
    """CSV → SQLite upsert. DB가 비어 있거나 CSV가 더 많을 때 진행분 반영."""
    if not csv_path.exists():
        return 0
    rows: list[dict[str, str]] = []
    with csv_path.open(encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            rows.append({k: row.get(k, "") for k in L3_FIELDNAMES})
    if not rows:
        return 0
    before = conn.execute("SELECT COUNT(*) AS c FROM l3_topics").fetchone()["c"]
    upsert_topics(conn, rows)
    _sync_l2_progress(conn, target_count=target_count)
    after = conn.execute("SELECT COUNT(*) AS c FROM l3_topics").fetchone()["c"]
    return after - before if before else after


def import_csv(conn: sqlite3.Connection, csv_path: Path) -> int:
    return sync_from_csv(conn, csv_path)


def load_all_topics(conn: sqlite3.Connection) -> list[dict[str, str]]:
    cur = conn.execute(
        f"SELECT {', '.join(L3_FIELDNAMES)} FROM l3_topics ORDER BY l2_id, l3_id"
    )
    return [{k: str(row[k]) for k in L3_FIELDNAMES} for row in cur.fetchall()]


def count_by_l2(conn: sqlite3.Connection) -> Counter[str]:
    cur = conn.execute("SELECT l2_id, COUNT(*) AS c FROM l3_topics GROUP BY l2_id")
    return Counter({row["l2_id"]: row["c"] for row in cur.fetchall()})


def upsert_topics(conn: sqlite3.Connection, rows: list[dict[str, str]]) -> None:
    if not rows:
        return
    placeholders = ", ".join("?" for _ in L3_FIELDNAMES)
    cols = ", ".join(L3_FIELDNAMES)
    conn.executemany(
        f"INSERT INTO l3_topics({cols}) VALUES({placeholders}) "
        f"ON CONFLICT(l3_id) DO UPDATE SET "
        + ", ".join(f"{f}=excluded.{f}" for f in L3_FIELDNAMES if f != "l3_id"),
        [[row.get(f, "") for f in L3_FIELDNAMES] for row in rows],
    )
    conn.commit()


def update_l2_progress(
    conn: sqlite3.Connection,
    l2_id: str,
    *,
    target_count: int,
    current_count: int,
    status: str,
    last_error: str | None = None,
) -> None:
    conn.execute(
        """
        INSERT INTO l2_progress(l2_id, status, target_count, current_count, last_error, updated_at)
        VALUES(?, ?, ?, ?, ?, ?)
        ON CONFLICT(l2_id) DO UPDATE SET
            status=excluded.status,
            target_count=excluded.target_count,
            current_count=excluded.current_count,
            last_error=excluded.last_error,
            updated_at=excluded.updated_at
        """,
        (l2_id, status, target_count, current_count, last_error, _now()),
    )
    conn.commit()


def _sync_l2_progress(conn: sqlite3.Connection, *, target_count: int = 50) -> None:
    counts = count_by_l2(conn)
    for l2_id, current in counts.items():
        if current >= target_count:
            status = "done"
        elif current > 0:
            status = "partial"
        else:
            status = "pending"
        update_l2_progress(
            conn, l2_id, target_count=target_count, current_count=current, status=status
        )


def export_artifacts(
    conn: sqlite3.Connection,
    *,
    csv_path: Path,
    json_path: Path,
    manifest_path: Path,
    l2_count: int,
    count_target: int,
    model: str,
    l2_processed: int,
    interrupted: bool = False,
    last_error: str | None = None,
    state_db: str = "",
) -> None:
    rows = load_all_topics(conn)
    now = _now()

    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with csv_path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=L3_FIELDNAMES)
        w.writeheader()
        w.writerows(rows)

    json_path.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")

    counts = count_by_l2(conn)
    manifest = {
        "generated_at": now,
        "model": model,
        "l2_count": l2_count,
        "l3_per_l2_target": count_target,
        "l3_total": len(rows),
        "l2_processed": l2_processed,
        "l2_done": sum(1 for c in counts.values() if c >= count_target),
        "l2_partial": sum(1 for c in counts.values() if 0 < c < count_target),
        "interrupted": interrupted,
        "last_error": last_error,
        "state_db": state_db,
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    set_meta(conn, "last_export_at", now)
    set_meta(conn, "interrupted", "1" if interrupted else "0")
    if last_error:
        set_meta(conn, "last_error", last_error)
    conn.commit()
