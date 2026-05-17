"""Load curated policy passages into DuckDB and Chroma."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from medicare_navigator.config import settings
from medicare_navigator.ingestion.manifest import merge_manifest
from medicare_navigator.ingestion.schema import ensure_schema
from medicare_navigator.storage.connection import DuckDBConnection

SOURCE_ID = "cms_policy_corpus"
COLLECTION_NAME = "policy_corpus"
REQUIRED_FIELDS = ("passage_id", "text", "source_label", "url", "as_of_date")


def default_corpus_path() -> Path:
    return settings.config_dir / "policy_corpus.yaml"


def load_policy_yaml(path: Path | None = None) -> tuple[list[dict[str, Any]], str]:
    """Parse policy corpus YAML. Returns (passages, corpus_as_of)."""
    path = path or default_corpus_path()
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"Invalid policy corpus YAML at {path}")
    passages = data.get("passages") or []
    if not isinstance(passages, list) or not passages:
        raise ValueError(f"No passages found in {path}")
    corpus_as_of = str(data.get("as_of", "2026-01-15"))
    normalized: list[dict[str, Any]] = []
    for entry in passages:
        if not isinstance(entry, dict):
            raise ValueError("Each passage must be a mapping")
        missing = [f for f in REQUIRED_FIELDS if not entry.get(f)]
        if missing:
            raise ValueError(f"Passage missing fields {missing}: {entry.get('passage_id')}")
        normalized.append(
            {
                "passage_id": str(entry["passage_id"]),
                "text": str(entry["text"]).strip(),
                "source_label": str(entry["source_label"]),
                "url": str(entry["url"]),
                "as_of_date": str(entry.get("as_of_date", corpus_as_of)),
            }
        )
    return normalized, corpus_as_of


def _write_duckdb(passages: list[dict[str, Any]], db: DuckDBConnection) -> None:
    ensure_schema(db)
    conn = db.connect()
    try:
        conn.execute("DELETE FROM policy_passages")
        for p in passages:
            conn.execute(
                "INSERT INTO policy_passages VALUES (?, ?, ?, ?, ?)",
                [p["passage_id"], p["text"], p["source_label"], p["url"], p["as_of_date"]],
            )
    finally:
        conn.close()


def _write_chroma(passages: list[dict[str, Any]], chroma_path: Path | None = None) -> None:
    import chromadb

    path = chroma_path or settings.chroma_path
    path.mkdir(parents=True, exist_ok=True)
    client = chromadb.PersistentClient(path=str(path))
    try:
        client.delete_collection(COLLECTION_NAME)
    except Exception:
        pass
    collection = client.create_collection(COLLECTION_NAME)
    collection.add(
        ids=[p["passage_id"] for p in passages],
        documents=[p["text"] for p in passages],
        metadatas=[
            {
                "source_label": p["source_label"],
                "url": p["url"],
                "as_of_date": p["as_of_date"],
            }
            for p in passages
        ],
    )


def ingest_policy_corpus(
    source: Path | None = None,
    *,
    db: DuckDBConnection | None = None,
    chroma_path: Path | None = None,
) -> dict[str, Any]:
    """Load YAML passages into DuckDB policy_passages and Chroma policy_corpus."""
    passages, corpus_as_of = load_policy_yaml(source)
    db = db or DuckDBConnection()
    _write_duckdb(passages, db)
    _write_chroma(passages, chroma_path)
    manifest = merge_manifest(
        {
            "policy_corpus": {
                "as_of": corpus_as_of,
                "source_id": SOURCE_ID,
                "passage_count": len(passages),
            }
        }
    )
    return {
        "passage_count": len(passages),
        "as_of": corpus_as_of,
        "source_id": SOURCE_ID,
        "manifest": manifest,
    }
