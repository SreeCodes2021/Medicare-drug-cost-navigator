from __future__ import annotations

from dataclasses import dataclass

from medicare_navigator.storage.connection import DuckDBConnection


@dataclass
class DrugRecord:
    drug_name: str
    rxcui: str
    ndc: str
    dosage: str
    ingredient: str


@dataclass
class FormularyRecord:
    plan_key: str
    plan_name: str
    contract_id: str
    plan_id: str
    ndc: str
    tier: int
    copay: float | None
    coinsurance_pct: float | None
    cost_type: str
    deductible: float
    as_of_date: str


@dataclass
class CostTrendRecord:
    rxcui: str
    drug_name: str
    year: int
    total_spend: float
    avg_unit_cost: float | None
    as_of_date: str


@dataclass
class AlternativeRecord:
    rxcui: str
    drug_name: str
    ingredient: str
    te_code: str


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
    def __init__(self, db: DuckDBConnection | None = None) -> None:
        self.db = db or DuckDBConnection()

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
            f"""
            SELECT plan_key, contract_id, plan_id, plan_name, plan_type, state, deductible, contract_year
            FROM plans WHERE {where}
            ORDER BY plan_name
            """,
            params,
        )
        return [
            {
                "plan_key": r[0],
                "contract_id": r[1],
                "plan_id": r[2],
                "plan_name": r[3],
                "plan_type": r[4],
                "state": r[5],
                "deductible": r[6],
                "contract_year": r[7],
            }
            for r in rows
        ]

    def get_plan(self, plan_key: str) -> dict | None:
        row = self.db.fetchone(
            """
            SELECT plan_key, contract_id, plan_id, plan_name, plan_type, state, deductible, contract_year
            FROM plans WHERE plan_key = ?
            """,
            [plan_key],
        )
        if not row:
            return None
        return {
            "plan_key": row[0],
            "contract_id": row[1],
            "plan_id": row[2],
            "plan_name": row[3],
            "plan_type": row[4],
            "state": row[5],
            "deductible": row[6],
            "contract_year": row[7],
        }

    def fuzzy_match_plan(self, text: str) -> list[dict]:
        rows = self.db.fetchall(
            """
            SELECT plan_key, contract_id, plan_id, plan_name, plan_type, state, deductible, contract_year
            FROM plans
            WHERE lower(plan_name) LIKE lower(?)
               OR plan_key LIKE lower(?)
               OR lower(contract_id || '-' || plan_id) LIKE lower(?)
            ORDER BY plan_name
            LIMIT 5
            """,
            [f"%{text}%", f"%{text}%", f"%{text}%"],
        )
        return [
            {
                "plan_key": r[0],
                "contract_id": r[1],
                "plan_id": r[2],
                "plan_name": r[3],
                "plan_type": r[4],
                "state": r[5],
                "deductible": r[6],
                "contract_year": r[7],
            }
            for r in rows
        ]


class FormularyRepository:
    def __init__(self, db: DuckDBConnection | None = None) -> None:
        self.db = db or DuckDBConnection()

    def get_formulary_entry(self, plan_key: str, ndc: str) -> FormularyRecord | None:
        row = self.db.fetchone(
            """
            SELECT f.plan_key, p.plan_name, p.contract_id, p.plan_id, f.ndc, f.tier,
                   f.copay, f.coinsurance_pct, f.cost_type, p.deductible, f.as_of_date
            FROM formulary f
            JOIN plans p ON f.plan_key = p.plan_key
            WHERE f.plan_key = ? AND f.ndc = ?
            """,
            [plan_key, ndc],
        )
        if not row:
            return None
        return FormularyRecord(
            plan_key=row[0],
            plan_name=row[1],
            contract_id=row[2],
            plan_id=row[3],
            ndc=row[4],
            tier=row[5],
            copay=row[6],
            coinsurance_pct=row[7],
            cost_type=row[8],
            deductible=row[9],
            as_of_date=row[10],
        )


class CostTrendRepository:
    def __init__(self, db: DuckDBConnection | None = None) -> None:
        self.db = db or DuckDBConnection()

    def get_trend(self, rxcui: str) -> list[CostTrendRecord]:
        rows = self.db.fetchall(
            """
            SELECT rxcui, drug_name, year, total_spend, avg_unit_cost, as_of_date
            FROM cost_trends WHERE rxcui = ? ORDER BY year
            """,
            [rxcui],
        )
        return [
            CostTrendRecord(
                rxcui=r[0],
                drug_name=r[1],
                year=r[2],
                total_spend=r[3],
                avg_unit_cost=r[4],
                as_of_date=r[5],
            )
            for r in rows
        ]


class AlternativesRepository:
    def __init__(self, db: DuckDBConnection | None = None) -> None:
        self.db = db or DuckDBConnection()

    def find_alternatives(self, ingredient: str, exclude_rxcui: str | None = None) -> list[AlternativeRecord]:
        rows = self.db.fetchall(
            """
            SELECT rxcui, drug_name, ingredient, te_code
            FROM alternatives
            WHERE lower(ingredient) = lower(?)
            ORDER BY drug_name
            """,
            [ingredient],
        )
        results = [
            AlternativeRecord(rxcui=r[0], drug_name=r[1], ingredient=r[2], te_code=r[3]) for r in rows
        ]
        if exclude_rxcui:
            results = [r for r in results if r.rxcui != exclude_rxcui]
        return results
