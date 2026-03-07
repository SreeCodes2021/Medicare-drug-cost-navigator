"""Download CMS SPUF/PUF datasets from the data.cms.gov catalog API."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from medicare_navigator.config import settings

CMS_DATA_CATALOG_URL = "https://data.cms.gov/data.json"

QUARTERLY_SPUF_TITLE = (
    "Quarterly Prescription Drug Plan Formulary, Pharmacy Network, and Pricing Information"
)
MONTHLY_PUF_TITLE = "Monthly Prescription Drug Plan Formulary and Pharmacy Network Information"


@dataclass
class CmsDistribution:
    title: str
    download_url: str
    media_type: str
    temporal: str | None
    version_label: str


def _version_from_url(url: str) -> str:
    """Derive a manifest version label from the CMS download URL or filename."""
    name = url.rsplit("/", 1)[-1]
    stem = name.removesuffix(".zip")
    # SPUF_2026_20260408 -> SPUF.2026.20260408
    match = re.match(r"^SPUF[_\.](\d{4})[_\.](\d{8})$", stem, re.I)
    if match:
        return f"SPUF.{match.group(1)}.{match.group(2)}"
    match = re.match(r"^(\d{4})[_\.](\d{8})$", stem)
    if match:
        return f"PUF.{match.group(1)}.{match.group(2)}"
    return stem


def _pick_zip_distributions(dataset: dict[str, Any]) -> list[dict[str, Any]]:
    distros = dataset.get("distribution") or []
    zips = [d for d in distros if d.get("mediaType") == "application/zip" and d.get("downloadURL")]
    return zips


def find_dataset_by_title(catalog: dict[str, Any], title: str) -> dict[str, Any] | None:
    for dataset in catalog.get("dataset", []):
        if dataset.get("title") == title:
            return dataset
    return None


def resolve_spuf_download(
    *,
    quarterly: bool = True,
    contract_year: int | None = None,
    catalog: dict[str, Any] | None = None,
    client: httpx.Client | None = None,
) -> CmsDistribution:
    """Resolve the latest CMS zip download URL from data.cms.gov/data.json."""
    title = QUARTERLY_SPUF_TITLE if quarterly else MONTHLY_PUF_TITLE

    if catalog is None:
        own_client = client is None
        client = client or httpx.Client(timeout=60.0, follow_redirects=True)
        try:
            response = client.get(CMS_DATA_CATALOG_URL)
            response.raise_for_status()
            catalog = response.json()
        finally:
            if own_client:
                client.close()

    dataset = find_dataset_by_title(catalog, title)
    if not dataset:
        raise LookupError(f"Dataset not found in CMS catalog: {title!r}")

    zips = _pick_zip_distributions(dataset)
    if not zips:
        raise LookupError(f"No zip distribution found for dataset: {title!r}")

    if contract_year is not None:
        year_prefix = f"SPUF_{contract_year}" if quarterly else str(contract_year)
        year_matches = [
            z for z in zips if year_prefix in z.get("downloadURL", "") or f"_{contract_year}_" in z.get("downloadURL", "")
        ]
        if year_matches:
            zips = year_matches

    latest = zips[0]
    url = latest["downloadURL"]
    return CmsDistribution(
        title=title,
        download_url=url,
        media_type=latest.get("mediaType", "application/zip"),
        temporal=latest.get("temporal"),
        version_label=_version_from_url(url),
    )


def download_file(
    url: str,
    dest: Path,
    *,
    client: httpx.Client | None = None,
    chunk_size: int = 1024 * 1024,
) -> Path:
    """Stream-download a URL to dest. Returns dest path."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    own_client = client is None
    client = client or httpx.Client(timeout=httpx.Timeout(60.0, read=600.0), follow_redirects=True)
    try:
        with client.stream("GET", url) as response:
            response.raise_for_status()
            with dest.open("wb") as f:
                for chunk in response.iter_bytes(chunk_size=chunk_size):
                    f.write(chunk)
    finally:
        if own_client:
            client.close()
    return dest


def download_spuf(
    *,
    quarterly: bool = True,
    contract_year: int | None = None,
    dest_dir: Path | None = None,
    use_cache: bool = True,
    client: httpx.Client | None = None,
) -> tuple[Path, CmsDistribution]:
    """
    Resolve and download the latest CMS SPUF/PUF zip.

    Returns (local_zip_path, distribution_metadata).
    Cached files live under data/raw/ and are reused when use_cache=True.
    """
    dest_dir = dest_dir or (settings.data_dir / "raw")
    own_client = client is None
    client = client or httpx.Client(timeout=httpx.Timeout(60.0, read=600.0), follow_redirects=True)
    try:
        distro = resolve_spuf_download(
            quarterly=quarterly,
            contract_year=contract_year,
            client=client,
        )
        filename = distro.download_url.rsplit("/", 1)[-1]
        dest = dest_dir / filename
        if use_cache and dest.exists() and dest.stat().st_size > 0:
            return dest, distro
        download_file(distro.download_url, dest, client=client)
        return dest, distro
    finally:
        if own_client:
            client.close()
