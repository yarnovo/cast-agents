from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    api_base_url: str = "https://api.xhs.agentaily.com"
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-haiku-4-5-20251001"
    agents_root: str = "agents"
    env: str = "dev"

    @property
    def agents_dir(self) -> Path:
        return Path(self.agents_root)


settings = Settings()
