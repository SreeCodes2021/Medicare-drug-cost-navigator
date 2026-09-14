"""DuckDB bulk loaders for large CMS SPUF pipe-delimited files."""

from __future__ import annotations

import logging
import os
import re
import shutil
import zipfile
from pathlib import Path

import yaml

from medicare_navigator.config import settings

log = logging.getLogger(__name__)

_BULK_LOAD_MIN_BYTES = 100_000
# Leave headroom on the persistent disk for DuckDB WAL/checkpoints during bulk SQL.
_STAGING_HEADROOM_BYTES = 512 * 1024 * 1024
_READ_CSV_OPTS = "delim='|', header=true, all_varchar=true, ignore_errors=true"


def _member_label(member: str | Path | None) -> str:
    if member is None:
        return "unknown"
    name = member if isinstance(member, str) else member.name
    return name.rsplit("/", 1)[-1].strip()


def _progress(msg: str, *, file: str | Path | None = None) -> None:
    from medicare_navigator.ingestion.spuf import _progress as spuf_progress

    spuf_progress(msg, file=file)


def _find_txt_in_zip_names(names: list[str]) -> str | None:
    for name in names:
        if name.lower().endswith(".txt"):
            return name
    return None


_INGEST_MEMORY_BY_PLAN: dict[str, str] = {
    "free": "256MB",
    "starter": "320MB",
    "standard": "1.2GB",
    "pro": "1.5GB",
    "pro_plus": "2GB",
}


def resolve_ingest_plan_tier() -> str:
    """Render/compute tier for ingest tuning (INGEST_PLAN env > deploy.yaml > starter)."""
    explicit = os.environ.get("INGEST_PLAN", "").strip().lower()
    if explicit:
        return explicit
    deploy_path = settings.config_dir / "deploy.yaml"
    if deploy_path.is_file():
        try:
            data = yaml.safe_load(deploy_path.read_text(encoding="utf-8")) or {}
            render_plan = (data.get("render") or {}).get("plan")
            if render_plan:
                return str(render_plan).strip().lower()
        except (OSError, yaml.YAMLError):
            pass
    return "starter"


def resolve_ingest_memory_limit() -> str:
    """DuckDB memory_limit for ingest; override with INGEST_MEMORY_LIMIT."""
    override = os.environ.get("INGEST_MEMORY_LIMIT", "").strip()
    if override:
        return override
    tier = resolve_ingest_plan_tier()
    if tier.startswith("2c"):
        return "1.5GB"
    return _INGEST_MEMORY_BY_PLAN.get(tier, "1.2GB")


def resolve_ingest_threads() -> int:
    """Keep ingest single-threaded so cron jobs do not peg multi-core hosts."""
    raw = os.environ.get("INGEST_THREADS", "1").strip()
    try:
        return max(1, int(raw))
    except ValueError:
        return 1


def configure_ingest_connection(conn) -> None:
    """Tune DuckDB for ingest inside the API container (plan-aware limits)."""
    temp_dir = settings.data_dir / "duckdb_temp"
    temp_dir.mkdir(parents=True, exist_ok=True)
    conn.execute(f"SET temp_directory TO '{temp_dir.as_posix()}'")
    # Spill to the persistent data volume (not the container rootfs).
    conn.execute("SET max_temp_directory_size TO '4GiB'")
    threads = resolve_ingest_threads()
    memory_limit = resolve_ingest_memory_limit()
    conn.execute(f"SET threads TO {threads}")
    conn.execute("SET preserve_insertion_order TO false")
    conn.execute(f"SET memory_limit TO '{memory_limit}'")
    log.info(
        "DuckDB ingest tuning: plan=%s memory_limit=%s threads=%s",
        resolve_ingest_plan_tier(),
        memory_limit,
        threads,
    )


def _staging_path(member: str | Path) -> Path:
    safe = re.sub(r"[^\w.-]+", "_", _member_label(member))
    return settings.data_dir / "staging" / f"{safe}.txt"


def _data_dir_free_bytes() -> int:
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    return shutil.disk_usage(settings.data_dir).free


def estimate_materialized_bytes(source: Path, member: str | Path) -> int | None:
    """Estimate uncompressed pipe-delimited bytes needed to stage one archive member."""
    if isinstance(member, Path):
        if member.is_file():
            return member.stat().st_size
        candidate = source / member if source.is_dir() else member
        return candidate.stat().st_size if candidate.is_file() else None

    member_str = str(member)
    if source.is_dir():
        path = source / member_str
        return path.stat().st_size if path.is_file() else None

    with zipfile.ZipFile(source) as zf:
        if member_str.lower().endswith(".zip"):
            with zf.open(member_str) as nested_raw:
                with zipfile.ZipFile(nested_raw) as inner_zf:
                    inner_member = _find_txt_in_zip_names(inner_zf.namelist())
                    if inner_member:
                        return inner_zf.getinfo(inner_member).file_size
            return zf.getinfo(member_str).file_size
        return zf.getinfo(member_str).file_size


