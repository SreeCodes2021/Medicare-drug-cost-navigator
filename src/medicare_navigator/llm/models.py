from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from medicare_navigator.config import settings

# Fallback only — used if config/deploy.yaml is missing or its `llm:` section is malformed.
# The real, operator-editable catalog lives in config/deploy.yaml so adding/repricing a
# model never requires a code change or redeploy of this module.
_FALLBACK_DEFAULT_MODEL = "gpt-5.6-luna"
_FALLBACK_MODELS: tuple[dict[str, Any], ...] = (
    {
        "id": "gpt-5.6-luna",
        "label": "GPT-5.6 Luna",
        "provider": "openai",
        "input_per_mtok": 0.50,
        "output_per_mtok": 2.00,
        "openai_reasoning_effort": "none",
    },
    {
        "id": "gpt-5.4-nano",
        "label": "GPT-5.4 Nano",
        "provider": "openai",
        "input_per_mtok": 0.10,
        "output_per_mtok": 0.40,
    },
    {
        "id": "claude-haiku-4-5-20251001",
        "label": "Claude Haiku 4.5",
        "provider": "anthropic",
        "input_per_mtok": 0.25,
        "output_per_mtok": 1.25,
    },
)


@dataclass(frozen=True)
class ModelSpec:
    id: str
    label: str
    provider: str
    input_per_mtok: float
    output_per_mtok: float
    # Reasoning models reject function tools on chat/completions unless effort is "none".
    openai_reasoning_effort: str | None = None


@dataclass(frozen=True)
class _LlmDeployConfig:
    default_model: str
    mediator_default_model: str
    catalog: dict[str, ModelSpec]


def _specs_from_entries(entries: list[dict[str, Any]]) -> dict[str, ModelSpec]:
    return {
        entry["id"]: ModelSpec(
            id=entry["id"],
            label=entry["label"],
            provider=entry["provider"],
            input_per_mtok=float(entry["input_per_mtok"]),
            output_per_mtok=float(entry["output_per_mtok"]),
            openai_reasoning_effort=entry.get("openai_reasoning_effort"),
        )
        for entry in entries
    }


@lru_cache(maxsize=1)
def _load_deploy_llm_config() -> _LlmDeployConfig:
    fallback = _LlmDeployConfig(
        default_model=_FALLBACK_DEFAULT_MODEL,
        mediator_default_model=_FALLBACK_DEFAULT_MODEL,
        catalog=_specs_from_entries(list(_FALLBACK_MODELS)),
    )
    path: Path = settings.config_dir / "deploy.yaml"
    if not path.is_file():
        return fallback
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        llm_section = data.get("llm") or {}
        entries = llm_section.get("models")
        if not entries:
            return fallback
        catalog = _specs_from_entries(entries)
        default_model = llm_section.get("default_model") or fallback.default_model
        mediator_default_model = (
            llm_section.get("mediator_default_model") or default_model
        )
        if default_model not in catalog:
            return fallback
        return _LlmDeployConfig(
            default_model=default_model,
            mediator_default_model=mediator_default_model,
            catalog=catalog,
        )
    except (yaml.YAMLError, KeyError, TypeError, ValueError):
        # Malformed config/deploy.yaml must never take the app down — fall back to the
        # hardcoded catalog, same policy as ingestion/spuf.py and part_d_benefit_params.py.
        return fallback


def _model_catalog() -> dict[str, ModelSpec]:
    return _load_deploy_llm_config().catalog


def default_llm_model() -> str:
    return _load_deploy_llm_config().default_model


def default_mediator_llm_model() -> str:
    return _load_deploy_llm_config().mediator_default_model


def resolve_model(model_id: str | None) -> ModelSpec:
    catalog = _model_catalog()
    if model_id is not None:
        key = model_id.strip()
        spec = catalog.get(key)
        if spec is None:
            allowed = ", ".join(sorted(catalog))
            raise ValueError(f"Unsupported model '{key}'. Allowed models: {allowed}.")
        return spec

    default_model = default_llm_model()
    key = (settings.llm_model or default_model).strip()
    spec = catalog.get(key)
    if spec is not None:
        return spec
    return catalog[default_model]


def provider_has_credentials(provider: str) -> bool:
    if provider == "openai":
        return bool(settings.openai_api_key)
    return bool(settings.anthropic_api_key)


def list_available_models() -> list[dict[str, str]]:
    return [
        {
            "id": spec.id,
            "label": spec.label,
            "provider": spec.provider,
            "configured": provider_has_credentials(spec.provider),
        }
        for spec in _model_catalog().values()
    ]


def estimate_cost_usd(spec: ModelSpec, *, input_tokens: int, output_tokens: int) -> float:
    return round(
        (input_tokens * spec.input_per_mtok + output_tokens * spec.output_per_mtok) / 1_000_000,
        6,
    )
