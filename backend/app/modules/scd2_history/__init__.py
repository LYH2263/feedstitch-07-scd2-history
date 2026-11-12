"""Type-2 slowly changing dimension for canonical master data.

The canonical master data keeps one *current* row per business key
(``canonical_items``). Every accepted write — the first insert and every
eligible non-key change — is additionally recorded as an immutable version
row in ``canonical_history``:

- the first write opens version 1 with ``valid_from`` = the item's
  ``event_ts`` and ``valid_to`` NULL (open / currently effective);
- when a later write is allowed to evolve the key and its non-key fields
  diverge, the open version is closed (``valid_to`` = the new event_ts) and
  a new open version is inserted — never an in-place overwrite;
- conflicted rows never reach this table: they stay in the conflict queue
  and must not fabricate a history version.

Version intervals are half-open ``[valid_from, valid_to)``; the row whose
``valid_to`` IS NULL is the currently effective version.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

SCD2_DDL = """
CREATE TABLE IF NOT EXISTS canonical_history(
    id INTEGER PRIMARY KEY,
    business_key TEXT NOT NULL,
    version_no INTEGER NOT NULL,
    title TEXT NOT NULL,
    price REAL NOT NULL,
    category TEXT NOT NULL,
    source_id INTEGER NOT NULL,
    run_id INTEGER NOT NULL,
    event_ts TEXT NOT NULL,
    source_revision INTEGER NOT NULL,
    valid_from TEXT NOT NULL,
    valid_to TEXT,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(business_key, version_no)
);
CREATE INDEX IF NOT EXISTS idx_scd2_key ON canonical_history(business_key, version_no);
CREATE INDEX IF NOT EXISTS idx_scd2_open ON canonical_history(business_key) WHERE valid_to IS NULL;
"""


def init_scd2(conn: sqlite3.Connection) -> None:
    """Create the history table and backfill one open version per current row."""
    conn.executescript(SCD2_DDL)
    backfilled = conn.execute(
        """
        SELECT COUNT(*) AS c
        FROM canonical_items c
        WHERE NOT EXISTS (
            SELECT 1 FROM canonical_history h WHERE h.business_key = c.business_key
        )
        """
    ).fetchone()["c"]
    if backfilled:
        conn.execute(
            """
            INSERT INTO canonical_history(
                business_key, version_no, title, price, category, source_id,
                run_id, event_ts, source_revision, valid_from, valid_to,
                payload_json, created_at
            )
            SELECT business_key, 1, title, price, category, source_id,
                   0, event_ts, source_revision, event_ts, NULL,
                   payload_json, updated_at
            FROM canonical_items c
            WHERE NOT EXISTS (
                SELECT 1 FROM canonical_history h WHERE h.business_key = c.business_key
            )
            """
        )


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    d = dict(row)
    d["payload"] = json.loads(d.pop("payload_json"))
    d["is_current"] = d["valid_to"] is None
    return d


def open_version(
    conn: sqlite3.Connection,
    *,
    item: dict[str, Any],
    source_id: int,
    run_id: int,
) -> int:
    """Insert a new open version for the key and return its version number."""
    next_no = conn.execute(
        "SELECT COALESCE(MAX(version_no), 0) + 1 AS v FROM canonical_history WHERE business_key = ?",
        (item["business_key"],),
    ).fetchone()["v"]
    conn.execute(
        """
        INSERT INTO canonical_history(
            business_key, version_no, title, price, category, source_id,
            run_id, event_ts, source_revision, valid_from, valid_to,
            payload_json, created_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,NULL,?,datetime('now'))
        """,
        (
            item["business_key"],
            next_no,
            item["title"],
            item["price"],
            item["category"],
            source_id,
            run_id,
            item["event_ts"],
            item["source_revision"],
            item["event_ts"],
            json.dumps(item["payload"], ensure_ascii=False),
        ),
    )
    return int(next_no)


def close_open_version(
    conn: sqlite3.Connection,
    business_key: str,
    valid_to: str,
) -> None:
    """Close the currently open version at ``valid_to`` (half-open interval)."""
    conn.execute(
        """
        UPDATE canonical_history
        SET valid_to = ?
        WHERE business_key = ? AND valid_to IS NULL
        """,
        (valid_to, business_key),
    )


def evolve_version(
    conn: sqlite3.Connection,
    *,
    item: dict[str, Any],
    source_id: int,
    run_id: int,
) -> int:
    """Close the open version and open the next one. Returns the new version."""
    close_open_version(conn, item["business_key"], item["event_ts"])
    return open_version(conn, item=item, source_id=source_id, run_id=run_id)


def refresh_open_version(
    conn: sqlite3.Connection,
    *,
    item: dict[str, Any],
    source_id: int,
    run_id: int,
) -> None:
    """Record a same-snapshot, higher-revision write against the open version.

    Non-key fields are unchanged, so the validity interval is not closed and
    no new version row is made: only the mutable provenance columns move
    forward. ``run_id`` stays the run that *opened* the version (so
    per-run version counts stay exact); the refresh itself is traceable via
    the ``upsert_refresh`` staging row and the canonical current row.
    """
    del run_id  # intentionally not persisted: opener run is immutable
    conn.execute(
        """
        UPDATE canonical_history
        SET source_id = ?, event_ts = ?, source_revision = ?, payload_json = ?
        WHERE business_key = ? AND valid_to IS NULL
        """,
        (
            source_id,
            item["event_ts"],
            item["source_revision"],
            json.dumps(item["payload"], ensure_ascii=False),
            item["business_key"],
        ),
    )


def list_versions(
    conn: sqlite3.Connection,
    business_key: str,
) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT id, business_key, version_no, title, price, category, source_id,
               run_id, event_ts, source_revision, valid_from, valid_to,
               payload_json, created_at
        FROM canonical_history
        WHERE business_key = ?
        ORDER BY version_no DESC
        """,
        (business_key,),
    ).fetchall()
    return [_row_to_dict(r) for r in rows]


def current_version_count(conn: sqlite3.Connection) -> int:
    """Number of open versions — must equal the canonical current-row count."""
    return int(
        conn.execute(
            "SELECT COUNT(*) AS c FROM canonical_history WHERE valid_to IS NULL"
        ).fetchone()["c"]
    )


def total_version_count(conn: sqlite3.Connection) -> int:
    return int(conn.execute("SELECT COUNT(*) AS c FROM canonical_history").fetchone()["c"])


def versions_opened_in_run(conn: sqlite3.Connection, run_id: int) -> int:
    """All history rows created by a run, including its first inserts."""
    return int(
        conn.execute(
            "SELECT COUNT(*) AS c FROM canonical_history WHERE run_id = ?",
            (run_id,),
        ).fetchone()["c"]
    )
