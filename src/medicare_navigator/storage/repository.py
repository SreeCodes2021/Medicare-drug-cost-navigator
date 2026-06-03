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
class DrugRecord:
    drug_name: str
    rxcui: str
    ndc: str
    dosage: str
    ingredient: str


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


class DrugRepository:
    def __init__(self, db: DuckDBConnection | None = None) -> None:
        self.db = db or DuckDBConnection()

    def lookup_by_name(self, name: str, dosage: str | None = None) -> list[DrugRecord]:
        rows = self.db.fetchall(
            """
            SELECT drug_name, rxcui, ndc, dosage, ingredient
            FROM drugs
            WHERE lower(drug_name) LIKE lower(?)
               OR lower(ingredient) LIKE lower(?)
            ORDER BY drug_name
            """,
            [f"%{name}%", f"%{name}%"],
        )
        records = [
            DrugRecord(drug_name=r[0], rxcui=r[1], ndc=r[2], dosage=r[3], ingredient=r[4])
            for r in rows
        ]
        if dosage:
            dosage_lower = dosage.lower().replace(" ", "")
            filtered = [r for r in records if dosage_lower in r.dosage.lower().replace(" ", "")]
            return filtered or records
        return records

    def lookup_by_rxcui(self, rxcui: str) -> DrugRecord | None:
        row = self.db.fetchone(
            "SELECT drug_name, rxcui, ndc, dosage, ingredient FROM drugs WHERE rxcui = ?",
            [rxcui],
        )
        if not row:
            return None
        return DrugRecord(drug_name=row[0], rxcui=row[1], ndc=row[2], dosage=row[3], ingredient=row[4])


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
    ) -> tuple[str, float | None, float | None] | None:
        """Returns (cost_type, copay, coinsurance_pct) for the exact match, or None.

        ``days_supply_code`` may be None when the requested raw days-supply doesn't map to
        any known CMS code (Section 4's explicit "other" branch) — in that case no cost-share
        row can be matched, by design (no silent coercion to a nearby code).
        """
        if days_supply_code is None:
            return None
        row = self.db.fetchone(
            """
            SELECT cost_type, copay, coinsurance_pct
            FROM beneficiary_cost
            WHERE plan_key = ? AND tier = ? AND pharmacy_channel = ?
              AND coverage_level = ? AND days_supply_code = ?
            """,
            [plan_key, tier, pharmacy_channel, coverage_level, days_supply_code],
        )
        if not row:
            return None
        return row[0], row[1], row[2]

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