def has_staging_disk_for(source: Path, member: str | Path) -> bool:
    """Return False when staging would likely exhaust the data volume (e.g. Render /data)."""
    try:
        needed = estimate_materialized_bytes(source, member)
    except (KeyError, FileNotFoundError, OSError, zipfile.BadZipFile):
        return False
    if needed is None:
        return False
    return _data_dir_free_bytes() >= needed + _STAGING_HEADROOM_BYTES


def release_staging_member(member: str | Path) -> None:
    """Delete a staged .txt extracted for DuckDB bulk load."""
    path = _staging_path(member)
    if path.is_file():
        path.unlink()


def materialize_spuf_member(source: Path, member: str | Path) -> Path:
    """Return a local pipe-delimited file path suitable for DuckDB read_csv."""
    if isinstance(member, Path):
        if member.is_file():
            return member
        candidate = source / member if source.is_dir() else member
        if candidate.is_file():
            return candidate

    member_str = str(member)
    dest = _staging_path(member)
    dest.parent.mkdir(parents=True, exist_ok=True)

    if source.is_dir():
        path = source / member_str
        if not path.is_file():
            raise FileNotFoundError(f"SPUF member not found under {source}: {member_str}")
        if dest.exists() and dest.stat().st_mtime >= path.stat().st_mtime:
            return dest
        shutil.copy2(path, dest)
        return dest

    with zipfile.ZipFile(source) as zf:
        if member_str.lower().endswith(".zip"):
            with zf.open(member_str) as nested_raw:
                with zipfile.ZipFile(nested_raw) as inner_zf:
                    inner_member = _find_txt_in_zip_names(inner_zf.namelist())
                    if not inner_member:
                        raise FileNotFoundError(f"No .txt member in nested zip {member_str}")
                    with inner_zf.open(inner_member) as raw, dest.open("wb") as out:
                        shutil.copyfileobj(raw, out)
        else:
            with zf.open(member_str) as raw, dest.open("wb") as out:
                shutil.copyfileobj(raw, out)
    return dest


def _staging_columns(conn, path: Path) -> set[str]:
    rows = conn.execute(
        f"DESCRIBE SELECT * FROM read_csv('{path.as_posix()}', {_READ_CSV_OPTS})"
    ).fetchall()
    return {str(row[0]).upper() for row in rows}


def _bool_yn_sql(expr: str) -> str:
    return f"upper(trim(coalesce({expr}, ''))) = 'Y'"


def _first_present_sql(columns: set[str], names: tuple[str, ...]) -> str | None:
    present = [name for name in names if name in columns]
    if not present:
        return None
    return "coalesce(" + ", ".join(f"nullif(trim(s.{name}), '')" for name in present) + ")"


def use_bulk_load(path: Path) -> bool:
    return path.is_file() and path.stat().st_size >= _BULK_LOAD_MIN_BYTES


