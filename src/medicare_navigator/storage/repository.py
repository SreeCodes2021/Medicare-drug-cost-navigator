from __future__ import annotations

from dataclasses import dataclass

from medicare_navigator.ingestion.ndc import format_ndc_display, normalize_ndc
from medicare_navigator.storage.connection import DuckDBConnection


def ndc_variants(ndc: str) -> list[str]:
    variants: list[str] = [ndc]
    try:
        normalized = normalize_ndc(ndc)
        variants.append(normalized)
        variants.append(format_ndc_display(normalized))
    except ValueError:
        pass
    return list(dict.fromkeys(variants))


@dataclass
class BasicDrugsFormularyRecord:
    formulary_id: str
    ndc: str
    rxcui: str | None
    tier: int
    quantity_limit_yn: bool
    quantity_limit_amount: float | None
    quantity_limit_days: int | None
    prior_authorization_yn: bool
    step_therapy_yn: bool
    as_of_date: str


class PlanRepository:
    _COLUMNS = (
        "plan_key, contract_id, plan_id, plan_name, plan_type, state, "
        "deductible, contract_year, formulary_id, plan_suppressed"
    )

    def __init__(self, db: DuckDBConnection | None = None) -> None:
        self.db = db or DuckDBConnection()

    def _row_to_dict(self, row) -> dict:
        return {
            "plan_key": row[0],
            "contract_id": row[1],
            "plan_id": row[2],
            "plan_name": row[3],
            "plan_type": row[4],
            "state": row[5],
            "deductible": row[6],
            "contract_year": row[7],
            "formulary_id": row[8],
            "plan_suppressed": bool(row[9]),
        }

    def list_plans(
        self,
        plan_type: str | None = None,
        state: str | None = None,
        contract_year: int | None = None,
    ) -> list[dict]:
        clauses = ["1=1"]
        params: list = []
        if plan_type:
            clauses.append("plan_type = ?")
            params.append(plan_type)
        if state:
            clauses.append("state = ?")
            params.append(state)
        if contract_year:
            clauses.append("contract_year = ?")
            params.append(contract_year)
        where = " AND ".join(clauses)
        rows = self.db.fetchall(
            f"SELECT {self._COLUMNS} FROM plans WHERE {where} ORDER BY plan_name",
            params,
        )
        return [self._row_to_dict(r) for r in rows]

    def list_states(self) -> list[str]:
        """Distinct states with at least one ingested plan, for state-picker UIs."""
        rows = self.db.fetchall(
            "SELECT DISTINCT state FROM plans WHERE state IS NOT NULL AND state != '' "
            "ORDER BY state"
        )
        return [r[0] for r in rows]

    def list_contract_years(self) -> list[int]:
        """Distinct contract years present in ingested plan data."""
        rows = self.db.fetchall(
            "SELECT DISTINCT contract_year FROM plans "
            "WHERE contract_year IS NOT NULL ORDER BY contract_year DESC"
        )
        return [int(r[0]) for r in rows]

    def get_plan(self, plan_key: str) -> dict | None:
        row = self.db.fetchone(
            f"SELECT {self._COLUMNS} FROM plans WHERE plan_key = ?",
            [plan_key],
        )
        if not row:
            return None
        return self._row_to_dict(row)

    def fuzzy_match_plan(self, text: str) -> list[dict]:
        rows = self.db.fetchall(
            f"""
            SELECT {self._COLUMNS}
            FROM plans
            WHERE lower(plan_name) LIKE lower(?)
               OR plan_key LIKE lower(?)
               OR lower(contract_id || '-' || plan_id) LIKE lower(?)
            ORDER BY plan_name
            LIMIT 5
            """,
            [f"%{text}%", f"%{text}%", f"%{text}%"],
        )
        return [self._row_to_dict(r) for r in rows]


