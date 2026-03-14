"""Ingestion CLI — CMS SPUF load for production and local fixture-based tests."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from medicare_navigator.ingestion.cms_download import download_spuf, resolve_spuf_download
from medicare_navigator.ingestion.spuf import IngestFilters, ingest_spuf


def _apply_state_filter(filters: IngestFilters, states: str | None) -> IngestFilters:
    if not states:
        return filters
    filters.states = [s.upper() for s in states.split(",")]
    filters.pdp_region_codes = {
        k: v for k, v in filters.pdp_region_codes.items() if k in filters.states
    }
    return filters


def _cmd_spuf(args: argparse.Namespace) -> None:
    filters = _apply_state_filter(IngestFilters.from_yaml(), args.states)

    if args.download or not args.source:
        if args.source:
            print("Note: --download ignores --source; fetching from data.cms.gov catalog.", file=sys.stderr)
        print("Resolving latest CMS SPUF download URL from data.cms.gov...")
        zip_path, distro = download_spuf(
            quarterly=not args.monthly,
            contract_year=filters.contract_year,
            use_cache=not args.force_download,
        )
        source = zip_path
        version = args.version or distro.version_label
        print(f"Using {distro.title}")
        print(f"Downloaded: {zip_path}")
        if distro.temporal:
            print(f"Coverage period: {distro.temporal}")
    else:
        source = Path(args.source)
        if not source.exists():
            print(f"SPUF source not found: {source}", file=sys.stderr)
            sys.exit(1)
        version = args.version or f"SPUF.{filters.contract_year}.local"

    result = ingest_spuf(
        source,
        filters=filters,
        version=version,
        preserve_non_spuf_tables=args.preserve_other,
    )
    stats = result["stats"]
    print(f"SPUF ingestion complete: {stats['plans']} plans, {stats['formulary_rows']} formulary rows.")
    print(f"Manifest as_of: {result['as_of']} (source_id={result['source_id']})")


def _cmd_fetch(args: argparse.Namespace) -> None:
    """Download CMS zip only (no DuckDB ingest)."""
    distro = resolve_spuf_download(
        quarterly=not args.monthly,
        contract_year=args.contract_year,
    )
    print(f"Latest: {distro.title}")
    print(f"URL: {distro.download_url}")
    if args.print_url_only:
        return
    zip_path, _ = download_spuf(
        quarterly=not args.monthly,
        contract_year=args.contract_year,
        use_cache=not args.force_download,
    )
    print(f"Saved to: {zip_path}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Medicare navigator CMS data ingestion",
        epilog="Production: medicare-ingest spuf --download",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    spuf_parser = sub.add_parser("spuf", help="Ingest CMS SPUF (auto-download or local path)")
    spuf_parser.add_argument(
        "--source",
        help="Local path to SPUF .zip or directory (optional if --download)",
    )
    spuf_parser.add_argument(
        "--download",
        action="store_true",
        help="Fetch latest zip from data.cms.gov catalog API (default when --source omitted)",
    )
    spuf_parser.add_argument(
        "--monthly",
        action="store_true",
        help="Use monthly PUF (no pricing file) instead of quarterly SPUF",
    )
    spuf_parser.add_argument(
        "--force-download",
        action="store_true",
        help="Re-download even if cached under data/raw/",
    )
    spuf_parser.add_argument(
        "--states",
        help="Comma-separated state codes (default from config/ingest_filters.yaml)",
    )
    spuf_parser.add_argument(
        "--version",
        help="SPUF version label for manifest (default: from CMS filename)",
    )
    spuf_parser.add_argument(
        "--preserve-other",
        action="store_true",
        help="Keep cost_trends, alternatives, policy tables when reloading SPUF",
    )
    spuf_parser.set_defaults(func=_cmd_spuf)

    fetch_parser = sub.add_parser("fetch", help="Download CMS SPUF zip to data/raw/ without ingesting")
    fetch_parser.add_argument(
        "--monthly",
        action="store_true",
        help="Use monthly PUF instead of quarterly SPUF",
    )
    fetch_parser.add_argument(
        "--contract-year",
        type=int,
        help="Prefer a zip for this contract year when multiple are listed",
    )
    fetch_parser.add_argument(
        "--force-download",
        action="store_true",
        help="Re-download even if cached",
    )
    fetch_parser.add_argument(
        "--print-url-only",
        action="store_true",
        help="Print resolved download URL only; do not download",
    )
    fetch_parser.set_defaults(func=_cmd_fetch)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
