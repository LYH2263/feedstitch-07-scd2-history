from __future__ import annotations

import json
import sqlite3
from typing import Any


_CURRENT_SELECT = """
    SELECT c.id, c.business_key, c.title, c.price, c.category, c.source_id,
           c.event_ts, c.source_revision, c.payload_json, c.updated_at,
           h.version_no, h.valid_from
    FROM canonical_items c
    JOIN canonical_history h
      ON h.business_key = c.business_key AND h.valid_to IS NULL
"""


def _decode(row: sqlite3.Row) -> dict[str, Any]:
    d = dict(row)
    d["payload"] = json.loads(d.pop("payload_json"))
    return d


def list_items(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Current effective rows only — one per business key."""
    rows = conn.execute(
        _CURRENT_SELECT + " ORDER BY c.business_key"
    ).fetchall()
    return [_decode(r) for r in rows]


def get_by_key(conn: sqlite3.Connection, business_key: str) -> dict[str, Any] | None:
    row = conn.execute(
        _CURRENT_SELECT + " WHERE c.business_key = ?",
        (business_key,),
    ).fetchone()
    if not row:
        return None
    return _decode(row)


def insert_item(conn: sqlite3.Connection, item: dict[str, Any], source_id: int) -> None:
    conn.execute(
        """
        INSERT INTO canonical_items(
            business_key, title, price, category, source_id,
            event_ts, source_revision, payload_json, updated_at
        ) VALUES (?,?,?,?,?,?,?,?,datetime('now'))
        """,
        (
            item["business_key"],
            item["title"],
            item["price"],
            item["category"],
            source_id,
            item["event_ts"],
            item["source_revision"],
            json.dumps(item["payload"], ensure_ascii=False),
        ),
    )


def update_item(conn: sqlite3.Connection, item: dict[str, Any], source_id: int) -> None:
    """Overwrite the *current* row of a key.

    Only called together with an SCD2 version transition (the previous
    snapshot is already preserved in canonical_history), never on its own.
    """
    conn.execute(
        """
        UPDATE canonical_items SET
            title=?, price=?, category=?, source_id=?, event_ts=?,
            source_revision=?, payload_json=?, updated_at=datetime('now')
        WHERE business_key=?
        """,
        (
            item["title"],
            item["price"],
            item["category"],
            source_id,
            item["event_ts"],
            item["source_revision"],
            json.dumps(item["payload"], ensure_ascii=False),
            item["business_key"],
        ),
    )


def count_items(conn: sqlite3.Connection) -> int:
    return int(conn.execute("SELECT COUNT(*) c FROM canonical_items").fetchone()["c"])
