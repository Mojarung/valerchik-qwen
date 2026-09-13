FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy PYTHONUNBUFFERED=1

# uv.lock копируется вместе с манифестом, а sync идёт --frozen: образ собирается
# на тех же версиях, на которых прогонялись тесты, а не на свежерезолвленных.
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev

COPY portal.py server.py ./

# Не от root: сервис ходит во внешнюю сеть, и обычная учётка
# ограничивает последствия любой ошибки разбора.
RUN useradd --create-home --uid 10001 app && chown -R app:app /app
USER app

EXPOSE 9200
CMD ["uv", "run", "--no-sync", "python", "server.py"]
