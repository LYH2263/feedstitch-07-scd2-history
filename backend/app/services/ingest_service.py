from __future__ import annotations

from typing import Any

from app.config import FIXTURES_DIR
from app.db import connect
from app.engines.merge_rules import can_version_key, decide_merge, eligible_for_incremental
from app.engines.parse_feed import parse_fixture
from app.modules import scd2_history
from app.repositories import canonical as canon_repo
from app.repositories import runs as runs_repo
from app.repositories import sources as sources_repo


def preview_source(source_id: int) -> dict[str, Any]:
    conn = connect()
    try:
        source = sources_repo.get_source(conn, source_id)
        if not source:
            raise ValueError("source not found")
        path = FIXTURES_DIR / source["fixture_path"]
        items = parse_fixture(path, source["format"])
        return {
            "source": source,
            "count": len(items),
            "items": items[:50],
        }
    finally:
        conn.close()


def _current_view(existing: dict[str, Any] | None) -> dict[str, Any] | None:
    if not existing:
        return None
    return {
        "business_key": existing["business_key"],
        "title": existing["title"],
        "price": existing["price"],
        "category": existing["category"],
        "event_ts": existing["event_ts"],
        "source_revision": existing["source_revision"],
        "source_id": existing["source_id"],
        "payload": existing["payload"],
    }


