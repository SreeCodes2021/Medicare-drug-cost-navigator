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


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    llm_provider: str = "anthropic"
    anthropic_api_key: str = ""
    openai_api_key: str = ""
    llm_model: str = "claude-sonnet-4-6"

    data_dir: Path = Path("./data")
    duckdb_path: Path = Path("./data/navigator.duckdb")
    chroma_path: Path = Path("./data/chroma")

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
    navigator_mode: str = "mcp_agent"

    project_root: Path = Field(default_factory=_resolve_project_root)

    @property
    def config_dir(self) -> Path:
        return self.project_root / "config"

    @property
    def disclaimer_text(self) -> str:
        path = self.config_dir / "disclaimer.txt"
        return path.read_text(encoding="utf-8").strip()

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


settings = Settings()
