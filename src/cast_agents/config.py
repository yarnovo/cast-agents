from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """cast-agents 装配商配置 · LLM / memory / tools 由 harness 自己读 env。

    harness 自己读的 env (FC 函数 env 配)：
      - AKONG_LLM_API_KEY      LLM key (DashScope)
      - AKONG_LLM_BASE_URL     默认 dashscope OpenAI 兼容 endpoint
      - AKONG_LLM_MODEL        默认 deepseek-v3.1
      - AKONG_API_BASE_URL     cast-api endpoint (memory / tools / agent bundle 都从这拉)
    """

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # cast-api 地址 · cast-platform-tools 5 个 tool 都 POST 这里
    api_base_url: str = "https://api.cast.agentaily.com"
    env: str = "prod"


settings = Settings()