def run_ingest(source_id: int, mode: str = "incremental") -> dict[str, Any]:
    if mode not in ("incremental", "full"):
        raise ValueError("mode must be incremental or full")

    conn = connect()
    try:
        source = sources_repo.get_source(conn, source_id)
        if not source:
            raise ValueError("source not found")
        if not source["enabled"]:
            raise ValueError("source disabled")

        if mode == "full":
            sources_repo.set_watermark(conn, source_id, None)
            source["watermark_ts"] = None

        run_id = runs_repo.create_run(conn, source_id, mode)
        path = FIXTURES_DIR / source["fixture_path"]

        try:
            items = parse_fixture(path, source["format"])
        except Exception as exc:  # noqa: BLE001
            runs_repo.finish_run(
                conn,
                run_id,
                status="failed",
                parsed=0,
                upserted=0,
                conflicts=0,
                skipped=0,
                report={"error": str(exc)},
            )
            conn.commit()
            raise

        parsed = 0
        inserted = 0
        version_opened = 0
        refreshed = 0
        conflicts = 0
        skipped = 0
        max_event_ts: str | None = source.get("watermark_ts")
        dispositions: list[str] = []
        authority_group = source.get("authority_group")

        for item in items:
            parsed += 1
            if mode == "incremental" and not eligible_for_incremental(
                item["event_ts"],
                source.get("watermark_ts"),
                int(source.get("lookback_seconds") or 0),
            ):
                runs_repo.insert_staging(
                    conn,
                    run_id=run_id,
                    source_id=source_id,
                    item=item,
                    disposition="skipped_watermark",
                )
                skipped += 1
                dispositions.append("skipped_watermark")
                continue

            existing = canon_repo.get_by_key(conn, item["business_key"])
            existing_view = _current_view(existing)
            existing_group = (
                sources_repo.get_authority_group(conn, existing["source_id"])
                if existing
                else None
            )
            versioning_allowed = can_version_key(
                existing_view,
                incoming_source_id=source_id,
                incoming_authority_group=authority_group,
                existing_authority_group=existing_group,
            )
            decision = decide_merge(existing_view, item, versioning_allowed)

            if decision == "insert":
                # First sighting: current row + open history version v1.
                canon_repo.insert_item(conn, item, source_id)
                scd2_history.open_version(
                    conn, item=item, source_id=source_id, run_id=run_id
                )
                runs_repo.insert_staging(
                    conn,
                    run_id=run_id,
                    source_id=source_id,
                    item=item,
                    disposition="insert",
                )
                inserted += 1
                dispositions.append("insert")
                if max_event_ts is None or item["event_ts"] > max_event_ts:
                    max_event_ts = item["event_ts"]

            elif decision == "version_update":
                # Eligible non-key change: close the old version, open a new
                # one, then move the current row forward — never overwrite-only.
                new_version = scd2_history.evolve_version(
                    conn, item=item, source_id=source_id, run_id=run_id
                )
                canon_repo.update_item(conn, item, source_id)
                runs_repo.insert_staging(
                    conn,
                    run_id=run_id,
                    source_id=source_id,
                    item=item,
                    disposition=f"version_update_v{new_version}",
                )
                version_opened += 1
                dispositions.append(f"version_update_v{new_version}")
                if max_event_ts is None or item["event_ts"] > max_event_ts:
                    max_event_ts = item["event_ts"]

            elif decision == "upsert":
                # Same non-key snapshot, higher revision: current row and the
                # already-open version move together; no new version is made.
                canon_repo.update_item(conn, item, source_id)
                scd2_history.refresh_open_version(
                    conn, item=item, source_id=source_id, run_id=run_id
                )
                runs_repo.insert_staging(
                    conn,
                    run_id=run_id,
                    source_id=source_id,
                    item=item,
                    disposition="upsert_refresh",
                )
                refreshed += 1
                dispositions.append("upsert_refresh")
                if max_event_ts is None or item["event_ts"] > max_event_ts:
                    max_event_ts = item["event_ts"]

            elif decision == "conflict":
                # Quarantine only: master data and its history stay untouched,
                # no fabricated history version.
                runs_repo.insert_conflict(
                    conn,
                    run_id=run_id,
                    source_id=source_id,
                    business_key=item["business_key"],
                    existing_payload=existing_view["payload"] if existing_view else {},
                    incoming_payload=item["payload"],
                    reason="non_key_field_mismatch",
                )
                runs_repo.insert_staging(
                    conn,
                    run_id=run_id,
                    source_id=source_id,
                    item=item,
                    disposition="quarantine",
                )
                conflicts += 1
                dispositions.append("quarantine")
                if max_event_ts is None or item["event_ts"] > max_event_ts:
                    max_event_ts = item["event_ts"]

            else:  # skip_stale
                runs_repo.insert_staging(
                    conn,
                    run_id=run_id,
                    source_id=source_id,
                    item=item,
                    disposition="skip_stale",
                )
                skipped += 1
                dispositions.append("skip_stale")
                if max_event_ts is None or item["event_ts"] > max_event_ts:
                    max_event_ts = item["event_ts"]

        if max_event_ts:
            sources_repo.set_watermark(conn, source_id, max_event_ts)

        upserted = inserted + version_opened + refreshed
        canonical_count = canon_repo.count_items(conn)
        current_versions = scd2_history.current_version_count(conn)
        total_versions = scd2_history.total_version_count(conn)
        run_versions = scd2_history.versions_opened_in_run(conn, run_id)
        staging_counts = runs_repo.staging_disposition_counts(conn, run_id)
        run_conflicts = runs_repo.conflicts_for_run(conn, run_id)

        # Same-source reconciliation: every counter below is re-counted from
        # the actual tables instead of trusting the in-loop accumulators.
        accepted_staging = sum(
            c for d, c in staging_counts.items()
            if d in ("insert", "upsert_refresh") or d.startswith("version_update")
        )
        skipped_staging = sum(
            c for d, c in staging_counts.items() if d.startswith("skip")
        )
        checks = {
            "parsed_matches_staging": sum(staging_counts.values()) == parsed,
            "accepted_matches_staging": accepted_staging == upserted,
            "conflicts_match_queue": run_conflicts == conflicts,
            "quarantine_matches_conflicts": staging_counts.get("quarantine", 0) == conflicts,
            "skipped_matches_staging": skipped_staging == skipped,
            "canonical_matches_open_versions": canonical_count == current_versions,
            "run_versions_match_writes": run_versions == inserted + version_opened,
        }
        checks["reconciled"] = all(checks.values())

        report = {
            "source_id": source_id,
            "mode": mode,
            "parsed_count": parsed,
            "upserted_count": upserted,
            "inserted_count": inserted,
            "version_opened_count": version_opened,
            "refreshed_count": refreshed,
            "conflict_count": conflicts,
            "skipped_count": skipped,
            "canonical_count": canonical_count,
            "current_version_count": current_versions,
            "history_version_total": total_versions,
            "history_versions_added": run_versions,
            "staging_dispositions": staging_counts,
            "reconciliation": checks,
            "watermark_ts": max_event_ts,
            "dispositions": dispositions,
        }
        runs_repo.finish_run(
            conn,
            run_id,
            status="ok",
            parsed=parsed,
            upserted=upserted,
            conflicts=conflicts,
            skipped=skipped,
            report=report,
        )
        conn.commit()
        run = runs_repo.get_run(conn, run_id)
        return run or report
    finally:
        conn.close()


def dashboard_stats() -> dict[str, Any]:
    conn = connect()
    try:
        sources = sources_repo.list_sources(conn)
        return {
            "source_count": len(sources),
            "enabled_sources": sum(1 for s in sources if s["enabled"]),
            "canonical_count": canon_repo.count_items(conn),
            "current_version_count": scd2_history.current_version_count(conn),
            "history_version_total": scd2_history.total_version_count(conn),
            "conflict_count": conn.execute("SELECT COUNT(*) c FROM conflicts").fetchone()["c"],
            "run_count": conn.execute("SELECT COUNT(*) c FROM runs").fetchone()["c"],
            "staging_count": conn.execute("SELECT COUNT(*) c FROM staging_rows").fetchone()["c"],
        }
    finally:
        conn.close()
