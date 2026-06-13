from __future__ import annotations

import asyncio
from pathlib import Path

import duckdb
import pytest

from medicare_navigator.storage.connection import DuckDBConnection
from medicare_navigator.storage.repository import PlanRepository
from medicare_navigator.tools.lookup_plan import lookup_plan


@pytest.fixture
def empty_duckdb(monkeypatch, tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    db_path = data_dir / "navigator.duckdb"
    duckdb.connect(str(db_path)).close()
    monkeypatch.setenv("DATA_DIR", str(data_dir))
    monkeypatch.setenv("DUCKDB_PATH", str(db_path))
    from medicare_navigator.config import settings

    settings.data_dir = data_dir
    settings.duckdb_path = db_path
    return db_path


def test_list_plans_returns_empty_when_schema_missing(empty_duckdb):
    assert PlanRepository(DuckDBConnection(empty_duckdb)).list_plans() == []


def test_lookup_plan_not_found_when_schema_missing(empty_duckdb):
    result = lookup_plan(plan_key="S5678-012")
    assert result.status.value == "not_found"


@pytest.mark.asyncio
async def test_chat_not_found_when_schema_missing(empty_duckdb, monkeypatch):
    monkeypatch.setenv("LLM_MOCK_MODE", "1")
    from medicare_navigator.config import settings

    settings.llm_mock_mode = True
    from medicare_navigator.orchestrator.router import orchestrator

    response = await orchestrator.run(
        message="How much will lisinopril 10mg cost on plan S5678-012 for a 90 day supply?"
    )
    assert response.status == "not_found"
    assert response.explanation
