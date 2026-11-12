"""End-to-end SCD2 history and run-report reconciliation tests."""

from __future__ import annotations

import pytest

from app import db
from app.db import connect
from app.modules import scd2_history
from app.repositories import canonical as canon_repo
from app.repositories import runs as runs_repo
from app.repositories import sources as sources_repo
from app.seed import init_db
from app.services.ingest_service import dashboard_stats, run_ingest


@pytest.fixture()
def isolated_db(tmp_path, monkeypatch):
    db_path = tmp_path / "scd2_test.db"
    monkeypatch.setattr(db, "DB_PATH", db_path)
    init_db()
    yield db_path


def _source_id(conn, name: str) -> int:
    rows = conn.execute("SELECT id FROM sources WHERE name = ?", (name,)).fetchall()
    assert rows, f"source {name} missing"
    return int(rows[0]["id"])


def test_insert_then_version_chain_and_reconciliation(isolated_db):
    conn = connect()
    try:
        alpha = _source_id(conn, "Alpha Catalog")
        refresh = _source_id(conn, "Alpha Catalog Refresh")

        run1 = run_ingest(alpha, "incremental")
        assert run1["status"] == "ok"
        rep1 = run1["report"]
        assert rep1["inserted_count"] == 3
        assert rep1["version_opened_count"] == 0
        assert rep1["canonical_count"] == 3
        assert rep1["history_versions_added"] == 3
        assert rep1["reconciliation"]["reconciled"] is True

        # Every current row has exactly one open history version v1.
        for key in ("SKU-1001", "SKU-1002", "SKU-1003"):
            versions = scd2_history.list_versions(conn, key)
            assert len(versions) == 1
            assert versions[0]["version_no"] == 1
            assert versions[0]["valid_to"] is None
            assert versions[0]["is_current"] is True

        run2 = run_ingest(refresh, "incremental")
        rep2 = run2["report"]
        # 1001: non-key change -> new version; 1002: same snapshot, rev up ->
        # refresh; 1003: identical snapshot/rev -> stale skip.
        assert rep2["parsed_count"] == 3
        assert rep2["inserted_count"] == 0
        assert rep2["version_opened_count"] == 1
        assert rep2["refreshed_count"] == 1
        assert rep2["skipped_count"] == 1
        assert rep2["upserted_count"] == 2
        assert rep2["conflict_count"] == 0
        assert rep2["history_versions_added"] == 1
        assert rep2["reconciliation"]["reconciled"] is True

        # Current row reflects the new snapshot.
        current = canon_repo.get_by_key(conn, "SKU-1001")
        assert current["title"] == "Oak Desk Lamp Pro"
        assert current["price"] == 139.0
        assert current["version_no"] == 2
        assert current["valid_from"] == "2026-11-05T09:00:00Z"

        # The version chain is closed/open correctly and keeps the old values.
        versions = scd2_history.list_versions(conn, "SKU-1001")
        assert [v["version_no"] for v in versions] == [2, 1]
        v2, v1 = versions
        assert v2["valid_to"] is None
        assert v2["is_current"] is True
        assert v2["valid_from"] == "2026-11-05T09:00:00Z"
        assert v1["valid_to"] == "2026-11-05T09:00:00Z"
        assert v1["valid_from"] == "2026-11-01T10:00:00Z"
        assert v1["title"] == "Oak Desk Lamp"
        assert v1["price"] == 129.0
        assert v1["is_current"] is False

        # Current view still has exactly one row per key and matches open count.
        assert len(canon_repo.list_items(conn)) == 3
        assert canon_repo.count_items(conn) == scd2_history.current_version_count(conn)
        assert scd2_history.total_version_count(conn) == 4  # 3 v1 + one v2

        # Refreshed key keeps a single open version with provenance moved on.
        v1002 = scd2_history.list_versions(conn, "SKU-1002")
        assert len(v1002) == 1
        assert v1002[0]["source_revision"] == 3
        assert v1002[0]["valid_to"] is None
    finally:
        conn.close()


def test_conflict_quarantines_without_history_or_master_change(isolated_db):
    conn = connect()
    try:
        alpha = _source_id(conn, "Alpha Catalog")
        beta = _source_id(conn, "Beta Offers")

        run_ingest(alpha, "incremental")
        before = canon_repo.get_by_key(conn, "SKU-1001")
        versions_before = scd2_history.list_versions(conn, "SKU-1001")

        run = run_ingest(beta, "incremental")
        rep = run["report"]
        assert rep["conflict_count"] == 1
        assert rep["version_opened_count"] == 0
        assert rep["inserted_count"] == 2  # SKU-2001, SKU-2002
        assert rep["reconciliation"]["reconciled"] is True
        assert rep["staging_dispositions"]["quarantine"] == 1

        # Master data untouched by the conflict row.
        after = canon_repo.get_by_key(conn, "SKU-1001")
        assert after["title"] == before["title"]
        assert after["price"] == 129.0
        assert after["source_id"] == alpha
        versions_after = scd2_history.list_versions(conn, "SKU-1001")
        assert versions_after == versions_before  # no fabricated version

        # The conflict really is in the queue with both payloads.
        conflicts = runs_repo.list_conflicts(conn)
        sku_conflicts = [c for c in conflicts if c["business_key"] == "SKU-1001"]
        assert len(sku_conflicts) == 1
        assert sku_conflicts[0]["incoming"]["price"] == 99.0
        assert sku_conflicts[0]["existing"]["price"] == 129.0
    finally:
        conn.close()