def bulk_insert_pharmacy_network_part(
    conn,
    staged_path: Path,
    pharmacy_member: str | Path,
    *,
    as_of: str,
    part_index: int,
    total_parts: int,
) -> tuple[int, dict[str, str], set[str]]:
    """Bulk-load one pharmacy-network file; return (rows, npi->zip, npi set)."""
    path = staged_path
    columns = _staging_columns(conn, path)
    identifier_sql = _first_present_sql(
        columns,
        ("NPI", "PHARMACY_NPI", "PROVIDER_ID", "PHARMACY_NUMBER"),
    )
    if identifier_sql is None:
        raise ValueError(f"Pharmacy network file missing identifier columns: {path.name}")

    zip_sql = _first_present_sql(columns, ("PHARMACY_ZIPCODE", "PHARMACY_ZIP"))
    zip_expr = "NULL"
    if zip_sql:
        zip_expr = (
            f"CASE WHEN length(regexp_replace({zip_sql}, '[^0-9]', '', 'g')) >= 5 "
            f"THEN left(lpad(regexp_replace({zip_sql}, '[^0-9]', '', 'g'), 5, '0'), 5) "
            f"ELSE NULL END"
        )

    has_cms_layout = "PHARMACY_RETAIL" in columns or "PHARMACY_MAIL" in columns
    if has_cms_layout:
        retail_yn = _bool_yn_sql("s.PHARMACY_RETAIL")
        mail_yn = _bool_yn_sql("s.PHARMACY_MAIL")
        preferred_yn = (
            f"(({_bool_yn_sql('s.PREFERRED_STATUS_RETAIL')} AND {retail_yn}) "
            f"OR ({_bool_yn_sql('s.PREFERRED_STATUS_MAIL')} AND {mail_yn}))"
        )
        ltc_yn = "FALSE"
        home_infusion_yn = "FALSE"
        channel_filter = f"({retail_yn} OR {mail_yn})"
    else:
        if "PREFERRED_YN" in columns:
            preferred_yn = _bool_yn_sql("s.PREFERRED_YN")
        elif "NTWRK_TYPE" in columns:
            preferred_yn = _bool_yn_sql("s.NTWRK_TYPE")
        else:
            preferred_yn = "FALSE"
        if "RETAIL_YN" in columns:
            retail_yn = _bool_yn_sql("s.RETAIL_YN")
        elif "PHARMACY_RETAIL" in columns:
            retail_yn = _bool_yn_sql("s.PHARMACY_RETAIL")
        else:
            retail_yn = "FALSE"
        if "MAIL_YN" in columns:
            mail_yn = _bool_yn_sql("s.MAIL_YN")
        elif "PHARMACY_MAIL" in columns:
            mail_yn = _bool_yn_sql("s.PHARMACY_MAIL")
        else:
            mail_yn = "FALSE"
        ltc_yn = _bool_yn_sql("s.LTC_YN") if "LTC_YN" in columns else "FALSE"
        home_infusion_yn = (
            _bool_yn_sql("s.HOME_INFUSION_YN") if "HOME_INFUSION_YN" in columns else "FALSE"
        )
        channel_filter = "TRUE"

    csv_from = f"read_csv('{path.as_posix()}', {_READ_CSV_OPTS})"
    parsed_rows = f"""
        SELECT
            trim(s.CONTRACT_ID) AS contract_id,
            trim(s.PLAN_ID) AS plan_id,
            {identifier_sql} AS identifier,
            {zip_expr} AS zip_code,
            s.*
        FROM {csv_from} s
    """
    before = conn.execute("SELECT COUNT(*) FROM pharmacy_network").fetchone()[0]
    conn.execute(
        f"""
        INSERT INTO pharmacy_network
        SELECT
            p.plan_key,
            s.identifier,
            {preferred_yn},
            {retail_yn},
            {mail_yn},
            {ltc_yn},
            {home_infusion_yn},
            ?
        FROM ({parsed_rows}) s
        INNER JOIN plans p ON p.plan_key = s.contract_id || '-' || s.plan_id
        WHERE s.identifier IS NOT NULL
          AND {channel_filter}
        """,
        [as_of],
    )
    after = conn.execute("SELECT COUNT(*) FROM pharmacy_network").fetchone()[0]
    row_count = after - before

    zip_rows = conn.execute(
        f"""
        SELECT DISTINCT identifier, zip_code
        FROM ({parsed_rows}) s
        INNER JOIN plans p ON p.plan_key = s.contract_id || '-' || s.plan_id
        WHERE s.identifier IS NOT NULL
          AND s.zip_code IS NOT NULL
          AND {channel_filter}
        """
    ).fetchall()
    zip_by_npi = {str(row[0]): str(row[1]) for row in zip_rows}
    npi_rows = conn.execute(
        f"""
        SELECT DISTINCT s.identifier
        FROM ({parsed_rows}) s
        INNER JOIN plans p ON p.plan_key = s.contract_id || '-' || s.plan_id
        WHERE s.identifier IS NOT NULL
          AND {channel_filter}
        """
    ).fetchall()
    npis = {str(row[0]) for row in npi_rows}

    _progress(
        f"finished part {part_index}/{total_parts}: {row_count:,} pharmacy network row(s) "
        f"(bulk SQL).",
        file=pharmacy_member,
    )
    return row_count, zip_by_npi, npis


def _format_ndc_sql(ndc_expr: str) -> str:
    digits = f"regexp_replace(trim(coalesce({ndc_expr}, '')), '[^0-9]', '', 'g')"
    return (
        f"CASE WHEN length({digits}) = 11 THEN "
        f"substring({digits}, 1, 5) || '-' || substring({digits}, 6, 4) || '-' || "
        f"substring({digits}, 10, 2) "
        f"ELSE NULL END"
    )


def bulk_insert_pricing(
    conn,
    staged_path: Path,
    pricing_member: str | Path,
) -> int:
    """Bulk-load pricing rows joined to ingested plans."""
    path = staged_path
    csv_from = f"read_csv('{path.as_posix()}', {_READ_CSV_OPTS})"
    ndc_display = _format_ndc_sql("s.NDC")
    days_supply = (
        "CASE WHEN try_cast(trim(s.DAYS_SUPPLY) AS INTEGER) IS NOT NULL "
        "THEN try_cast(trim(s.DAYS_SUPPLY) AS INTEGER) ELSE 30 END"
    )
    before = conn.execute("SELECT COUNT(*) FROM pricing").fetchone()[0]
    conn.execute(
        f"""
        INSERT INTO pricing
        SELECT
            p.plan_key,
            {ndc_display},
            {days_supply},
            try_cast(trim(s.UNIT_COST) AS DOUBLE)
        FROM {csv_from} s
        INNER JOIN plans p ON p.plan_key = trim(s.CONTRACT_ID) || '-' || trim(s.PLAN_ID)
        WHERE {ndc_display} IS NOT NULL
          AND try_cast(trim(s.UNIT_COST) AS DOUBLE) IS NOT NULL
        """
    )
    after = conn.execute("SELECT COUNT(*) FROM pricing").fetchone()[0]
    row_count = after - before
    _progress(f"inserted {row_count:,} pricing row(s) (bulk SQL).", file=pricing_member)
    return row_count
