"""Ingest CMS SPUF (Prescription Drug Plan Formulary) data into DuckDB."""

from __future__ import annotations

import csv
import zipfile
from dataclasses import dataclass
from datetime import date
from io import BytesIO, TextIOWrapper
from pathlib import Path
from typing import Any, Iterator

import yaml

from medicare_navigator.config import settings
from medicare_navigator.ingestion.manifest import load_manifest, merge_manifest
from medicare_navigator.ingestion.ndc import format_ndc_display, normalize_ndc
from medicare_navigator.ingestion.schema import create_indexes, create_tables
from medicare_navigator.storage.connection import DuckDBConnection

# CMS SPUF beneficiary cost file column groups by pharmacy channel
PHARMACY_CHANNEL_COLUMNS: dict[str, tuple[str, str]] = {
    "preferred_retail": ("COST_TYPE_PREF", "COST_AMT_PREF"),
    "standard_retail": ("COST_TYPE_NONPREF", "COST_AMT_NONPREF"),
    "preferred_mail": ("COST_TYPE_MAIL_PREF", "COST_AMT_MAIL_PREF"),
    "standard_mail": ("COST_TYPE_MAIL_NONPREF", "COST_AMT_MAIL_NONPREF"),
}

PLAN_FILE_HINTS = ("plan information",)
FORMULARY_FILE_HINTS = ("basic drugs formulary",)
BENEFICIARY_COST_FILE_HINTS = ("beneficiary cost",)
PRICING_FILE_HINTS = ("pricing",)


@dataclass
class IngestFilters:
    contract_year: int
    states: list[str]
    pdp_region_codes: dict[str, str]
    plan_type_prefixes: list[str]

    @classmethod
    def from_yaml(cls, path: Path | None = None) -> IngestFilters:
        path = path or settings.config_dir / "ingest_filters.yaml"
        with path.open(encoding="utf-8") as f:
            data = yaml.safe_load(f)
        return cls(
            contract_year=int(data.get("contract_year", 2026)),
            states=[s.upper() for s in data.get("states", [])],
            pdp_region_codes={k.upper(): str(v) for k, v in data.get("pdp_region_codes", {}).items()},
            plan_type_prefixes=list(data.get("plan_type_prefixes", ["S", "H"])),
        )

    @property
    def pdp_regions(self) -> set[str]:
        return {code for state, code in self.pdp_region_codes.items() if state in self.states}


def _normalize_header(name: str) -> str:
    return name.strip().upper().replace(" ", "_")


def _parse_spuf_row(header: list[str], row: list[str]) -> dict[str, str]:
    normalized_header = [_normalize_header(h) for h in header]
    return dict(zip(normalized_header, row, strict=False))


def _read_pipe_file(path: Path) -> Iterator[dict[str, str]]:
    with path.open(encoding="utf-8", errors="replace", newline="") as f:
        reader = csv.reader(f, delimiter="|")
        header = next(reader, None)
        if not header:
            return
        for row in reader:
            if not row or all(not cell.strip() for cell in row):
                continue
            yield _parse_spuf_row(header, row)


def _find_txt_in_zip_names(names: list[str]) -> str | None:
    for name in names:
        if name.lower().endswith(".txt"):
            return name
    return None


def _read_pipe_from_zip(zf: zipfile.ZipFile, member: str) -> Iterator[dict[str, str]]:
    if member.lower().endswith(".zip"):
        with zipfile.ZipFile(BytesIO(zf.read(member))) as inner_zf:
            inner_member = _find_txt_in_zip_names(inner_zf.namelist())
            if not inner_member:
                return
            yield from _read_pipe_from_zip(inner_zf, inner_member)
        return
    with zf.open(member) as raw:
        wrapper = TextIOWrapper(raw, encoding="utf-8", errors="replace", newline="")
        reader = csv.reader(wrapper, delimiter="|")
        header = next(reader, None)
        if not header:
            return
        for row in reader:
            if not row or all(not cell.strip() for cell in row):
                continue
            yield _parse_spuf_row(header, row)


def _find_member(names: list[str], hints: tuple[str, ...]) -> str | None:
    lowered = [(n, n.lower()) for n in names]
    for hint in hints:
        for original, low in lowered:
            if hint in low and low.endswith(".txt"):
                return original
    for hint in hints:
        for original, low in lowered:
            if hint in low:
                return original
    return None