class BasicDrugsFormularyRepository:
    def __init__(self, db: DuckDBConnection | None = None) -> None:
        self.db = db or DuckDBConnection()

    def get_matches(self, formulary_id: str, rxcui: str) -> list[BasicDrugsFormularyRecord]:
        rows = self.db.fetchall(
            """
            SELECT formulary_id, ndc, rxcui, tier, quantity_limit_yn, quantity_limit_amount,
                   quantity_limit_days, prior_authorization_yn, step_therapy_yn, as_of_date
            FROM basic_drugs_formulary
            WHERE formulary_id = ? AND rxcui = ?
            """,
            [formulary_id, rxcui],
        )
        return [
            BasicDrugsFormularyRecord(
                formulary_id=r[0],
                ndc=r[1],
                rxcui=r[2],
                tier=r[3],
                quantity_limit_yn=bool(r[4]),
                quantity_limit_amount=r[5],
                quantity_limit_days=r[6],
                prior_authorization_yn=bool(r[7]),
                step_therapy_yn=bool(r[8]),
                as_of_date=r[9],
            )
            for r in rows
        ]

    def get_matches_any(
        self, formulary_id: str, rxcuis: list[str]
    ) -> list[BasicDrugsFormularyRecord]:
        """Return formulary rows whose rxcui is any of ``rxcuis`` (deduped)."""
        if not rxcuis:
            return []
        unique = list(dict.fromkeys(str(r) for r in rxcuis if r))
        if not unique:
            return []
        placeholders = ", ".join("?" for _ in unique)
        rows = self.db.fetchall(
            f"""
            SELECT formulary_id, ndc, rxcui, tier, quantity_limit_yn, quantity_limit_amount,
                   quantity_limit_days, prior_authorization_yn, step_therapy_yn, as_of_date
            FROM basic_drugs_formulary
            WHERE formulary_id = ? AND rxcui IN ({placeholders})
            """,
            [formulary_id, *unique],
        )
        return [
            BasicDrugsFormularyRecord(
                formulary_id=r[0],
                ndc=r[1],
                rxcui=r[2],
                tier=r[3],
                quantity_limit_yn=bool(r[4]),
                quantity_limit_amount=r[5],
                quantity_limit_days=r[6],
                prior_authorization_yn=bool(r[7]),
                step_therapy_yn=bool(r[8]),
                as_of_date=r[9],
            )
            for r in rows
        ]

    def has_any_rxcui(self, formulary_id: str, rxcuis: list[str]) -> bool:
        if not rxcuis:
            return False
        placeholders = ", ".join("?" for _ in rxcuis)
        row = self.db.fetchone(
            f"""
            SELECT 1
            FROM basic_drugs_formulary
            WHERE formulary_id = ? AND rxcui IN ({placeholders})
            LIMIT 1
            """,
            [formulary_id, *rxcuis],
        )
        return row is not None


class BeneficiaryCostRepository:
    def __init__(self, db: DuckDBConnection | None = None) -> None:
        self.db = db or DuckDBConnection()

    def get_cost_share(
        self,
        plan_key: str,
        tier: int,
        *,
        coverage_level: int,
        days_supply_code: int | None,
        pharmacy_channel: str = "preferred_retail",
    ) -> tuple[str, float | None, float | None, float | None] | None:
        """Returns (cost_type, copay, coinsurance_pct, cost_max) for the exact match, or None.

        ``days_supply_code`` may be None when the requested raw days-supply doesn't map to
        any known CMS code (Section 4's explicit "other" branch) — in that case no cost-share
        row can be matched, by design (no silent coercion to a nearby code).
        """
        if days_supply_code is None:
            return None
        row = self.db.fetchone(
            """
            SELECT cost_type, copay, coinsurance_pct, cost_max
            FROM beneficiary_cost
            WHERE plan_key = ? AND tier = ? AND pharmacy_channel = ?
              AND coverage_level = ? AND days_supply_code = ?
            """,
            [plan_key, tier, pharmacy_channel, coverage_level, days_supply_code],
        )
        if not row:
            return None
        return row[0], row[1], row[2], row[3]

    def get_ded_applies(self, plan_key: str, tier: int) -> bool | None:
        """DED_APPLIES_YN for this tier (Bug 2 per-tier deductible exemption). Picks the
        preferred-retail row when available since the flag is a tier-level attribute, not
        expected to vary by channel/coverage-level/days-supply."""
        row = self.db.fetchone(
            """
            SELECT ded_applies_yn
            FROM beneficiary_cost
            WHERE plan_key = ? AND tier = ?
            ORDER BY CASE WHEN pharmacy_channel = 'preferred_retail' THEN 0 ELSE 1 END,
                     coverage_level, days_supply_code
            LIMIT 1
            """,
            [plan_key, tier],
        )
        if not row:
            return None
        return bool(row[0])


