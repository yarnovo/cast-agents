FROM python:3.13-slim

WORKDIR /app

# git: uv 拉 git source 依赖 (akong-agent-harness / cast-platform-tools)
RUN apt-get update && apt-get install -y --no-install-recommends git && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

COPY pyproject.toml uv.lock* README.md ./
RUN uv sync --frozen --no-install-project --no-dev || uv sync --no-install-project --no-dev

COPY src ./src

# cast/builtin-agents 跨平台 yaml 真源 · build context 由 GHA workflow clone
# (lead 后续起 cast-builtin-agents 独立 GitHub 仓 · workflow clone 进 build context)
COPY cast-builtin-agents ./cast-builtin-agents

RUN uv sync --no-dev

ENV PATH="/app/.venv/bin:$PATH"
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV CAST_BUILTIN_AGENTS_DIR=/app/cast-builtin-agents

EXPOSE 8000

CMD ["uvicorn", "cast_agents.main:app", "--host", "0.0.0.0", "--port", "8000"]
