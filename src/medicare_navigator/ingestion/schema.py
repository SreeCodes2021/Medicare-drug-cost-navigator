"""DuckDB schema creation shared by SPUF ingestion and empty-table bootstrap."""

from __future__ import annotations

from medicare_navigator.storage.connection import DuckDBConnection


def create_tables(conn, *, drop_existing: bool = True) -> None:
    if drop_existing:
        for table in (
            "beneficiary_cost",
            "drugs",
            "plans",
            "formulary",
            "cost_trends",
            "alternatives",
            "policy_passages",
            "query_log",
            "pricing",
        ):
            conn.execute(f"DROP TABLE IF EXISTS {table}")

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS drugs (
            drug_name VARCHAR, rxcui VARCHAR, ndc VARCHAR,
            dosage VARCHAR, ingredient VARCHAR
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS plans (
            plan_key VARCHAR PRIMARY KEY, contract_id VARCHAR, plan_id VARCHAR,
            plan_name VARCHAR, plan_type VARCHAR, state VARCHAR,
            deductible DOUBLE, contract_year INTEGER, formulary_id VARCHAR
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS formulary (
            plan_key VARCHAR, ndc VARCHAR, rxcui VARCHAR, tier INTEGER,
            copay DOUBLE, coinsurance_pct DOUBLE, cost_type VARCHAR,
            pharmacy_channel VARCHAR, as_of_date VARCHAR
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS beneficiary_cost (
            plan_key VARCHAR, tier INTEGER, coverage_level INTEGER,
            days_supply INTEGER, pharmacy_channel VARCHAR,
            cost_type VARCHAR, copay DOUBLE, coinsurance_pct DOUBLE
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
        CREATE TABLE IF NOT EXISTS cost_trends (
            rxcui VARCHAR, drug_name VARCHAR, year INTEGER,
            total_spend DOUBLE, avg_unit_cost DOUBLE, as_of_date VARCHAR
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS alternatives (
            rxcui VARCHAR, drug_name VARCHAR, ingredient VARCHAR, te_code VARCHAR
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS policy_passages (
            passage_id VARCHAR PRIMARY KEY, text VARCHAR,
            source_label VARCHAR, url VARCHAR, as_of_date VARCHAR
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS query_log (
            query_id VARCHAR, session_id VARCHAR, tools_invoked VARCHAR,
            agents_invoked VARCHAR, statuses VARCHAR, latency_ms DOUBLE,
            created_at TIMESTAMP DEFAULT current_timestamp
        )
        """
    )


SPUF_INDEX_NAMES = (
    "idx_formulary_plan_ndc",
    "idx_plans_state_year",
    "idx_beneficiary_cost_lookup",
    "idx_pricing_plan_ndc",
)


def drop_spuf_indexes(conn) -> None:
    """Drop SPUF lookup indexes before bulk deletes (DuckDB ART index delete bug)."""
    for name in SPUF_INDEX_NAMES:
        conn.execute(f"DROP INDEX IF EXISTS {name}")


def create_indexes(conn) -> None:
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_formulary_plan_ndc ON formulary(plan_key, ndc)"
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_plans_state_year ON plans(state, contract_year)")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_beneficiary_cost_lookup "
        "ON beneficiary_cost(plan_key, tier, pharmacy_channel, coverage_level, days_supply)"
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_pricing_plan_ndc ON pricing(plan_key, ndc)")


def ensure_schema(db: DuckDBConnection | None = None) -> None:
    db = db or DuckDBConnection()
    conn = db.connect()
    try:
        create_tables(conn, drop_existing=False)
        create_indexes(conn)
    finally:
        conn.close()
