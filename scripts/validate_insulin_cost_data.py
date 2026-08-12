#!/usr/bin/env python3
"""Post-ingest validation for insulin_beneficiary_cost rows.

Run after each real CMS SPUF ingest to re-check assumptions documented in
docs/insulin-cost-estimation.md §5 and §10:

  1. No copay exceeds the statutory cap for its days-supply code
     (30-day: $35, 60-day: $70, 90-day: $105).
  2. No duplicate lookup keys with conflicting copays for the same
     (plan_key, tier, days_supply_code, pharmacy_channel) — a proxy for
     segment_id ambiguity when multiple segments disagree.

Usage:
    python scripts/validate_insulin_cost_data.py
    python scripts/validate_insulin_cost_data.py --db data/navigator.duckdb
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# CMS days-supply code -> max copay per fill (IRA statutory cap, CY2026).
_CAP_BY_DAYS_SUPPLY_CODE: dict[int, float] = {
    1: 35.0,  # 30-day
    4: 70.0,  # 60-day
    2: 105.0,  # 90-day
}


def validate(db_path: Path | None = None) -> int:
    from medicare_navigator.storage.connection import DuckDBConnection

    db = DuckDBConnection(path=db_path) if db_path else DuckDBConnection()
    conn = db.connect()
    errors: list[str] = []
    try:
        row_count = conn.execute("SELECT COUNT(*) FROM insulin_beneficiary_cost").fetchone()[0]
        if row_count == 0:
            print("WARN: insulin_beneficiary_cost table is empty — run SPUF re-ingest.")
            return 1

        over_cap = conn.execute(
            """
            SELECT plan_key, tier, days_supply_code, pharmacy_channel, copay
            FROM insulin_beneficiary_cost
            WHERE (days_supply_code = 1 AND copay > 35.0)
               OR (days_supply_code = 4 AND copay > 70.0)
               OR (days_supply_code = 2 AND copay > 105.0)
            LIMIT 20
            """
        ).fetchall()
        for plan_key, tier, code, channel, copay in over_cap:
            cap = _CAP_BY_DAYS_SUPPLY_CODE.get(code, float("inf"))
            errors.append(
                f"cap violation: {plan_key} tier={tier} code={code} {channel} "
                f"copay=${copay:.2f} > max ${cap:.2f}"
            )

        conflicts = conn.execute(
            """
            SELECT plan_key, tier, days_supply_code, pharmacy_channel,
                   COUNT(DISTINCT copay) AS distinct_copays,
                   COUNT(*) AS row_count
            FROM insulin_beneficiary_cost
            GROUP BY 1, 2, 3, 4
            HAVING COUNT(DISTINCT copay) > 1
            LIMIT 20
            """
        ).fetchall()
        for plan_key, tier, code, channel, distinct_copays, row_count in conflicts:
            errors.append(
                f"segment ambiguity: {plan_key} tier={tier} code={code} {channel} "
                f"has {distinct_copays} distinct copays across {row_count} rows "
                f"(segment_id not used in lookups)"
            )

        unknown_codes = conn.execute(
            """
            SELECT DISTINCT days_supply_code
            FROM insulin_beneficiary_cost
            WHERE days_supply_code NOT IN (1, 2, 4)
            """
        ).fetchall()
        for (code,) in unknown_codes:
            errors.append(f"unmapped days_supply_code in insulin table: {code}")

    finally:
        conn.close()

    if errors:
        print(f"FAILED: {len(errors)} issue(s) found in insulin_beneficiary_cost:")
        for err in errors:
            print(f"  - {err}")
        return 1

    print(f"OK: {row_count:,} insulin cost-share rows passed cap and conflict checks.")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--db",
        type=Path,
        default=None,
        help="Path to navigator.duckdb (default: settings.data_dir/navigator.duckdb)",
    )
    args = parser.parse_args()
    raise SystemExit(validate(args.db))


if __name__ == "__main__":
    main()
