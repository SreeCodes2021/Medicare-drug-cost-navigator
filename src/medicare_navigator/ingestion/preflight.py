"""Pre-flight checks before running a full SPUF ingest."""

from __future__ import annotations

from medicare_navigator.ingestion.manifest import load_manifest


def should_skip_spuf_ingest(
    *,
    version: str,
    states: list[str],
    force: bool = False,
) -> tuple[bool, str]:
    """Return (True, reason) when the CMS release is already loaded for the active states."""
    if force:
        return False, ""

    manifest = load_manifest()
    spuf = manifest.get("spuf", {})
    if not isinstance(spuf, dict):
        return False, ""

    manifest_version = spuf.get("version")
    if not manifest_version or str(manifest_version) != str(version):
        return False, ""

    manifest_states = sorted(str(s).upper() for s in (spuf.get("states") or []))
    active_states = sorted(s.upper() for s in states)
    if manifest_states != active_states:
        return False, ""

    return True, (
        f"No new CMS release: {version} already ingested for states {','.join(active_states)}"
    )
