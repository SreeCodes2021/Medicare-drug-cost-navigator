"""Canonical documentation URLs for demo data sources."""

from medicare_navigator.guardrails.source_catalog import (
    SOURCE_CATALOG,
    label_for_source_id,
    url_for_source_id,
)

__all__ = ["SOURCE_URLS", "label_for_source_id", "url_for_source_id"]

# Backward-compatible alias
SOURCE_URLS = {source_id: meta["url"] for source_id, meta in SOURCE_CATALOG.items()}

