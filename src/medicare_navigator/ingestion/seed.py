"""Seed demo data into DuckDB and Chroma for local development and evaluation."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import yaml

from medicare_navigator.config import settings
from medicare_navigator.storage.connection import DuckDBConnection

AS_OF = "2026-01-15"
SOURCE_SPUF = "cms_spuf_2026_q1_demo"
SOURCE_SPENDING = "cms_part_d_spending_demo"
SOURCE_ORANGE_BOOK = "fda_orange_book_demo"
SOURCE_RXNORM = "rxnorm_cache_demo"

DEMO_DRUGS = [
    ("metformin", "6809", "00093-7214-01", "500mg", "metformin"),
    ("metformin", "6809", "00093-7214-10", "1000mg", "metformin"),
    ("lisinopril", "29046", "00378-1805-01", "10mg", "lisinopril"),
    ("lisinopril", "29046", "00378-1805-10", "20mg", "lisinopril"),
    ("atorvastatin", "83367", "00071-0156-23", "20mg", "atorvastatin"),
    ("atorvastatin", "83367", "00071-0157-23", "40mg", "atorvastatin"),
    ("omeprazole", "7646", "00378-3590-77", "20mg", "omeprazole"),
    ("eliquis", "1364430", "00003-0894-21", "5mg", "apixaban"),
    ("januvia", "593411", "00006-0112-54", "100mg", "sitagliptin"),
    ("lipitor", "153165", "00071-0157-23", "40mg", "atorvastatin"),
]

# plan_key, ndc, tier, copay, coinsurance_pct, cost_type
FORMULARY_ENTRIES = [
    ("H1234-001", "00093-7214-01", 1, 5.0, None, "copay"),
    ("H1234-001", "00378-1805-01", 1, 3.0, None, "copay"),
    ("H1234-001", "00071-0156-23", 2, 15.0, None, "copay"),
    ("H1234-001", "00378-3590-77", 1, 5.0, None, "copay"),
    ("H1234-045", "00093-7214-01", 1, 0.0, None, "copay"),
    ("H1234-045", "00378-1805-01", 1, 0.0, None, "copay"),
    ("H1234-045", "00071-0156-23", 2, 10.0, None, "copay"),
    ("H1234-045", "00003-0894-21", 3, 47.0, None, "copay"),
    ("S5678-012", "00093-7214-01", 2, 8.0, None, "copay"),
    ("S5678-012", "00378-1805-01", 1, 2.0, None, "copay"),
    ("S5678-018", "00093-7214-01", 2, 12.0, None, "copay"),
    ("A9012-003", "00093-7214-01", 1, 4.0, None, "copay"),
    ("A9012-003", "00071-0156-23", 2, 18.0, None, "copay"),
    ("U3456-002", "00093-7214-01", 1, 6.0, None, "copay"),
    ("U3456-002", "00378-1805-01", 1, 4.0, None, "copay"),
    ("C7890-004", "00093-7214-01", 1, 7.0, None, "copay"),
    ("W2345-006", "00378-1805-01", 1, 5.0, None, "copay"),
    ("B6789-009", "00071-0156-23", 2, 20.0, None, "copay"),
    ("M4567-015", "00093-7214-01", 1, 5.0, None, "copay"),
    # januvia excluded from S5678-018 (formulary exclusion demo)
]

COST_TRENDS = [
    ("6809", "metformin", 2022, 1_200_000_000, 0.12),
    ("6809", "metformin", 2023, 1_250_000_000, 0.13),
    ("6809", "metformin", 2024, 1_310_000_000, 0.14),
    ("6809", "metformin", 2025, 1_380_000_000, 0.15),
    ("29046", "lisinopril", 2022, 800_000_000, 0.08),
    ("29046", "lisinopril", 2023, 850_000_000, 0.09),
    ("29046", "lisinopril", 2024, 920_000_000, 0.10),
    ("29046", "lisinopril", 2025, 1_050_000_000, 0.12),
    ("83367", "atorvastatin", 2022, 2_100_000_000, 0.25),
    ("83367", "atorvastatin", 2023, 2_000_000_000, 0.22),
    ("83367", "atorvastatin", 2024, 1_900_000_000, 0.20),
    ("83367", "atorvastatin", 2025, 1_850_000_000, 0.19),
    ("1364430", "eliquis", 2022, 8_000_000_000, 6.50),
    ("1364430", "eliquis", 2023, 8_500_000_000, 6.80),
    ("1364430", "eliquis", 2024, 9_100_000_000, 7.10),
    ("1364430", "eliquis", 2025, 9_800_000_000, 7.50),
]

ALTERNATIVES = [
    ("6809", "metformin", "metformin", "A"),
    ("153165", "lipitor", "atorvastatin", "A"),
    ("83367", "atorvastatin", "atorvastatin", "A"),
    ("29046", "lisinopril", "lisinopril", "A"),
    ("7646", "omeprazole", "omeprazole", "A"),
]

POLICY_PASSAGES = [
    {
        "id": "cms_part_d_redesign_2026",
        "text": "For CY 2026, the Part D annual out-of-pocket threshold is $2,100. The standard deductible is $615. After the deductible, enrollees pay 25% coinsurance during initial coverage until reaching the OOP threshold, then enter catastrophic coverage with $0 cost sharing.",
        "source_label": "CMS CY 2026 Part D Redesign Instructions",
        "url": "https://www.cms.gov/newsroom/fact-sheets/final-cy-2026-part-d-redesign-program-instructions",
    },
    {
        "id": "ira_negotiated_prices",
        "text": "The Inflation Reduction Act Medicare Drug Price Negotiation Program establishes Maximum Fair Prices for selected drugs. When negotiated prices take effect, plan liability and beneficiary cost sharing may change for those selected drugs.",
        "source_label": "CMS Medicare Drug Price Negotiation Program",
        "url": "https://www.cms.gov/medicare/medicare-drug-price-negotiation",
    },
    {
        "id": "formulary_tier_explanation",
        "text": "Part D plans place drugs on formulary tiers. Lower tiers typically have lower cost sharing. A formulary tier change can increase or decrease what a beneficiary pays even if the underlying drug price stays the same.",
        "source_label": "CMS SPUF Methodology",
        "url": "https://www.cms.gov/files/document/methodology-spuf-2025.pdf",
    },
]


def _load_plans_from_yaml() -> list[dict]:
    path = settings.config_dir / "demo_plans.yaml"
    with path.open(encoding="utf-8") as f:
        data = yaml.safe_load(f)
    contract_year = data.get("contract_year", 2026)
    plans = []
    for p in data["plans"]:
        plan_key = f"{p['contract_id']}-{p['plan_id']}"
        plans.append(
            {
                "plan_key": plan_key,
                "contract_id": p["contract_id"],
                "plan_id": p["plan_id"],
                "plan_name": p["plan_name"],
                "plan_type": p["plan_type"],
                "state": p["state"],
                "deductible": float(p["deductible"]),
                "contract_year": contract_year,
            }
        )
    return plans


def seed_duckdb(db: DuckDBConnection | None = None) -> None:
    db = db or DuckDBConnection()
    conn = db.connect()
    try:
        conn.execute("DROP TABLE IF EXISTS drugs")
        conn.execute("DROP TABLE IF EXISTS plans")
        conn.execute("DROP TABLE IF EXISTS formulary")
        conn.execute("DROP TABLE IF EXISTS cost_trends")
        conn.execute("DROP TABLE IF EXISTS alternatives")
        conn.execute("DROP TABLE IF EXISTS policy_passages")
        conn.execute("DROP TABLE IF EXISTS query_log")

        conn.execute(
            """
            CREATE TABLE drugs (
                drug_name VARCHAR, rxcui VARCHAR, ndc VARCHAR,
                dosage VARCHAR, ingredient VARCHAR
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE plans (
                plan_key VARCHAR PRIMARY KEY, contract_id VARCHAR, plan_id VARCHAR,
                plan_name VARCHAR, plan_type VARCHAR, state VARCHAR,
                deductible DOUBLE, contract_year INTEGER
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE formulary (
                plan_key VARCHAR, ndc VARCHAR, tier INTEGER,
                copay DOUBLE, coinsurance_pct DOUBLE, cost_type VARCHAR, as_of_date VARCHAR
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE cost_trends (
                rxcui VARCHAR, drug_name VARCHAR, year INTEGER,
                total_spend DOUBLE, avg_unit_cost DOUBLE, as_of_date VARCHAR
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE alternatives (
                rxcui VARCHAR, drug_name VARCHAR, ingredient VARCHAR, te_code VARCHAR
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE policy_passages (
                passage_id VARCHAR PRIMARY KEY, text VARCHAR,
                source_label VARCHAR, url VARCHAR, as_of_date VARCHAR
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE query_log (
                query_id VARCHAR, session_id VARCHAR, tools_invoked VARCHAR,
                agents_invoked VARCHAR, statuses VARCHAR, latency_ms DOUBLE,
                created_at TIMESTAMP DEFAULT current_timestamp
            )
            """
        )

        for drug in DEMO_DRUGS:
            conn.execute("INSERT INTO drugs VALUES (?, ?, ?, ?, ?)", list(drug))

        for plan in _load_plans_from_yaml():
            conn.execute(
                "INSERT INTO plans VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    plan["plan_key"],
                    plan["contract_id"],
                    plan["plan_id"],
                    plan["plan_name"],
                    plan["plan_type"],
                    plan["state"],
                    plan["deductible"],
                    plan["contract_year"],
                ],
            )

        for entry in FORMULARY_ENTRIES:
            conn.execute(
                "INSERT INTO formulary VALUES (?, ?, ?, ?, ?, ?, ?)",
                [*entry, AS_OF],
            )

        for trend in COST_TRENDS:
            conn.execute(
                "INSERT INTO cost_trends VALUES (?, ?, ?, ?, ?, ?)",
                [*trend, AS_OF],
            )

        for alt in ALTERNATIVES:
            conn.execute("INSERT INTO alternatives VALUES (?, ?, ?, ?)", list(alt))

        for passage in POLICY_PASSAGES:
            conn.execute(
                "INSERT INTO policy_passages VALUES (?, ?, ?, ?, ?)",
                [passage["id"], passage["text"], passage["source_label"], passage["url"], AS_OF],
            )
    finally:
        conn.close()


def seed_chroma() -> None:
    try:
        import chromadb
    except ImportError:
        return

    try:
        chroma_path = settings.chroma_path
        chroma_path.mkdir(parents=True, exist_ok=True)
        client = chromadb.PersistentClient(path=str(chroma_path))
        collection = client.get_or_create_collection(name="policy_corpus")

        ids = [p["id"] for p in POLICY_PASSAGES]
        documents = [p["text"] for p in POLICY_PASSAGES]
        metadatas = [
            {"source_label": p["source_label"], "url": p["url"], "as_of_date": AS_OF}
            for p in POLICY_PASSAGES
        ]
        collection.upsert(ids=ids, documents=documents, metadatas=metadatas)
    except Exception as exc:
        print(f"Chroma seed skipped (policy retrieval uses DuckDB fallback): {exc}")


def write_manifest() -> None:
    manifest = {
        "spuf": {"version": "SPUF.2026.20260115.demo", "as_of": AS_OF, "source_id": SOURCE_SPUF},
        "spending": {"as_of": AS_OF, "source_id": SOURCE_SPENDING},
        "orange_book": {"as_of": AS_OF, "source_id": SOURCE_ORANGE_BOOK},
        "rxnorm": {"as_of": AS_OF, "source_id": SOURCE_RXNORM},
        "benefit_params": {"contract_year": 2026, "as_of": "2026-01-01"},
        "policy_corpus": {"as_of": AS_OF},
        "seeded_at": date.today().isoformat(),
        "note": "Demo seed data for Phase 1. Replace with real CMS ingestion for production.",
    }
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = settings.data_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")


def run_seed() -> None:
    seed_duckdb()
    seed_chroma()
    write_manifest()
    print(f"Seeded DuckDB at {settings.duckdb_path}")
    print(f"Wrote manifest to {settings.data_dir / 'manifest.json'}")
