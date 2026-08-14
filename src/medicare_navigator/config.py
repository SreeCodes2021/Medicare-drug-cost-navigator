import os
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_CONFIG_MARKER = "config/ingest_filters.yaml"


def _resolve_project_root() -> Path:
    """Repo root in dev (src layout) and Docker (/app with pip-installed package)."""
    if env_root := os.environ.get("PROJECT_ROOT"):
        return Path(env_root)
    here = Path(__file__).resolve()
    src_layout = here.parents[2]
    if (src_layout / _CONFIG_MARKER).is_file():
        return src_layout
    for candidate in (Path.cwd(), *Path.cwd().parents):
        if (candidate / _CONFIG_MARKER).is_file():
            return candidate
    return src_layout


def _env_file_path() -> Path | None:
    path = _resolve_project_root() / ".env"
    return path if path.is_file() else None


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=_env_file_path(),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    llm_provider: str = "anthropic"
    anthropic_api_key: str = ""
    openai_api_key: str = ""
    # Empty = fall back to llm/models.py's default_llm_model(), sourced from
    # config/deploy.yaml's `llm.default_model` (not hardcoded here).
    llm_model: str = ""

    data_dir: Path = Path("./data")
    duckdb_path: Path = Path("./data/navigator.duckdb")

    api_host: str = "0.0.0.0"
    api_port: int = Field(default=8000, validation_alias="API_PORT")

    @field_validator("api_port", mode="before")
    @classmethod
    def _coerce_port(cls, value: object) -> object:
        # Render and other PaaS hosts set PORT; prefer it over API_PORT default.
        if os.environ.get("PORT"):
            return os.environ["PORT"]
        return value
    cors_origins: str = "http://localhost:5173,http://localhost:8000"

    session_ttl_minutes: int = 30
    max_chat_turns: int = 5
    max_tool_rounds: int = 8

    llm_mock_mode: bool = Field(default=False, validation_alias="LLM_MOCK")
    llm_timeout_seconds: float = Field(default=60.0, validation_alias="LLM_TIMEOUT_SECONDS")
    llm_max_retries: int = Field(default=2, validation_alias="LLM_MAX_RETRIES")

    mediator_enabled: bool = Field(default=False, validation_alias="MEDIATOR_ENABLED")
    # Empty = fall back to llm/models.py's default_mediator_llm_model(), sourced from
    # config/deploy.yaml's `llm.mediator_default_model` (not hardcoded here).
    mediator_llm_model: str = Field(default="", validation_alias="MEDIATOR_LLM_MODEL")
    mediator_timeout_seconds: float = Field(
        default=4.0, validation_alias="MEDIATOR_TIMEOUT_SECONDS"
    )
    mediator_max_retries: int = Field(default=1, validation_alias="MEDIATOR_MAX_RETRIES")

    # Comma-separated state codes; intersected with pdp_region_codes in ingest_filters.yaml.
    # Overrides yaml `states` default. Unset = use yaml defaults (local dev).
    ingest_states: str = Field(default="", validation_alias="INGEST_STATES")

    default_timezone: str = Field(default="America/Chicago", validation_alias="DEFAULT_TIMEZONE")

    analytics_enabled: bool = Field(default=True, validation_alias="ANALYTICS_ENABLED")
    analytics_flush_interval_seconds: float = Field(
        default=60.0, validation_alias="ANALYTICS_FLUSH_INTERVAL_SECONDS"
    )
    admin_token: str = Field(default="", validation_alias="ADMIN_TOKEN")
    # Default lookback window (hours) when the usage dashboard passes no since/until.
    # 2160 = ~3 months; keep in sync with the dashboard's "3mo" preset.
    admin_usage_hours: int = Field(default=2160, validation_alias="ADMIN_USAGE_HOURS")

    project_root: Path = Field(default_factory=_resolve_project_root)

    @property
    def config_dir(self) -> Path:
        return self.project_root / "config"

    @property
    def disclaimer_text(self) -> str:
        path = self.config_dir / "disclaimer.txt"
        return path.read_text(encoding="utf-8").strip()

    @property
    def privacy_policy_text(self) -> str:
        path = self.config_dir / "privacy_policy.txt"
        return path.read_text(encoding="utf-8").strip()

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def env_file(self) -> Path:
        return self.project_root / ".env"

    def llm_provider_status(self) -> dict[str, str]:
        """configured | empty_in_env_file | missing"""
        env_names = {
            "openai": ("openai_api_key", "OPENAI_API_KEY"),
            "anthropic": ("anthropic_api_key", "ANTHROPIC_API_KEY"),
        }
        empty_vars: set[str] = set()
        if self.env_file.is_file():
            for line in self.env_file.read_text(encoding="utf-8").splitlines():
                stripped = line.strip()
                if not stripped or stripped.startswith("#") or "=" not in stripped:
                    continue
                name, value = stripped.split("=", 1)
                if not value.strip():
                    empty_vars.add(name)

        statuses: dict[str, str] = {}
        for provider, (field_name, env_name) in env_names.items():
            if getattr(self, field_name):
                statuses[provider] = "configured"
            elif env_name in empty_vars:
                statuses[provider] = "empty_in_env_file"
            else:
                statuses[provider] = "missing"
        return statuses

    def llm_configuration_hint(self, provider: str) -> str:
        env_name = "OPENAI_API_KEY" if provider == "openai" else "ANTHROPIC_API_KEY"
        status = self.llm_provider_status().get(provider, "missing")
        if status == "empty_in_env_file":
            return (
                f"{env_name} is listed in {self.env_file} but has no value. "
                "Paste your API key after the equals sign, save the file, and restart the server."
            )
        return (
            f"Set {env_name} in {self.env_file} (or export it in your shell), "
            "then restart the server."
        )


settings = Settings()