def test_history_survives_reconnect_and_dashboard_matches(isolated_db):
    conn = connect()
    try:
        alpha = _source_id(conn, "Alpha Catalog")
        refresh = _source_id(conn, "Alpha Catalog Refresh")
        run_ingest(alpha, "incremental")
        run_ingest(refresh, "incremental")
    finally:
        conn.close()

    # New connection: versions are persisted, not held in memory.
    conn = connect()
    try:
        versions = scd2_history.list_versions(conn, "SKU-1001")
        assert len(versions) == 2
        assert versions[1]["valid_to"] == "2026-11-05T09:00:00Z"
        stats = dashboard_stats()
        assert stats["canonical_count"] == 3
        assert stats["current_version_count"] == stats["canonical_count"]
        assert stats["history_version_total"] == 4
    finally:
        conn.close()


def test_full_mode_reprocesses_feed(isolated_db):
    conn = connect()
    try:
        alpha = _source_id(conn, "Alpha Catalog")
        run_ingest(alpha, "incremental")
        run_full = run_ingest(alpha, "full")
        rep = run_full["report"]
        # Same snapshots, same revisions -> all stale, no extra versions.
        assert rep["parsed_count"] == 3
        assert rep["inserted_count"] == 0
        assert rep["version_opened_count"] == 0
        assert rep["skipped_count"] == 3
        assert scd2_history.total_version_count(conn) == 3
        assert sources_repo.get_source(conn, alpha)["watermark_ts"] == rep["watermark_ts"]
    finally:
        conn.close()


def test_repeated_incremental_is_watermark_skipped(isolated_db):
    conn = connect()
    try:
        refresh = _source_id(conn, "Alpha Catalog Refresh")
        alpha = _source_id(conn, "Alpha Catalog")
        run_ingest(alpha, "incremental")
        run_ingest(refresh, "incremental")
        versions_before = scd2_history.total_version_count(conn)

        rerun = run_ingest(refresh, "incremental")
        rep = rerun["report"]
        assert rep["parsed_count"] == 3
        assert rep["upserted_count"] == 0
        assert rep["conflict_count"] == 0
        assert rep["skipped_count"] == 3
        assert rep["staging_dispositions"]["skipped_watermark"] == 3
        assert rep["reconciliation"]["reconciled"] is True
        # Nothing accepted -> not a single extra version row.
        assert scd2_history.total_version_count(conn) == versions_before
    finally:
        conn.close()


def test_repeated_evolution_closes_chain_in_order(isolated_db):
    conn = connect()
    try:
        alpha = _source_id(conn, "Alpha Catalog")
        run_ingest(alpha, "incremental")
        base = {
            "business_key": "SKU-1001",
            "title": "Oak Desk Lamp Pro",
            "price": 139.0,
            "category": "lighting",
            "event_ts": "2026-11-05T09:00:00Z",
            "source_revision": 5,
            "payload": {},
        }
        v2 = scd2_history.evolve_version(conn, item=base, source_id=alpha, run_id=99)
        third = {
            **base,
            "title": "Oak Desk Lamp Max",
            "price": 149.0,
            "event_ts": "2026-11-10T09:00:00Z",
            "source_revision": 6,
        }
        v3 = scd2_history.evolve_version(conn, item=third, source_id=alpha, run_id=100)
        assert (v2, v3) == (2, 3)

        versions = scd2_history.list_versions(conn, "SKU-1001")
        assert [v["version_no"] for v in versions] == [3, 2, 1]
        assert versions[0]["valid_to"] is None
        assert versions[1]["valid_to"] == "2026-11-10T09:00:00Z"
        assert versions[2]["valid_to"] == "2026-11-05T09:00:00Z"
        # Exactly one open version per key, regardless of chain length.
        open_rows = conn.execute(
            "SELECT COUNT(*) c FROM canonical_history WHERE business_key=? AND valid_to IS NULL",
            ("SKU-1001",),
        ).fetchone()["c"]
        assert open_rows == 1
    finally:
        conn.close()
