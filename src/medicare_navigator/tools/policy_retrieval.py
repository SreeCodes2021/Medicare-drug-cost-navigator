from __future__ import annotations

import json

from medicare_navigator.config import settings
from medicare_navigator.models.tool_result import ToolResult, ToolStatus
from medicare_navigator.storage.connection import DuckDBConnection

SOURCE_ID = "cms_policy_corpus"


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


def policy_retrieval(query: str, top_k: int = 3) -> ToolResult[list[dict]]:
    as_of = _manifest_as_of()
    db = DuckDBConnection()
    rows = db.fetchall(
        "SELECT passage_id, text, source_label, url, as_of_date FROM policy_passages"
    )
    if not rows:
        try:
            import chromadb

            client = chromadb.PersistentClient(path=str(settings.chroma_path))
            collection = client.get_collection("policy_corpus")
            results = collection.query(query_texts=[query], n_results=top_k)
            passages = []
            for i, doc_id in enumerate(results["ids"][0]):
                meta = results["metadatas"][0][i]
                passages.append(
                    {
                        "passage_id": doc_id,
                        "text": results["documents"][0][i],
                        "source_label": meta.get("source_label"),
                        "url": meta.get("url"),
                        "score": 1.0 - (i * 0.1),
                    }
                )
            if passages:
                return ToolResult.ok(passages, source_id=SOURCE_ID, as_of_date=as_of)
        except Exception:
            pass
        return ToolResult.failure(
            ToolStatus.no_match,
            source_id=SOURCE_ID,
            as_of_date=as_of,
            message="No policy passages found in corpus.",
        )

    scored = []
    for row in rows:
        score = _keyword_score(query, row[1])
        if score > 0:
            scored.append((score, row))

    scored.sort(key=lambda x: x[0], reverse=True)
    top = scored[:top_k]

    if not top:
        return ToolResult.failure(
            ToolStatus.no_match,
            source_id=SOURCE_ID,
            as_of_date=as_of,
            message="No relevant policy passages found for query.",
        )

    passages = [
        {
            "passage_id": row[0],
            "text": row[1],
            "source_label": row[2],
            "url": row[3],
            "score": score,
        }
        for score, row in top
    ]
    return ToolResult.ok(passages, source_id=SOURCE_ID, as_of_date=as_of)
