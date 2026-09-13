FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy PYTHONUNBUFFERED=1

# Лок копируется вместе с манифестом, а sync идёт --frozen: образ собирается
# на тех же версиях, на которых сервис проверяли, а не на свежерезолвленных.
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev

COPY main.py ./

# Не от root: сервис принимает файлы от пользователей, и обычная учётка
# ограничивает последствия любой ошибки разбора.
# Каталог кэша моделей создаём заранее от имени app: том stt_models монтируется
# в /home/app/.cache/huggingface, и без этого Docker создал бы /home/app/.cache
# от root — uv не смог бы завести там свой кэш, и контейнер падал бы на старте.
RUN useradd --create-home --uid 10001 app \
    && mkdir -p /home/app/.cache/huggingface \
    && chown -R app:app /app /home/app
USER app

EXPOSE 9000
CMD ["uv", "run", "--no-sync", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "9000"]
