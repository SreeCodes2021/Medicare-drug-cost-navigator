"""Ingest CMS SPUF (Prescription Drug Plan Formulary) data into DuckDB."""

from __future__ import annotations

import csv
import sys
import zipfile
from dataclasses import dataclass
from datetime import date, datetime, timezone
from io import BytesIO, TextIOWrapper
from pathlib import Path
from typing import Any, Iterator

import yaml

from medicare_navigator.config import settings
from medicare_navigator.ingestion.manifest import load_manifest, merge_manifest
from medicare_navigator.ingestion.ndc import format_ndc_display, normalize_ndc
from medicare_navigator.ingestion.schema import create_indexes, create_tables, drop_spuf_indexes
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

_PROGRESS_INTERVAL = 500_000
_WRITE_PARTS = 10
_FORMULARY_WRITE_PARTS = 100

_FORMULARY_INSERT_SQL = """
INSERT INTO basic_drugs_formulary VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""
_BENEFICIARY_COST_INSERT_SQL = """
INSERT INTO beneficiary_cost VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""
_PRICING_INSERT_SQL = "INSERT INTO pricing VALUES (?, ?, ?, ?)"


def _member_label(member: str | Path | None) -> str:
    if member is None:
        return "unknown"
    name = member if isinstance(member, str) else member.name
    return name.rsplit("/", 1)[-1].strip()


def _progress(msg: str, *, file: str | Path | None = None) -> None:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    label = _member_label(file)
    print(f"[{ts}] [{label}] {msg}", file=sys.stderr, flush=True)


