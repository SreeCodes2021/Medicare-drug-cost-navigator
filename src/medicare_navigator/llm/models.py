from __future__ import annotations

from dataclasses import dataclass

from medicare_navigator.config import settings

DEFAULT_LLM_MODEL = "gpt-5.4-nano"


@dataclass(frozen=True)
class ModelSpec:
    id: str
    label: str
    provider: str
    input_per_mtok: float
    output_per_mtok: float
    # Reasoning models reject function tools on chat/completions unless effort is "none".
    openai_reasoning_effort: str | None = None


MODEL_CATALOG: dict[str, ModelSpec] = {
    "gpt-5.4-nano": ModelSpec(
        id="gpt-5.4-nano",
        label="GPT-5.4 Nano",
        provider="openai",
        input_per_mtok=0.10,
        output_per_mtok=0.40,
    ),
    "gpt-5.6-luna": ModelSpec(
        id="gpt-5.6-luna",
        label="GPT-5.6 Luna",
        provider="openai",
        input_per_mtok=0.50,
        output_per_mtok=2.00,
        openai_reasoning_effort="none",
    ),
    "claude-haiku-4-5-20251001": ModelSpec(
        id="claude-haiku-4-5-20251001",
        label="Claude Haiku 4.5",
        provider="anthropic",
        input_per_mtok=0.25,
        output_per_mtok=1.25,
    ),
}


def resolve_model(model_id: str | None) -> ModelSpec:
    if model_id is not None:
        key = model_id.strip()
        spec = MODEL_CATALOG.get(key)
        if spec is None:
            allowed = ", ".join(sorted(MODEL_CATALOG))
            raise ValueError(f"Unsupported model '{key}'. Allowed models: {allowed}.")
        return spec

    key = (settings.llm_model or DEFAULT_LLM_MODEL).strip()
    spec = MODEL_CATALOG.get(key)
    if spec is not None:
        return spec
    return MODEL_CATALOG[DEFAULT_LLM_MODEL]


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
        for spec in MODEL_CATALOG.values()
    ]


def estimate_cost_usd(spec: ModelSpec, *, input_tokens: int, output_tokens: int) -> float:
    return round(
        (input_tokens * spec.input_per_mtok + output_tokens * spec.output_per_mtok) / 1_000_000,
        6,
    )
