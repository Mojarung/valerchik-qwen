# Backend: FastAPI + taskiq worker (общий образ, разные команды).
# Всё ставится через uv — быстро и воспроизводимо (uv.lock).
FROM ghcr.io/astral-sh/uv:python3.14-bookworm-slim

WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH"

# Процесс работает от обычной учётки: уязвимость в приложении не даёт root в
# контейнере. uid тот же, что у docparse, — они делят том данных. /data — точка
# монтирования этого тома: свежий именованный том наследует владельца отсюда,
# иначе он приезжал бы принадлежащим root.
RUN useradd --create-home --uid 10001 app && mkdir -p /data && chown app:app /data

# слой зависимостей кэшируется отдельно от кода
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project --no-dev

COPY app ./app
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

USER app
EXPOSE 8000
# --proxy-headers: без него request.client.host — это IP контейнера nginx, один
# на всех, и лимит частоты становится общим ведром: любой посетитель двумя
# десятками запросов выключал чат всему порталу. Верим заголовкам только от
# адресов из FORWARDED_ALLOW_IPS — compose задаёт там свою подсеть; без
# переменной uvicorn доверяет лишь 127.0.0.1. Запуск без uv: окружение уже
# собрано, и рантайму незачем трогать его кэш.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