def _part_size(total: int, parts: int = _WRITE_PARTS) -> int:
    if total <= 0:
        return 1
    return max(1, (total + parts - 1) // parts)


def _insert_in_parts(
    conn,
    sql: str,
    rows: Iterator[list[Any]],
    total: int,
    *,
    label: str | Path,
    parts: int = _WRITE_PARTS,
) -> int:
    """Insert rows with executemany in ``parts`` batches (default 10)."""
    if total <= 0:
        return 0
    part_size = _part_size(total, parts)
    batch: list[list[Any]] = []
    part_num = 0
    inserted = 0
    for row in rows:
        batch.append(row)
        if len(batch) < part_size:
            continue
        part_num += 1
        conn.executemany(sql, batch)
        inserted += len(batch)
        _progress(
            f"wrote part {part_num}/{parts} ({inserted:,}/{total:,} rows)",
            file=label,
        )
        batch = []
    if batch:
        part_num += 1
        conn.executemany(sql, batch)
        inserted += len(batch)
        _progress(
            f"wrote part {part_num}/{parts} ({inserted:,}/{total:,} rows)",
            file=label,
        )
    return inserted


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
    """Selection filter for which plans to ingest at all (state/plan-type scope).

    Does NOT exclude suppressed plans — PLAN_SUPPRESSED_YN must be persisted and
    surfaced as a hard-stop at query time (spec Bug 6), not silently dropped here.
    """
    contract_id = row.get("CONTRACT_ID", "").strip()
    if not contract_id:
        return False
    if contract_id[0] not in filters.plan_type_prefixes:
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


def _parse_bool_yn(value: str | None) -> bool:
    return (value or "").strip().upper() == "Y"


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
    """Extract every pharmacy-channel cost share row (no coverage-level/days-supply filtering —
    spec Bug 1 requires every code/coverage-level row to survive, since the days-supply value
    here is a CMS CODE (1-4), not a raw day count, and callers must map explicitly."""
    tier = _parse_int(row.get("TIER"))
    if tier is None:
        return []
    coverage_level = _parse_int(row.get("COVERAGE_LEVEL"))
    days_supply_code = _parse_int(row.get("DAYS_SUPPLY"))
    if days_supply_code is None:
        return []
    ded_applies_yn = _parse_bool_yn(row.get("DED_APPLIES_YN"))

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
                "days_supply_code": days_supply_code,
                "pharmacy_channel": channel,
                "cost_type": cost_type,
                "copay": copay,
                "coinsurance_pct": coinsurance,
                "ded_applies_yn": ded_applies_yn,
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
    count = conn.execute(
        f"SELECT COUNT(*) FROM plans WHERE upper(state) IN ({placeholders})",
        normalized,
    ).fetchone()[0]
    if count == 0:
        return 0
    # DuckDB can fail bulk DELETE on indexed tables ("Failed to delete all rows from
    # index"); drop indexes first and recreate them after ingest completes.
    drop_spuf_indexes(conn)
    plan_subquery = f"SELECT plan_key FROM plans WHERE upper(state) IN ({placeholders})"
    for table in ("beneficiary_cost", "pricing"):
        conn.execute(
            f"DELETE FROM {table} WHERE plan_key IN ({plan_subquery})",
            normalized,
        )
    conn.execute(
        f"DELETE FROM plans WHERE upper(state) IN ({placeholders})",
        normalized,
    )
    return count


def _parse_as_of_from_version(version: str) -> str:
    # SPUF.2026.20260115 -> 2026-01-15
    parts = version.replace(".zip", "").split(".")
    if len(parts) >= 3 and len(parts[-1]) == 8 and parts[-1].isdigit():
        d = parts[-1]
        return f"{d[:4]}-{d[4:6]}-{d[6:8]}"
    return date.today().isoformat()


# formulary_id -> version -> list of (ndc, rxcui, tier, ql_yn, ql_amount, ql_days, pa_yn, st_yn)
FormularyRow = tuple[str, str, int, bool, float | None, int | None, bool, bool]


def _select_max_version_rows(
    formulary_by_version: dict[str, dict[str, list[FormularyRow]]],
) -> dict[str, list[FormularyRow]]:
    """Keep only the max FORMULARY_VERSION's rows per formulary_id (spec: avoid stale/duplicate
    historical-version rows polluting the Bug 5 multi-NDC range)."""
    selected: dict[str, list[FormularyRow]] = {}
    for fid, by_version in formulary_by_version.items():
        best_version = max(by_version, key=lambda v: (v.isdigit(), int(v) if v.isdigit() else 0, v))
        selected[fid] = by_version[best_version]
    return selected


def _count_formulary_insert_rows(formulary_drugs: dict[str, list[FormularyRow]]) -> int:
    return sum(len(rows) for rows in formulary_drugs.values())


def _iter_formulary_insert_rows(
    formulary_drugs: dict[str, list[FormularyRow]],
    *,
    as_of: str,
) -> Iterator[list[Any]]:
    for fid, rows in formulary_drugs.items():
        for ndc, rxcui, tier, ql_yn, ql_amount, ql_days, pa_yn, st_yn in rows:
            yield [
                fid,
                format_ndc_display(ndc),
                rxcui or None,
                tier,
                ql_yn,
                ql_amount,
                ql_days,
                pa_yn,
                st_yn,
                as_of,
            ]


def _pricing_insert_row(
    row: dict[str, str],
    plans: dict[str, dict[str, Any]],
) -> list[Any] | None:
    contract_id = row.get("CONTRACT_ID", "").strip()
    plan_id = row.get("PLAN_ID", "").strip()
    plan_key = f"{contract_id}-{plan_id}"
    if plan_key not in plans:
        return None
    ndc_raw = row.get("NDC", "").strip()
    if not ndc_raw:
        return None
    try:
        ndc = format_ndc_display(normalize_ndc(ndc_raw))
    except ValueError:
        return None
    parsed_days_supply = _parse_int(row.get("DAYS_SUPPLY"))
    days_supply = parsed_days_supply if parsed_days_supply is not None else 30
    unit_cost = _parse_float(row.get("UNIT_COST"))
    if unit_cost is None:
        return None
    return [plan_key, ndc, days_supply, unit_cost]


def _count_pricing_rows(
    source: Path,
    member: str | Path | None,
    plans: dict[str, dict[str, Any]],
) -> int:
    count = 0
    for row in _iter_rows(source, member):
        if _pricing_insert_row(row, plans) is not None:
            count += 1
    return count


def _iter_pricing_insert_rows(
    source: Path,
    member: str | Path | None,
    plans: dict[str, dict[str, Any]],
) -> Iterator[list[Any]]:
    for row in _iter_rows(source, member):
        insert_row = _pricing_insert_row(row, plans)
        if insert_row is not None:
            yield insert_row


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
    _progress(
        f"Ingesting SPUF into {db.path} "
        f"(states={','.join(filters.states)}, merge_states={merge_states})...",
        file=source,
    )
    try:
        if merge_states:
            create_tables(conn, drop_existing=False)
            purged = _purge_states(conn, filters.states)
            if purged:
                _progress(f"Purged {purged} existing plan(s) for merge.", file="plans")
        elif preserve_non_spuf_tables:
            for table in ("plans", "basic_drugs_formulary", "beneficiary_cost", "pricing"):
                conn.execute(f"DROP TABLE IF EXISTS {table}")
            create_tables(conn, drop_existing=False)
            purged = 0
        else:
            create_tables(conn, drop_existing=True)
            purged = 0

        plans: dict[str, dict[str, Any]] = {}
        formulary_ids: set[str] = set()

        _progress("Loading plans...", file=files["plan"])
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
                "plan_suppressed": _parse_bool_yn(row.get("PLAN_SUPPRESSED_YN")),
            }
            if formulary_id:
                formulary_ids.add(formulary_id)

        _progress(
            f"Loaded {len(plans)} plan(s), {len(formulary_ids)} formulary id(s).",
            file=files["plan"],
        )

        for plan in plans.values():
            conn.execute(
                """
                INSERT INTO plans VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    plan["plan_suppressed"],
                ],
            )

        # formulary_id -> version -> list of formulary rows (deduped to max version after scan)
        formulary_by_version: dict[str, dict[str, list[FormularyRow]]] = {}
        _progress(
            "Scanning national formulary file (this may take several minutes)...",
            file=files["formulary"],
        )
        formulary_scanned = 0
        for row in _iter_rows(source, files["formulary"]):
            formulary_scanned += 1
            if formulary_scanned % _PROGRESS_INTERVAL == 0:
                matched = sum(
                    len(rows) for by_version in formulary_by_version.values() for rows in by_version.values()
                )
                _progress(
                    f"scanned {formulary_scanned:,} rows, "
                    f"kept {matched:,} for selected plans",
                    file=files["formulary"],
                )
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
            version_str = row.get("FORMULARY_VERSION", "").strip() or "0"
            formulary_row: FormularyRow = (
                ndc,
                rxcui,
                tier,
                _parse_bool_yn(row.get("QUANTITY_LIMIT_YN")),
                _parse_float(row.get("QUANTITY_LIMIT_AMOUNT")),
                _parse_int(row.get("QUANTITY_LIMIT_DAYS")),
                _parse_bool_yn(row.get("PRIOR_AUTHORIZATION_YN")),
                _parse_bool_yn(row.get("STEP_THERAPY_YN")),
            )
            formulary_by_version.setdefault(fid, {}).setdefault(version_str, []).append(formulary_row)

        formulary_drugs = _select_max_version_rows(formulary_by_version)
        matched_drugs = sum(len(v) for v in formulary_drugs.values())
        _progress(
            f"scan done: {formulary_scanned:,} rows scanned, "
            f"{matched_drugs:,} drug entries kept (max FORMULARY_VERSION per formulary_id).",
            file=files["formulary"],
        )

        beneficiary_cost_rows: list[list[Any]] = []
        if files.get("beneficiary_cost"):
            _progress("Loading beneficiary cost shares...", file=files["beneficiary_cost"])
            for row in _iter_rows(source, files["beneficiary_cost"]):
                contract_id = row.get("CONTRACT_ID", "").strip()
                plan_id = row.get("PLAN_ID", "").strip()
                plan_key = f"{contract_id}-{plan_id}"
                if plan_key not in plans:
                    continue
                for share in _extract_cost_shares(row):
                    beneficiary_cost_rows.append(
                        [
                            plan_key,
                            share["tier"],
                            share["coverage_level"],
                            share["days_supply_code"],
                            share["pharmacy_channel"],
                            share["cost_type"],
                            share["copay"],
                            share["coinsurance_pct"],
                            share["ded_applies_yn"],
                            as_of,
                        ]
                    )
            if beneficiary_cost_rows:
                _progress(
                    f"Inserting {len(beneficiary_cost_rows):,} beneficiary cost row(s) "
                    f"in {_WRITE_PARTS} parts...",
                    file=files["beneficiary_cost"],
                )
                _insert_in_parts(
                    conn,
                    _BENEFICIARY_COST_INSERT_SQL,
                    iter(beneficiary_cost_rows),
                    len(beneficiary_cost_rows),
                    label=files["beneficiary_cost"],
                )

        formulary_total = _count_formulary_insert_rows(formulary_drugs)
        _progress(
            f"Inserting {formulary_total:,} basic_drugs_formulary row(s) into DuckDB "
            f"in {_FORMULARY_WRITE_PARTS} parts...",
            file="basic_drugs_formulary",
        )
        formulary_inserted = _insert_in_parts(
            conn,
            _FORMULARY_INSERT_SQL,
            _iter_formulary_insert_rows(formulary_drugs, as_of=as_of),
            formulary_total,
            label="basic_drugs_formulary",
            parts=_FORMULARY_WRITE_PARTS,
        )
        _progress(f"inserted {formulary_inserted:,} basic_drugs_formulary row(s).", file="basic_drugs_formulary")

        if files.get("pricing"):
            _progress("Counting matching pricing rows...", file=files["pricing"])
            pricing_total = _count_pricing_rows(source, files["pricing"], plans)
            _progress(
                f"Inserting {pricing_total:,} pricing row(s) in {_WRITE_PARTS} parts "
                "(scanning pricing file; may take 20–40 min on Starter)...",
                file=files["pricing"],
            )
            pricing_inserted = _insert_in_parts(
                conn,
                _PRICING_INSERT_SQL,
                _iter_pricing_insert_rows(source, files["pricing"], plans),
                pricing_total,
                label=files["pricing"],
            )
            _progress(f"inserted {pricing_inserted:,} pricing row(s).", file=files["pricing"])

        _progress("Creating indexes...", file="navigator.duckdb")
        create_indexes(conn)

        stats = {
            "plans": len(plans),
            "plans_purged": purged,
            "formulary_ids": len(formulary_ids),
            "formulary_rows": conn.execute("SELECT COUNT(*) FROM basic_drugs_formulary").fetchone()[0],
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
        }
    )
    return {"stats": stats, "manifest": manifest, "source_id": source_id, "as_of": as_of}
