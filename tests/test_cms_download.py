import json
from pathlib import Path

import httpx
import pytest

from medicare_navigator.ingestion.cms_download import (
    MONTHLY_PUF_TITLE,
    QUARTERLY_SPUF_TITLE,
    _version_from_url,
    download_spuf,
    find_dataset_by_title,
    resolve_spuf_download,
)

MOCK_CATALOG = {
    "dataset": [
        {
            "title": QUARTERLY_SPUF_TITLE,
            "distribution": [
                {
                    "mediaType": "application/zip",
                    "downloadURL": "https://data.cms.gov/example/SPUF_2026_20260408.zip",
                    "temporal": "2026-01-01/2026-03-31",
                },
                {
                    "mediaType": "application/zip",
                    "downloadURL": "https://data.cms.gov/example/SPUF_2025_20250408.zip",
                    "temporal": "2025-01-01/2025-03-31",
                },
            ],
        },
        {
            "title": MONTHLY_PUF_TITLE,
            "distribution": [
                {
                    "mediaType": "application/zip",
                    "downloadURL": "https://data.cms.gov/example/2026_20260610.zip",
                    "temporal": "2026-06-01/2026-06-30",
                }
            ],
        },
    ]
}


def test_version_from_url_spuf_format():
    assert _version_from_url("https://x/SPUF_2026_20260408.zip") == "SPUF.2026.20260408"


def test_version_from_url_monthly_format():
    assert _version_from_url("https://x/2026_20260610.zip") == "PUF.2026.20260610"


def test_find_dataset_by_title():
    ds = find_dataset_by_title(MOCK_CATALOG, QUARTERLY_SPUF_TITLE)
    assert ds is not None
    assert len(ds["distribution"]) == 2


def test_resolve_spuf_download_quarterly():
    distro = resolve_spuf_download(quarterly=True, catalog=MOCK_CATALOG)
    assert distro.title == QUARTERLY_SPUF_TITLE
    assert distro.version_label == "SPUF.2026.20260408"
    assert "SPUF_2026_20260408.zip" in distro.download_url


def test_resolve_spuf_download_contract_year_filter():
    distro = resolve_spuf_download(quarterly=True, contract_year=2025, catalog=MOCK_CATALOG)
    assert "2025" in distro.download_url


def test_resolve_spuf_download_monthly():
    distro = resolve_spuf_download(quarterly=False, catalog=MOCK_CATALOG)
    assert distro.title == MONTHLY_PUF_TITLE
    assert distro.version_label == "PUF.2026.20260610"


def test_download_spuf_uses_cache(tmp_path, monkeypatch):
    raw_dir = tmp_path / "raw"
    zip_name = "SPUF_2026_20260408.zip"
    cached = raw_dir / zip_name
    raw_dir.mkdir()
    cached.write_bytes(b"cached-zip-content")

    def fake_resolve(**kwargs):
        return resolve_spuf_download(catalog=MOCK_CATALOG, **kwargs)

    monkeypatch.setattr(
        "medicare_navigator.ingestion.cms_download.resolve_spuf_download",
        fake_resolve,
    )

    calls = {"download": 0}

    def fake_download(url, dest, **kwargs):
        calls["download"] += 1
        dest.write_bytes(b"should-not-run")
        return dest

    monkeypatch.setattr(
        "medicare_navigator.ingestion.cms_download.download_file",
        fake_download,
    )

    path, distro = download_spuf(dest_dir=raw_dir, use_cache=True)
    assert path == cached
    assert path.read_bytes() == b"cached-zip-content"
    assert calls["download"] == 0
    assert distro.version_label == "SPUF.2026.20260408"


@pytest.mark.integration
def test_resolve_live_cms_catalog():
    """Optional live check — run with pytest -m integration."""
    distro = resolve_spuf_download(quarterly=True, contract_year=2026)
    assert distro.download_url.startswith("https://")
    assert "zip" in distro.download_url.lower()
