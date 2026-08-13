"""DuckDB schema creation shared by SPUF ingestion and empty-table bootstrap."""

from __future__ import annotations

from medicare_navigator.storage.connection import DuckDBConnection


def create_tables(conn, *, drop_existing: bool = True) -> None:
    if drop_existing:
        for table in (
            "beneficiary_cost",
            "insulin_beneficiary_cost",
            "plans",
            "basic_drugs_formulary",
            "query_log",
            "usage_hourly",
            "pricing",
        ):
            conn.execute(f"DROP TABLE IF EXISTS {table}")

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS plans (
            plan_key VARCHAR PRIMARY KEY, contract_id VARCHAR, plan_id VARCHAR,
            plan_name VARCHAR, plan_type VARCHAR, state VARCHAR,
            deductible DOUBLE, contract_year INTEGER, formulary_id VARCHAR,
            plan_suppressed BOOLEAN DEFAULT FALSE
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS basic_drugs_formulary (
            formulary_id VARCHAR, ndc VARCHAR, rxcui VARCHAR, tier INTEGER,
            quantity_limit_yn BOOLEAN, quantity_limit_amount DOUBLE, quantity_limit_days INTEGER,
            prior_authorization_yn BOOLEAN, step_therapy_yn BOOLEAN, as_of_date VARCHAR
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS beneficiary_cost (
            plan_key VARCHAR, tier INTEGER, coverage_level INTEGER,
            days_supply_code INTEGER, pharmacy_channel VARCHAR,
            cost_type VARCHAR, copay DOUBLE, coinsurance_pct DOUBLE,
            cost_max DOUBLE, ded_applies_yn BOOLEAN, as_of_date VARCHAR
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS pricing (
            plan_key VARCHAR, ndc VARCHAR, days_supply INTEGER, unit_cost DOUBLE
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS insulin_beneficiary_cost (
            plan_key VARCHAR, segment_id VARCHAR, tier INTEGER,
            days_supply_code INTEGER, pharmacy_channel VARCHAR,
            copay DOUBLE, as_of_date VARCHAR
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS query_log (
            query_id VARCHAR, session_id VARCHAR, tools_invoked VARCHAR,
            statuses VARCHAR, latency_ms DOUBLE,
            created_at TIMESTAMP DEFAULT current_timestamp
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS usage_hourly (
            hour_bucket TIMESTAMP, region VARCHAR, mode VARCHAR, model VARCHAR,
            sessions_new INTEGER DEFAULT 0,
            requests_total INTEGER DEFAULT 0,
            requests_ok INTEGER DEFAULT 0,
            requests_error INTEGER DEFAULT 0,
            requests_clarification INTEGER DEFAULT 0,
            requests_not_found INTEGER DEFAULT 0,
            requests_limit_reached INTEGER DEFAULT 0,
            prompt_len_short INTEGER DEFAULT 0,
            prompt_len_medium INTEGER DEFAULT 0,
            prompt_len_long INTEGER DEFAULT 0,
            prompt_len_sum INTEGER DEFAULT 0,
            latency_ms_sum DOUBLE DEFAULT 0,
            tokens_in_sum INTEGER DEFAULT 0,
            tokens_out_sum INTEGER DEFAULT 0,
            requests_with_tokens INTEGER DEFAULT 0,
            cost_usd_sum DOUBLE DEFAULT 0,
            PRIMARY KEY (hour_bucket, region, mode, model)
        )
        """
    )


SPUF_INDEX_NAMES = (
    "idx_basic_drugs_formulary",
    "idx_plans_state_year",
    "idx_beneficiary_cost_lookup",
    "idx_pricing_plan_ndc",
    "idx_insulin_beneficiary_cost",
)

# Additive migrations for DuckDB files created before a column was introduced.
# CREATE TABLE IF NOT EXISTS does not alter existing tables on persistent disks.
SCHEMA_MIGRATIONS: tuple[tuple[str, str, str], ...] = (
    ("plans", "plan_suppressed", "BOOLEAN DEFAULT FALSE"),
    ("beneficiary_cost", "ded_applies_yn", "BOOLEAN"),
    ("beneficiary_cost", "cost_max", "DOUBLE"),
    ("usage_hourly", "prompt_len_sum", "INTEGER DEFAULT 0"),
    ("usage_hourly", "requests_with_tokens", "INTEGER DEFAULT 0"),
)


def _table_exists(conn, table: str) -> bool:
    row = conn.execute(
        """
        SELECT 1
        FROM information_schema.tables
        WHERE table_schema = 'main' AND table_name = ?
        """,
        [table],
    ).fetchone()
    return row is not None


def _column_exists(conn, table: str, column: str) -> bool:
    row = conn.execute(
        """
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = 'main' AND table_name = ? AND column_name = ?
        """,
        [table, column],
    ).fetchone()
    return row is not None


def migrate_schema(conn) -> None:
    if _table_exists(conn, "drugs"):
        conn.execute("DROP TABLE drugs")
    # usage_hourly's primary key gained mode/model columns; older shapes can't be
    # ALTER'd into the new PK, and the table only holds recomputable rollups.
    if _table_exists(conn, "usage_hourly") and not _column_exists(conn, "usage_hourly", "tokens_in_sum"):
        conn.execute("DROP TABLE usage_hourly")
    for table, column, col_type in SCHEMA_MIGRATIONS:
        if _table_exists(conn, table) and not _column_exists(conn, table, column):
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}")


def drop_spuf_indexes(conn) -> None:
    """Drop SPUF lookup indexes before bulk deletes (DuckDB ART index delete bug)."""
    for name in SPUF_INDEX_NAMES:
        conn.execute(f"DROP INDEX IF EXISTS {name}")


def create_indexes(conn) -> None:
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_basic_drugs_formulary "
        "ON basic_drugs_formulary(formulary_id, rxcui)"
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_plans_state_year ON plans(state, contract_year)")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_beneficiary_cost_lookup "
        "ON beneficiary_cost(plan_key, tier, coverage_level, days_supply_code, pharmacy_channel)"
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_pricing_plan_ndc ON pricing(plan_key, ndc, days_supply)")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_insulin_beneficiary_cost "
        "ON insulin_beneficiary_cost(plan_key, tier, days_supply_code, pharmacy_channel)"
    )


def ensure_schema(db: DuckDBConnection | None = None) -> None:
    db = db or DuckDBConnection()
    conn = db.connect()
    try:
        create_tables(conn, drop_existing=False)
        migrate_schema(conn)
        create_indexes(conn)
    finally:
        conn.close()
