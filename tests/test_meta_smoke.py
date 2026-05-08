"""meta-agent schema 烟雾测试 · 不调真 LLM"""

from cast_agents.meta import META_TOOLS, SYSTEM_PROMPT


def test_meta_tools_schema():
    names = {t["name"] for t in META_TOOLS}
    assert names == {"create_user_agent", "ask_owner", "summarize_and_confirm"}

    create = next(t for t in META_TOOLS if t["name"] == "create_user_agent")
    required = create["input_schema"]["required"]
    assert {"name", "tagline", "soul", "playbook", "style", "expertise"} <= set(required)

    services_prop = create["input_schema"]["properties"]["services"]
    assert services_prop["type"] == "array"
    item_required = services_prop["items"]["required"]
    assert {"title", "description", "price_cents"} <= set(item_required)


def test_system_prompt_contains_key_concepts():
    assert "阿空小造" in SYSTEM_PROMPT
    assert "虚拟角色" in SYSTEM_PROMPT
    assert "create_user_agent" in SYSTEM_PROMPT