def _plan_type(contract_id: str) -> str:
    prefix = contract_id[0].upper() if contract_id else ""
    if prefix == "S":
        return "PDP"
    if prefix == "H":
        return "MA-PD"
    if prefix == "R":
        return "MA-PD"
    return "PDP"


def _plan_in_filter(row: dict[str, str], filters: IngestFilters) -> bool:
    contract_id = row.get("CONTRACT_ID", "").strip()
    if not contract_id:
        return False
    if contract_id[0] not in filters.plan_type_prefixes:
        return False
    if row.get("PLAN_SUPPRESSED_YN", "N").strip().upper() == "Y":
        return False

    state = row.get("STATE", "").strip().upper()
    pdp_region = row.get("PDP_REGION_CODE", "").strip()
    if contract_id.startswith("S"):
        return pdp_region in filters.pdp_regions
    if contract_id.startswith("H"):
        return state in filters.states
    return False


def _parse_float(value: str | None) -> float | None:
    if value is None:
        return None
    text = value.strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _parse_int(value: str | None) -> int | None:
    if value is None:
        return None
    text = value.strip()
    if not text:
        return None
    try:
        return int(text)
    except ValueError:
        return None


def _cost_share_from_type(cost_type: str | None, cost_amt: str | None) -> tuple[str, float | None, float | None]:
    """Map CMS cost type code to (cost_type, copay, coinsurance_pct)."""
    code = (cost_type or "").strip()
    amount = _parse_float(cost_amt)
    if code == "1":
        return "copay", amount, None
    if code == "2":
        pct = amount * 100 if amount is not None and amount <= 1 else amount
        return "coinsurance", None, pct
    return "unknown", None, None


def _extract_cost_shares(row: dict[str, str]) -> list[dict[str, Any]]:
    tier = _parse_int(row.get("TIER"))
    if tier is None:
        return []
    coverage_level = _parse_int(row.get("COVERAGE_LEVEL"))
    days_supply = _parse_int(row.get("DAYS_SUPPLY"))
    if days_supply is None:
        days_supply = 1

    shares: list[dict[str, Any]] = []
    for channel, (type_col, amt_col) in PHARMACY_CHANNEL_COLUMNS.items():
        cost_type, copay, coinsurance = _cost_share_from_type(
            row.get(type_col), row.get(amt_col)
        )
        if cost_type == "unknown":
            continue
        shares.append(
            {
                "tier": tier,
                "coverage_level": coverage_level if coverage_level is not None else 1,
                "days_supply": days_supply,
                "pharmacy_channel": channel,
                "cost_type": cost_type,
                "copay": copay,
                "coinsurance_pct": coinsurance,
            }
        )
    return shares


def _discover_spuf_files(source: Path) -> dict[str, str | Path]:
    if source.is_dir():
        names = [p.name for p in source.iterdir() if p.is_file()]
        base = source
        return {
            "plan": base / _find_member(names, PLAN_FILE_HINTS),
            "formulary": base / _find_member(names, FORMULARY_FILE_HINTS),
            "beneficiary_cost": base / _find_member(names, BENEFICIARY_COST_FILE_HINTS),
            "pricing": base / _find_member(names, PRICING_FILE_HINTS),
        }
    if source.suffix.lower() == ".zip":
        with zipfile.ZipFile(source) as zf:
            names = zf.namelist()
            return {
                "plan": _find_member(names, PLAN_FILE_HINTS),
                "formulary": _find_member(names, FORMULARY_FILE_HINTS),
                "beneficiary_cost": _find_member(names, BENEFICIARY_COST_FILE_HINTS),
                "pricing": _find_member(names, PRICING_FILE_HINTS),
            }
    raise FileNotFoundError(f"SPUF source must be a directory or .zip file: {source}")


def _iter_rows(source: Path, member: str | Path | None) -> Iterator[dict[str, str]]:
    if member is None:
        return
    if isinstance(member, Path):
        yield from _read_pipe_file(member)
        return
    with zipfile.ZipFile(source) as zf:
        yield from _read_pipe_from_zip(zf, member)


