from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    api_base_url: str = "https://api.xhs.agentaily.com"

    # LLM provider · 默认走阿里百炼 (DashScope) · OpenAI 兼容协议
    llm_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    llm_api_key: str = ""  # DASHSCOPE_API_KEY
    llm_model: str = "deepseek-v3.1"  # 默认 DeepSeek · 老板拍

    agents_root: str = "agents"
    env: str = "dev"

    @property
    def agents_dir(self) -> Path:
        return Path(self.agents_root)


settings = Settings()
