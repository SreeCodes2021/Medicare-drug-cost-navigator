"""Reproduce the state-coverage numbers in docs/state-coverage-analysis.md.

Two data sources:
  1. Already-extracted CMS SPUF plan_information file (see data/analysis/) for
     per-state plan counts (offline, no network call).
  2. CMS's official "Medicare Monthly Enrollment" dataset, fetched live from
     the data.cms.gov catalog API, for per-state beneficiary counts.

Usage:
    python scripts/analyze_state_coverage.py            # plan counts only
    python scripts/analyze_state_coverage.py --enrollment  # also fetch & merge CMS enrollment data
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import httpx
import pandas as pd

DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "analysis"
PLAN_COLS = ["CONTRACT_ID", "PLAN_ID", "SEGMENT_ID"]
CMS_CATALOG_URL = "https://data.cms.gov/data.json"
ENROLLMENT_DATASET_TITLE = "Medicare Monthly Enrollment"
TERRITORY_CODES_TO_DROP = {"AS", "GU", "MP", "VI", "UK", "FO"}


def load_active_plans() -> pd.DataFrame:
    df = pd.read_csv(DATA_DIR / "plan_information_decoded.txt", sep="|", dtype=str)
    df["STATE"] = df["STATE"].str.strip()
    return df[df["PLAN_SUPPRESSED_YN"] == "N"].copy()


def state_plan_counts(active: pd.DataFrame) -> pd.DataFrame:
    state_plans = active[active["STATE"] != ""].drop_duplicates(subset=PLAN_COLS + ["STATE"])
    counts = state_plans.groupby("STATE").size().sort_values(ascending=False)
    total = counts.sum()
    report = counts.reset_index()
    report.columns = ["STATE", "PLAN_COUNT"]
    report["PCT_OF_NATIONAL"] = (report["PLAN_COUNT"] / total * 100).round(2)
    return report


def fetch_latest_enrollment_csv_url(client: httpx.Client) -> str:
    """Resolve the newest 'Medicare Monthly Enrollment' CSV distribution URL from the CMS catalog."""
    resp = client.get(CMS_CATALOG_URL)
    resp.raise_for_status()
    catalog = resp.json()
    dataset = next(
        (d for d in catalog.get("dataset", []) if d.get("title") == ENROLLMENT_DATASET_TITLE),
        None,
    )
    if dataset is None:
        raise LookupError(f"Dataset not found in CMS catalog: {ENROLLMENT_DATASET_TITLE!r}")
    csv_distros = [d for d in dataset["distribution"] if d.get("mediaType") == "text/csv"]
    if not csv_distros:
        raise LookupError("No CSV distribution found for Medicare Monthly Enrollment")
    return csv_distros[0]["downloadURL"]


def fetch_state_enrollment(client: httpx.Client) -> pd.DataFrame:
    """Download the latest monthly enrollment CSV (~200MB) and return state-level rows only."""
    url = fetch_latest_enrollment_csv_url(client)
    cols = ["YEAR", "MONTH", "BENE_GEO_LVL", "BENE_STATE_ABRVTN", "BENE_STATE_DESC", "TOT_BENES"]

    with tempfile.NamedTemporaryFile(suffix=".csv") as tmp:
        with client.stream("GET", url) as resp:
            resp.raise_for_status()
            for data in resp.iter_bytes(chunk_size=1024 * 1024):
                tmp.write(data)
        tmp.flush()

        chunks = []
        for chunk in pd.read_csv(tmp.name, usecols=cols, chunksize=200_000, dtype=str):
            state_rows = chunk[chunk["BENE_GEO_LVL"] == "State"]
            if len(state_rows):
                chunks.append(state_rows)
    df = pd.concat(chunks, ignore_index=True)
    latest_year, latest_month = df[["YEAR", "MONTH"]].iloc[-1]
    df = df[(df["YEAR"] == latest_year) & (df["MONTH"] == latest_month)]
    df = df[~df["BENE_STATE_ABRVTN"].isin(TERRITORY_CODES_TO_DROP)].copy()
    df["TOT_BENES"] = df["TOT_BENES"].astype(float)
    return df


def merge_plans_and_enrollment(plan_report: pd.DataFrame, enrollment: pd.DataFrame) -> pd.DataFrame:
    national_total = enrollment["TOT_BENES"].sum()
    merged = enrollment.set_index("BENE_STATE_ABRVTN")[["BENE_STATE_DESC", "TOT_BENES"]].copy()
    merged["PCT_OF_NATIONAL_BENES"] = (merged["TOT_BENES"] / national_total * 100).round(2)
    merged["PLAN_COUNT"] = plan_report.set_index("STATE")["PLAN_COUNT"]
    merged["PLANS_PER_10K_BENES"] = (merged["PLAN_COUNT"] / (merged["TOT_BENES"] / 10_000)).round(2)
    return merged.sort_values("TOT_BENES", ascending=False)


def main() -> None:
    active = load_active_plans()
    report = state_plan_counts(active)

    print(f"Unique active plans nationally: {active.drop_duplicates(subset=PLAN_COLS).shape[0]}")
    print(f"States/territories represented: {report.shape[0]}")
    print()
    print(report.to_string(index=False))

    if "--enrollment" in sys.argv:
        with httpx.Client(timeout=httpx.Timeout(60.0, read=300.0), follow_redirects=True) as client:
            enrollment = fetch_state_enrollment(client)
        merged = merge_plans_and_enrollment(report, enrollment)
        print()
        print("=== Beneficiaries by state (latest CMS Medicare Monthly Enrollment) ===")
        print(merged.to_string())


if __name__ == "__main__":
    main()
