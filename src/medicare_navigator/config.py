from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


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
    api_port: int = 8000
    cors_origins: str = "http://localhost:5173,http://localhost:8000"

    session_ttl_minutes: int = 30
    max_chat_turns: int = 5
    max_tool_rounds: int = 8
    navigator_mode: str = "mcp_agent"

    project_root: Path = Path(__file__).resolve().parents[2]

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
