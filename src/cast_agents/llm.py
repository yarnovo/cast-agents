"""LLM 客户端工厂 · 默认走阿里百炼 (DashScope OpenAI 兼容协议) + DeepSeek-v3.1"""

from __future__ import annotations

from openai import OpenAI

from .config import settings


def new_client() -> OpenAI:
    return OpenAI(
        api_key=settings.llm_api_key,
        base_url=settings.llm_base_url,
    )


def to_openai_tool(spec: dict) -> dict:
    """把 {name, description, input_schema} 转成 OpenAI function tool 格式"""
    return {
        "type": "function",
        "function": {
            "name": spec["name"],
            "description": spec.get("description", ""),
            "parameters": spec.get("input_schema", {"type": "object", "properties": {}}),
        },
    }
