import zipfile
from io import BytesIO
from pathlib import Path

import pytest

from medicare_navigator.config import settings
from medicare_navigator.ingestion.bulk_load import (
    estimate_materialized_bytes,
    has_staging_disk_for,
    materialize_spuf_member,
    release_staging_member,
    resolve_ingest_memory_limit,
    resolve_ingest_plan_tier,
)


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    return tmp_path


def _write_nested_spuf_zip(dest: Path) -> Path:
    inner_txt = (
        "CONTRACT_ID|PLAN_ID|NPI|RETAIL_YN|MAIL_YN\n"
        "S9999|001|1234567890|Y|N\n"
    ).encode("utf-8")
    inner_buf = BytesIO()
    with zipfile.ZipFile(inner_buf, "w") as inner_zf:
        inner_zf.writestr("pharmacy network file.txt", inner_txt)
    outer_path = dest / "spuf.zip"
    with zipfile.ZipFile(outer_path, "w") as outer_zf:
        outer_zf.writestr("pharmacy networks file  PPUF_2026Q2 part 1.zip", inner_buf.getvalue())
    return outer_path


def test_materialize_nested_zip_member(data_dir):
    source = _write_nested_spuf_zip(data_dir)
    member = "pharmacy networks file  PPUF_2026Q2 part 1.zip"
    path = materialize_spuf_member(source, member)
    assert path.is_file()
    assert "1234567890" in path.read_text(encoding="utf-8")


def test_release_staging_member_deletes_materialized_file(data_dir):
    source = _write_nested_spuf_zip(data_dir)
    member = "pharmacy networks file  PPUF_2026Q2 part 1.zip"
    path = materialize_spuf_member(source, member)
    assert path.is_file()
    release_staging_member(member)
    assert not path.is_file()


def test_estimate_materialized_bytes_for_nested_zip(data_dir):
    source = _write_nested_spuf_zip(data_dir)
    member = "pharmacy networks file  PPUF_2026Q2 part 1.zip"
    assert estimate_materialized_bytes(source, member) > 0


def test_resolve_ingest_memory_limit_defaults_to_starter(monkeypatch):
    monkeypatch.delenv("INGEST_MEMORY_LIMIT", raising=False)
    monkeypatch.delenv("INGEST_PLAN", raising=False)
    monkeypatch.setattr(
        "medicare_navigator.ingestion.bulk_load.resolve_ingest_plan_tier",
        lambda: "starter",
    )
    assert resolve_ingest_memory_limit() == "320MB"


def test_resolve_ingest_memory_limit_standard_tier(monkeypatch):
    monkeypatch.delenv("INGEST_MEMORY_LIMIT", raising=False)
    monkeypatch.setenv("INGEST_PLAN", "standard")
    assert resolve_ingest_memory_limit() == "1.2GB"


def test_resolve_ingest_memory_limit_env_override(monkeypatch):
    monkeypatch.setenv("INGEST_MEMORY_LIMIT", "900MB")
    assert resolve_ingest_memory_limit() == "900MB"


def test_resolve_ingest_plan_tier_reads_deploy_yaml(tmp_path, monkeypatch):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "deploy.yaml").write_text("render:\n  plan: standard\n", encoding="utf-8")
    monkeypatch.delenv("INGEST_PLAN", raising=False)
    monkeypatch.setattr(settings, "project_root", tmp_path)
    assert resolve_ingest_plan_tier() == "standard"


def test_has_staging_disk_for_false_when_volume_too_small(data_dir, monkeypatch):
    source = _write_nested_spuf_zip(data_dir)
    member = "pharmacy networks file  PPUF_2026Q2 part 1.zip"
    needed = estimate_materialized_bytes(source, member)
    monkeypatch.setattr(
        "medicare_navigator.ingestion.bulk_load._data_dir_free_bytes",
        lambda: needed,
    )
    assert has_staging_disk_for(source, member) is False
