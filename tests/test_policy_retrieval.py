import pytest

from medicare_navigator.config import settings
from medicare_navigator.models.tool_result import ToolStatus
from medicare_navigator.storage.connection import DuckDBConnection
from medicare_navigator.tools.policy_retrieval import (
    DEFAULT_TOP_K,
    _keyword_score,
    _merge_passages,
    _keyword_retrieve,
    policy_retrieval,
)


def test_keyword_score_matches_overlap():
    assert _keyword_score("deductible phase", "During the deductible phase of Medicare") > 0


def test_keyword_retrieve_returns_top_matches(spuf_db):
    db = DuckDBConnection()
    rows = db.fetchall(
        "SELECT passage_id, text, source_label, url, as_of_date FROM policy_passages"
    )
    hits = _keyword_retrieve("deductible phase", rows)
    assert hits
    assert any("deductible" in h["text"].lower() for h in hits)


def test_vector_retrieve_returns_results(spuf_db):
    result = policy_retrieval("deductible phase benefit", top_k=3)
    assert result.status == ToolStatus.ok
    assert result.data


def test_merge_passages_combines_scores():
    keyword_hits = [
        {
            "passage_id": "a",
            "text": "deductible text",
            "source_label": "CMS",
            "url": "https://example.com/a",
            "score": 0.8,
        }
    ]
    vector_hits = [
        {
            "passage_id": "a",
            "text": "deductible text",
            "source_label": "CMS",
            "url": "https://example.com/a",
            "score": 0.6,
        },
        {
            "passage_id": "b",
            "text": "other",
            "source_label": "CMS",
            "url": "https://example.com/b",
            "score": 0.9,
        },
    ]
    merged = _merge_passages(keyword_hits, vector_hits)
    by_id = {m["passage_id"]: m for m in merged}
    assert by_id["a"]["score"] == pytest.approx(0.7)
    assert by_id["b"]["score"] == pytest.approx(0.45)


def test_merge_passages_deduplicates():
    keyword_hits = [
        {
            "passage_id": "dup",
            "text": "same",
            "source_label": "CMS",
            "url": "https://example.com",
            "score": 0.5,
        }
    ]
    vector_hits = [
        {
            "passage_id": "dup",
            "text": "same",
            "source_label": "CMS",
            "url": "https://example.com",
            "score": 0.5,
        }
    ]
    merged = _merge_passages(keyword_hits, vector_hits)
    assert len(merged) == 1


def test_policy_retrieval_respects_top_k(spuf_db):
    result = policy_retrieval("deductible catastrophic formulary tier", top_k=2)
    assert result.status == ToolStatus.ok
    assert len(result.data) <= 2


def test_policy_retrieval_default_top_k_is_five(spuf_db):
    result = policy_retrieval("Medicare Part D benefit phase deductible catastrophic")
    assert result.status == ToolStatus.ok
    assert len(result.data) <= DEFAULT_TOP_K


def test_policy_retrieval_no_match_empty_corpus(tmp_path, monkeypatch):
    from medicare_navigator.ingestion.schema import ensure_schema

    data_dir = tmp_path / "empty"
    data_dir.mkdir()
    duckdb_path = data_dir / "empty.duckdb"
    monkeypatch.setattr(settings, "data_dir", data_dir)
    monkeypatch.setattr(settings, "duckdb_path", duckdb_path)
    monkeypatch.setattr(settings, "chroma_path", data_dir / "chroma")
    ensure_schema(DuckDBConnection(path=duckdb_path))
    result = policy_retrieval("deductible")
    assert result.status == ToolStatus.no_match


def test_policy_retrieval_ok_after_fixture_seed(spuf_db):
    result = policy_retrieval("deductible phase")
    assert result.status == ToolStatus.ok
    assert result.source_id == "cms_policy_corpus"


def test_policy_retrieval_includes_metadata(spuf_db):
    result = policy_retrieval("deductible")
    assert result.status == ToolStatus.ok
    passage = result.data[0]
    for key in ("passage_id", "text", "source_label", "url", "score"):
        assert key in passage
