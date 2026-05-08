FROM python:3.13-slim

WORKDIR /app

# git: uv 拉 git source 依赖 (akong-* / cast-platform-tools / meta-hermes / demo-agents)
RUN apt-get update && apt-get install -y --no-install-recommends git && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

COPY pyproject.toml uv.lock* README.md ./

# build arg 控制是否带 demo extra (staging / dev = 1 · prod = 0)
# 老板 5-9 拍 · meta-hermes 必装 · demo-agents 可选 (装包 + env 双控)
ARG INSTALL_DEMO=0
RUN if [ "$INSTALL_DEMO" = "1" ]; then \
      uv sync --frozen --no-install-project --no-dev --extra demo \
        || uv sync --no-install-project --no-dev --extra demo; \
    else \
      uv sync --frozen --no-install-project --no-dev \
        || uv sync --no-install-project --no-dev; \
    fi

COPY src ./src

RUN if [ "$INSTALL_DEMO" = "1" ]; then \
      uv sync --no-dev --extra demo; \
    else \
      uv sync --no-dev; \
    fi

ENV PATH="/app/.venv/bin:$PATH"
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
# meta.yaml 通过 meta-hermes wheel 包自带 · 不需要 mount 目录
# demo agent yaml 通过 demo-agents wheel (装上才有) · env CAST_INSTALL_DEMO_AGENTS=1 控制 lifespan sync

EXPOSE 8000

CMD ["uvicorn", "cast_agents.main:app", "--host", "0.0.0.0", "--port", "8000"]
