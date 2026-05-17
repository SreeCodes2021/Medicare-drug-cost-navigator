from __future__ import annotations

import json
from typing import Any

from medicare_navigator.config import settings
from medicare_navigator.models.tool_result import ToolResult, ToolStatus
from medicare_navigator.storage.connection import DuckDBConnection

SOURCE_ID = "cms_policy_corpus"
COLLECTION_NAME = "policy_corpus"
DEFAULT_TOP_K = 5


def _manifest_as_of() -> str:
    manifest_path = settings.data_dir / "manifest.json"
    if manifest_path.exists():
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
        return data.get("policy_corpus", {}).get("as_of", "2026-01-15")
    return "2026-01-15"


def _keyword_score(query: str, text: str) -> float:
    query_words = set(query.lower().split())
    text_lower = text.lower()
    return sum(1 for w in query_words if w in text_lower) / max(len(query_words), 1)


def _passage_dict(
    passage_id: str,
    text: str,
    source_label: str | None,
    url: str | None,
    score: float,
) -> dict[str, Any]:
    return {
        "passage_id": passage_id,
        "text": text,
        "source_label": source_label,
        "url": url,
        "score": score,
    }


def _keyword_retrieve(query: str, rows: list[tuple]) -> list[dict[str, Any]]:
    scored: list[tuple[float, tuple]] = []
    for row in rows:
        score = _keyword_score(query, row[1])
        if score > 0:
            scored.append((score, row))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [
        _passage_dict(row[0], row[1], row[2], row[3], score)
        for score, row in scored
    ]


def _vector_retrieve(query: str, top_k: int) -> list[dict[str, Any]]:
    try:
        import chromadb

        client = chromadb.PersistentClient(path=str(settings.chroma_path))
        collection = client.get_collection(COLLECTION_NAME)
        results = collection.query(query_texts=[query], n_results=top_k * 2)
        passages: list[dict[str, Any]] = []
        for i, doc_id in enumerate(results["ids"][0]):
            meta = results["metadatas"][0][i]
            passages.append(
                _passage_dict(
                    doc_id,
                    results["documents"][0][i],
                    meta.get("source_label"),
                    meta.get("url"),
                    1.0 - (i * 0.1),
                )
            )
        return passages
    except Exception:
        return []


def _merge_passages(
    keyword_hits: list[dict[str, Any]],
    vector_hits: list[dict[str, Any]],
    *,
    keyword_weight: float = 0.5,
    vector_weight: float = 0.5,
) -> list[dict[str, Any]]:
    by_id: dict[str, dict[str, Any]] = {}

    for hit in keyword_hits:
        pid = hit["passage_id"]
        by_id[pid] = {
            **hit,
            "keyword_score": hit["score"],
            "vector_score": 0.0,
        }

    for hit in vector_hits:
        pid = hit["passage_id"]
        if pid in by_id:
            by_id[pid]["vector_score"] = hit["score"]
            if not by_id[pid].get("source_label"):
                by_id[pid]["source_label"] = hit.get("source_label")
            if not by_id[pid].get("url"):
                by_id[pid]["url"] = hit.get("url")
        else:
            by_id[pid] = {
                **hit,
                "keyword_score": 0.0,
                "vector_score": hit["score"],
            }

    merged: list[dict[str, Any]] = []
    for entry in by_id.values():
        combined = (
            keyword_weight * entry["keyword_score"] + vector_weight * entry["vector_score"]
        )
        merged.append({**entry, "score": combined})

    merged.sort(key=lambda p: p["score"], reverse=True)
    return merged


def policy_retrieval(query: str, top_k: int = DEFAULT_TOP_K) -> ToolResult[list[dict]]:
    as_of = _manifest_as_of()
    db = DuckDBConnection()
    rows = db.fetchall(
        "SELECT passage_id, text, source_label, url, as_of_date FROM policy_passages"
    )

    keyword_hits = _keyword_retrieve(query, rows) if rows else []
    vector_hits = _vector_retrieve(query, top_k)

    if not keyword_hits and not vector_hits:
        return ToolResult.failure(
            ToolStatus.no_match,
            source_id=SOURCE_ID,
            as_of_date=as_of,
            message="No policy passages found in corpus.",
        )

    merged = _merge_passages(keyword_hits, vector_hits)
    top = merged[:top_k]

    if not top:
        return ToolResult.failure(
            ToolStatus.no_match,
            source_id=SOURCE_ID,
            as_of_date=as_of,
            message="No relevant policy passages found for query.",
        )

    passages = [
        {
            "passage_id": p["passage_id"],
            "text": p["text"],
            "source_label": p.get("source_label"),
            "url": p.get("url"),
            "score": p["score"],
        }
        for p in top
    ]
    return ToolResult.ok(passages, source_id=SOURCE_ID, as_of_date=as_of)