class InsulinBeneficiaryCostRepository:
    """Insulin cost-share lookups keyed on plan_key (not segment_id).

    segment_id is stored at ingest but not used in queries — same convention as
    BeneficiaryCostRepository. MA-PD plans with multiple local segments may have
    ambiguous copays when segment rows differ; inherited project-wide limitation.
    """

    def __init__(self, db: DuckDBConnection | None = None) -> None:
        self.db = db or DuckDBConnection()

    def get_cost_share(
        self,
        plan_key: str,
        tier: int,
        days_supply_code: int | None,
        *,
        pharmacy_channel: str = "preferred_retail",
    ) -> float | None:
        """Returns the already-capped insulin copay for this plan/tier/fill-size/channel,
        or None if not offered / no data. Falls back to TIER IS NULL when the exact tier
        has no row — CMS uses a "." (parsed to NULL) tier sentinel for defined-standard
        plans, which price insulin with a single flat schedule regardless of formulary
        tier.

        ``days_supply_code`` may be None when the requested raw days-supply doesn't map
        to any known CMS code — same "no silent coercion" contract as
        BeneficiaryCostRepository.get_cost_share.
        """
        if days_supply_code is None:
            return None
        row = self.db.fetchone(
            """
            SELECT copay FROM insulin_beneficiary_cost
            WHERE plan_key = ? AND tier = ? AND days_supply_code = ? AND pharmacy_channel = ?
            """,
            [plan_key, tier, days_supply_code, pharmacy_channel],
        )
        if not row:
            row = self.db.fetchone(
                """
                SELECT copay FROM insulin_beneficiary_cost
                WHERE plan_key = ? AND tier IS NULL AND days_supply_code = ? AND pharmacy_channel = ?
                """,
                [plan_key, days_supply_code, pharmacy_channel],
            )
        if not row:
            return None
        return float(row[0])

    def has_any(self, plan_key: str, tier: int, days_supply_code: int) -> bool:
        """True if this plan has an insulin cost-share row for this tier (or the
        defined-standard-plan NULL-tier fallback) and fill-size, in any channel."""
        row = self.db.fetchone(
            """
            SELECT 1 FROM insulin_beneficiary_cost
            WHERE plan_key = ? AND (tier = ? OR tier IS NULL) AND days_supply_code = ?
            LIMIT 1
            """,
            [plan_key, tier, days_supply_code],
        )
        return row is not None


class PharmacyRepository:
    """Pharmacy locator queries joining `pharmacies` (NPPES enrichment) with
    `pharmacy_network` (CMS SPUF plan membership). Distance/radius filtering happens in
    tools/pharmacy_lookup.py, not here — this repository only returns SQL-filtered
    candidate rows."""

    def __init__(self, db: DuckDBConnection | None = None) -> None:
        self.db = db or DuckDBConnection()

    def nearby_candidates(
        self,
        *,
        plan_key: str | None = None,
        preferred_only: bool | None = None,
    ) -> list[dict]:
        """Candidate pharmacies for distance filtering.

        When ``plan_key`` is given, restricts to that plan's CMS pharmacy network
        (optionally preferred-only). When ``plan_key`` is None, a ZIP-only locator
        question isn't scoped to any one plan's network, so every enriched pharmacy is
        a candidate.
        """
        if plan_key:
            clauses = ["n.plan_key = ?"]
            params: list = [plan_key]
            if preferred_only:
                clauses.append("n.preferred_yn = TRUE")
            where = " AND ".join(clauses)
            rows = self.db.fetchall(
                f"""
                SELECT p.npi, p.pharmacy_name, p.address_line1, p.city, p.state, p.zip_code,
                       n.preferred_yn, n.retail_yn, n.mail_yn
                FROM pharmacies p
                JOIN pharmacy_network n ON p.npi = n.npi
                WHERE {where}
                """,
                params,
            )
        else:
            rows = self.db.fetchall(
                """
                SELECT npi, pharmacy_name, address_line1, city, state, zip_code,
                       NULL, NULL, NULL
                FROM pharmacies
                """
            )
        return [
            {
                "npi": r[0],
                "pharmacy_name": r[1],
                "address_line1": r[2],
                "city": r[3],
                "state": r[4],
                "zip_code": r[5],
                "preferred_yn": bool(r[6]) if r[6] is not None else None,
                "retail_yn": bool(r[7]) if r[7] is not None else None,
                "mail_yn": bool(r[8]) if r[8] is not None else None,
            }
            for r in rows
        ]


class PricingRepository:
    def __init__(self, db: DuckDBConnection | None = None) -> None:
        self.db = db or DuckDBConnection()

    def get_unit_cost(self, plan_key: str, ndc: str, days_supply: int = 30) -> float | None:
        for variant in ndc_variants(ndc):
            row = self.db.fetchone(
                """
                SELECT unit_cost FROM pricing
                WHERE plan_key = ? AND (ndc = ? OR REPLACE(ndc, '-', '') = REPLACE(?, '-', ''))
                  AND days_supply = ?
                """,
                [plan_key, variant, variant, days_supply],
            )
            if row:
                return float(row[0])
        return None