def _purge_states(conn, states: list[str]) -> int:
    """Remove plans and related SPUF rows for the given state codes. Returns plans removed."""
    if not states:
        return 0
    normalized = [s.upper() for s in states]
    placeholders = ", ".join("?" * len(normalized))
    rows = conn.execute(
        f"SELECT plan_key FROM plans WHERE upper(state) IN ({placeholders})",
        normalized,
    ).fetchall()
    plan_keys = [row[0] for row in rows]
    if not plan_keys:
        return 0
    key_placeholders = ", ".join("?" * len(plan_keys))
    for table in ("formulary", "beneficiary_cost", "pricing"):
        conn.execute(f"DELETE FROM {table} WHERE plan_key IN ({key_placeholders})", plan_keys)
    conn.execute(f"DELETE FROM plans WHERE plan_key IN ({key_placeholders})", plan_keys)
    return len(plan_keys)


def _parse_as_of_from_version(version: str) -> str:
    # SPUF.2026.20260115 -> 2026-01-15
    parts = version.replace(".zip", "").split(".")
    if len(parts) >= 3 and len(parts[-1]) == 8 and parts[-1].isdigit():
        d = parts[-1]
        return f"{d[:4]}-{d[4:6]}-{d[6:8]}"
    return date.today().isoformat()


