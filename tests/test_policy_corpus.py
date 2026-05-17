import subprocess
import sys

import pytest

from medicare_navigator.config import settings
from medicare_navigator.ingestion.manifest import get_as_of, load_manifest
from medicare_navigator.ingestion.policy_corpus import (
    default_corpus_path,
    ingest_policy_corpus,
    load_policy_yaml,
)
from medicare_navigator.storage.connection import DuckDBConnection


def test_load_policy_yaml_parses_passages():
    passages, as_of = load_policy_yaml(default_corpus_path())
    assert len(passages) >= 10
    assert as_of == "2026-01-15"
    assert all(p["passage_id"] for p in passages)
    assert all(p["text"] for p in passages)


def test_ingest_policy_corpus_writes_duckdb(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    duckdb_path = data_dir / "navigator.duckdb"
    passages, _ = load_policy_yaml()
    ingest_policy_corpus(db=DuckDBConnection(path=duckdb_path), chroma_path=data_dir / "chroma")
    db = DuckDBConnection(path=duckdb_path)
    count = db.fetchone("SELECT COUNT(*) FROM policy_passages")[0]
    assert count == len(passages)


def test_ingest_policy_corpus_writes_chroma(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    duckdb_path = data_dir / "navigator.duckdb"
    passages, _ = load_policy_yaml()
    chroma_path = data_dir / "chroma"
    ingest_policy_corpus(db=DuckDBConnection(path=duckdb_path), chroma_path=chroma_path)

    import chromadb

    client = chromadb.PersistentClient(path=str(chroma_path))
    collection = client.get_collection("policy_corpus")
    assert collection.count() == len(passages)


def test_ingest_policy_corpus_updates_manifest(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setattr(settings, "data_dir", data_dir)
    duckdb_path = data_dir / "navigator.duckdb"
    passages, as_of = load_policy_yaml()
    ingest_policy_corpus(db=DuckDBConnection(path=duckdb_path), chroma_path=data_dir / "chroma")
    manifest = load_manifest()
    entry = manifest["policy_corpus"]
    assert entry["as_of"] == as_of
    assert entry["passage_count"] == len(passages)
    assert entry["source_id"] == "cms_policy_corpus"
    assert get_as_of("policy_corpus") == as_of


def test_ingest_policy_corpus_idempotent(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    duckdb_path = data_dir / "navigator.duckdb"
    db = DuckDBConnection(path=duckdb_path)
    chroma_path = data_dir / "chroma"
    ingest_policy_corpus(db=db, chroma_path=chroma_path)
    ingest_policy_corpus(db=db, chroma_path=chroma_path)
    count = db.fetchone("SELECT COUNT(*) FROM policy_passages")[0]
    distinct = db.fetchone("SELECT COUNT(DISTINCT passage_id) FROM policy_passages")[0]
    assert count == distinct


def test_cli_policy_subcommand(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setattr(settings, "data_dir", data_dir)
    monkeypatch.setattr(settings, "duckdb_path", data_dir / "navigator.duckdb")
    monkeypatch.setattr(settings, "chroma_path", data_dir / "chroma")
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "medicare_navigator.ingestion.cli",
            "policy",
            "--source",
            str(default_corpus_path()),
        ],
        capture_output=True,
        text=True,
        cwd=settings.project_root,
    )
    assert result.returncode == 0
    assert "passages" in result.stdout.lower()
