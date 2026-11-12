from app.db import connect
from app.modules import scd2_history


def _ensure_column(conn, table: str, column: str, ddl_type: str) -> None:
    cols = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl_type}")


def init_db():
    conn = connect()
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS settings(
            key TEXT PRIMARY KEY,
            value TEXT
        );
        CREATE TABLE IF NOT EXISTS sources(
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            format TEXT NOT NULL,
            fixture_path TEXT NOT NULL,
            watermark_ts TEXT,
            lookback_seconds INTEGER NOT NULL DEFAULT 0,
            enabled INTEGER NOT NULL DEFAULT 1,
            note TEXT
        );
        CREATE TABLE IF NOT EXISTS runs(
            id INTEGER PRIMARY KEY,
            source_id INTEGER NOT NULL,
            mode TEXT NOT NULL,
            status TEXT NOT NULL,
            parsed_count INTEGER NOT NULL,
            upserted_count INTEGER NOT NULL,
            conflict_count INTEGER NOT NULL,
            skipped_count INTEGER NOT NULL,
            report_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS staging_rows(
            id INTEGER PRIMARY KEY,
            run_id INTEGER NOT NULL,
            source_id INTEGER NOT NULL,
            business_key TEXT NOT NULL,
            event_ts TEXT NOT NULL,
            source_revision INTEGER NOT NULL,
            payload_json TEXT NOT NULL,
            disposition TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS canonical_items(
            id INTEGER PRIMARY KEY,
            business_key TEXT NOT NULL UNIQUE,
            title TEXT NOT NULL,
            price REAL NOT NULL,
            category TEXT NOT NULL,
            source_id INTEGER NOT NULL,
            event_ts TEXT NOT NULL,
            source_revision INTEGER NOT NULL,
            payload_json TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS conflicts(
            id INTEGER PRIMARY KEY,
            run_id INTEGER NOT NULL,
            source_id INTEGER NOT NULL,
            business_key TEXT NOT NULL,
            existing_payload TEXT NOT NULL,
            incoming_payload TEXT NOT NULL,
            reason TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        """
    )
    # SCD2: authority group on sources + canonical_history version table.
    _ensure_column(conn, "sources", "authority_group", "TEXT")
    scd2_history.init_scd2(conn)

    if conn.execute("SELECT COUNT(*) c FROM sources").fetchone()["c"] == 0:
        conn.executemany(
            """
            INSERT INTO sources(name, format, fixture_path, watermark_ts, lookback_seconds,
                                enabled, note, authority_group)
            VALUES (?,?,?,?,?,?,?,?)
            """,
            [
                (
                    "Alpha Catalog",
                    "json",
                    "alpha_catalog.json",
                    None,
                    0,
                    1,
                    "clean：三件独立货号，首跑应全部 insert",
                    "alpha",
                ),
                (
                    "Beta Offers",
                    "jsonl",
                    "beta_offers.jsonl",
                    None,
                    0,
                    1,
                    "dirty：SKU-1001 与 Alpha 同键异价→冲突隔离；另含独立货号",
                    "beta",
                ),
                (
                    "Gamma Shelf",
                    "xml",
                    "gamma_shelf.xml",
                    None,
                    0,
                    1,
                    "xml 源：两件独立货号",
                    "gamma",
                ),
                (
                    "Alpha Catalog Refresh",
                    "json",
                    "alpha_catalog_refresh.json",
                    None,
                    0,
                    1,
                    "Alpha 同组刷新：非键变化开 SCD2 新版本，同快照高 revision 刷新，其余跳过",
                    "alpha",
                ),
            ],
        )
        conn.execute("INSERT INTO settings(key, value) VALUES ('project_label', 'Feedstitch')")
        conn.execute("INSERT INTO settings(key, value) VALUES ('default_mode', 'incremental')")
        conn.commit()
    else:
        # Existing local DBs: Alpha keeps ownership of its keys so its refresh
        # feed may evolve them; the demo conflict source stays in another group.
        conn.execute(
            "UPDATE sources SET authority_group = 'alpha' WHERE name = 'Alpha Catalog' "
            "AND authority_group IS NULL"
        )
        conn.execute(
            "UPDATE sources SET authority_group = 'beta' WHERE name = 'Beta Offers' "
            "AND authority_group IS NULL"
        )
        conn.execute(
            "UPDATE sources SET authority_group = 'gamma' WHERE name = 'Gamma Shelf' "
            "AND authority_group IS NULL"
        )
        builtin_sources = [
            (
                "Beta Offers",
                "jsonl",
                "beta_offers.jsonl",
                "dirty：SKU-1001 与 Alpha 同键异价→冲突隔离；另含独立货号",
                "beta",
            ),
            (
                "Gamma Shelf",
                "xml",
                "gamma_shelf.xml",
                "xml 源：两件独立货号",
                "gamma",
            ),
            (
                "Alpha Catalog Refresh",
                "json",
                "alpha_catalog_refresh.json",
                "Alpha 同组刷新：非键变化开 SCD2 新版本，同快照高 revision 刷新，其余跳过",
                "alpha",
            ),
        ]
        for name, fmt, fixture, note, group in builtin_sources:
            exists = conn.execute(
                "SELECT 1 FROM sources WHERE fixture_path = ?", (fixture,)
            ).fetchone()
            if not exists:
                conn.execute(
                    """
                    INSERT INTO sources(name, format, fixture_path, watermark_ts, lookback_seconds,
                                        enabled, note, authority_group)
                    VALUES (?,?,?,?,?,?,?,?)
                    """,
                    (name, fmt, fixture, None, 0, 1, note, group),
                )
        conn.commit()
    conn.close()