def ingest_spuf(
    source: Path,
    *,
    filters: IngestFilters | None = None,
    db: DuckDBConnection | None = None,
    version: str | None = None,
    preserve_non_spuf_tables: bool = False,
    merge_states: bool = False,
) -> dict[str, Any]:
    """Load CMS SPUF into DuckDB. Source may be a .zip or directory of pipe-delimited files."""
    filters = filters or IngestFilters.from_yaml()
    files = _discover_spuf_files(source)
    if not files.get("plan") or not files.get("formulary"):
        raise FileNotFoundError(
            "SPUF source must include plan information and basic drugs formulary files. "
            f"Found: {list(files.keys())}"
        )

    version = version or source.stem
    as_of = _parse_as_of_from_version(version)
    source_id = f"cms_spuf_{filters.contract_year}_q1"

    db = db or DuckDBConnection()
    conn = db.connect()
    try:
        if merge_states:
            create_tables(conn, drop_existing=False)
            purged = _purge_states(conn, filters.states)
        elif preserve_non_spuf_tables:
            for table in ("plans", "formulary", "beneficiary_cost", "pricing"):
                conn.execute(f"DROP TABLE IF EXISTS {table}")
            create_tables(conn, drop_existing=False)
            purged = 0
        else:
            create_tables(conn, drop_existing=True)
            purged = 0

        plans: dict[str, dict[str, Any]] = {}
        formulary_ids: set[str] = set()

        for row in _iter_rows(source, files["plan"]):
            if not _plan_in_filter(row, filters):
                continue
            contract_id = row["CONTRACT_ID"].strip()
            plan_id = row["PLAN_ID"].strip()
            plan_key = f"{contract_id}-{plan_id}"
            formulary_id = row.get("FORMULARY_ID", "").strip()
            state = row.get("STATE", "").strip().upper()
            if contract_id.startswith("S"):
                for st, code in filters.pdp_region_codes.items():
                    if row.get("PDP_REGION_CODE", "").strip() == code:
                        state = st
                        break

            plans[plan_key] = {
                "plan_key": plan_key,
                "contract_id": contract_id,
                "plan_id": plan_id,
                "plan_name": row.get("PLAN_NAME", "").strip(),
                "plan_type": _plan_type(contract_id),
                "state": state,
                "deductible": _parse_float(row.get("DEDUCTIBLE")) or 0.0,
                "contract_year": filters.contract_year,
                "formulary_id": formulary_id,
            }
            if formulary_id:
                formulary_ids.add(formulary_id)

        for plan in plans.values():
            conn.execute(
                """
                INSERT INTO plans VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    plan["plan_key"],
                    plan["contract_id"],
                    plan["plan_id"],
                    plan["plan_name"],
                    plan["plan_type"],
                    plan["state"],
                    plan["deductible"],
                    plan["contract_year"],
                    plan["formulary_id"],
                ],
            )

        # formulary_id -> list of (ndc, rxcui, tier)
        formulary_drugs: dict[str, list[tuple[str, str, int]]] = {}
        for row in _iter_rows(source, files["formulary"]):
            fid = row.get("FORMULARY_ID", "").strip()
            if fid not in formulary_ids:
                continue
            year = _parse_int(row.get("CONTRACT_YEAR"))
            if year is not None and year != filters.contract_year:
                continue
            ndc_raw = row.get("NDC", "").strip()
            if not ndc_raw:
                continue
            try:
                ndc = normalize_ndc(ndc_raw)
            except ValueError:
                continue
            tier = _parse_int(row.get("TIER_LEVEL_VALUE"))
            if tier is None:
                continue
            rxcui = row.get("RXCUI", "").strip()
            formulary_drugs.setdefault(fid, []).append((ndc, rxcui, tier))

        # plan_key -> tier -> cost shares by channel (initial coverage, 30-day preferred)
        tier_costs: dict[str, dict[int, dict[str, dict[str, Any]]]] = {}
        if files.get("beneficiary_cost"):
            for row in _iter_rows(source, files["beneficiary_cost"]):
                contract_id = row.get("CONTRACT_ID", "").strip()
                plan_id = row.get("PLAN_ID", "").strip()
                plan_key = f"{contract_id}-{plan_id}"
                if plan_key not in plans:
                    continue
                for share in _extract_cost_shares(row):
                    # Prefer initial coverage (1) and 30-day supply for default tier cost
                    if share["coverage_level"] not in (0, 1):
                        continue
                    if share["days_supply"] != 1:
                        continue
                    tier_costs.setdefault(plan_key, {}).setdefault(share["tier"], {})[
                        share["pharmacy_channel"]
                    ] = share

                    conn.execute(
                        """
                        INSERT INTO beneficiary_cost VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        [
                            plan_key,
                            share["tier"],
                            share["coverage_level"],
                            share["days_supply"],
                            share["pharmacy_channel"],
                            share["cost_type"],
                            share["copay"],
                            share["coinsurance_pct"],
                        ],
                    )

        default_channel = "preferred_retail"
        for plan_key, plan in plans.items():
            fid = plan["formulary_id"]
            if not fid or fid not in formulary_drugs:
                continue
            plan_tiers = tier_costs.get(plan_key, {})
            for ndc, rxcui, tier in formulary_drugs[fid]:
                share = plan_tiers.get(tier, {}).get(default_channel)
                if share:
                    cost_type = share["cost_type"]
                    copay = share["copay"]
                    coinsurance = share["coinsurance_pct"]
                else:
                    cost_type, copay, coinsurance = "unknown", None, None

                conn.execute(
                    """
                    INSERT INTO formulary VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        plan_key,
                        format_ndc_display(ndc),
                        rxcui or None,
                        tier,
                        copay,
                        coinsurance,
                        cost_type,
                        default_channel,
                        as_of,
                    ],
                )

        if files.get("pricing"):
            for row in _iter_rows(source, files["pricing"]):
                contract_id = row.get("CONTRACT_ID", "").strip()
                plan_id = row.get("PLAN_ID", "").strip()
                plan_key = f"{contract_id}-{plan_id}"
                if plan_key not in plans:
                    continue
                ndc_raw = row.get("NDC", "").strip()
                if not ndc_raw:
                    continue
                try:
                    ndc = format_ndc_display(normalize_ndc(ndc_raw))
                except ValueError:
                    continue
                days_supply = _parse_int(row.get("DAYS_SUPPLY")) or 30
                unit_cost = _parse_float(row.get("UNIT_COST"))
                if unit_cost is None:
                    continue
                conn.execute(
                    "INSERT INTO pricing VALUES (?, ?, ?, ?)",
                    [plan_key, ndc, days_supply, unit_cost],
                )

        create_indexes(conn)

        stats = {
            "plans": len(plans),
            "plans_purged": purged,
            "formulary_ids": len(formulary_ids),
            "formulary_rows": conn.execute("SELECT COUNT(*) FROM formulary").fetchone()[0],
            "beneficiary_cost_rows": conn.execute(
                "SELECT COUNT(*) FROM beneficiary_cost"
            ).fetchone()[0],
            "total_plans": conn.execute("SELECT COUNT(*) FROM plans").fetchone()[0],
        }
    finally:
        conn.close()

    manifest_states = list(filters.states)
    if merge_states:
        existing = load_manifest().get("spuf", {})
        if isinstance(existing, dict) and existing.get("states"):
            manifest_states = sorted(set(existing["states"]) | set(filters.states))

    manifest = merge_manifest(
        {
            "spuf": {
                "version": version,
                "as_of": as_of,
                "source_id": source_id,
                "contract_year": filters.contract_year,
                "states": manifest_states,
            },
            "benefit_params": {"contract_year": filters.contract_year, "as_of": as_of},
        }
    )
    return {"stats": stats, "manifest": manifest, "source_id": source_id, "as_of": as_of}
