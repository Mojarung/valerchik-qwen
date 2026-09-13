FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy PYTHONUNBUFFERED=1 \
    # Веса моделей разметки — в том, иначе они качаются заново при каждом старте
    HF_HOME=/models \
    # Инференс идёт на CPU: попытка torch.compile упирается в отсутствие компилятора
    TORCHDYNAMO_DISABLE=1 \
    # Каждый воркер и так занимает все ядра; лишние потоки только дерутся за них
    OMP_NUM_THREADS=4

# Системные библиотеки для opencv, который приходит с моделью разбора таблиц:
# в slim-образе их нет, и импорт cv2 падает на libxcb.so.1
RUN apt-get update && apt-get install -y --no-install-recommends \
        libgl1 libglib2.0-0 libxcb1 libxext6 libsm6 libxrender1 \
    && rm -rf /var/lib/apt/lists/*

# Лок копируется вместе с манифестом, а sync идёт --frozen: образ собирается
# на тех же версиях, на которых сервис проверяли, а не на свежерезолвленных.
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev

COPY main.py ./

# Не от root: сервис принимает файлы от пользователей, и обычная учётка
# ограничивает последствия любой ошибки разбора.
# /models — том с весами разметки: он монтируется снаружи, и без прав на
# запись первый же старт под обычной учёткой падал бы на скачивании модели.
# /data — общий с api и воркером том: владелец тот же uid 10001, кто бы из них
# ни создал том первым.
RUN useradd --create-home --uid 10001 app && mkdir -p /models /data && chown -R app:app /app /models /data
USER app

EXPOSE 9100
CMD ["uv", "run", "--no-sync", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "9100"]
