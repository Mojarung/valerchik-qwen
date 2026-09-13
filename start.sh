#!/usr/bin/env bash
# Подъём всего стека одной командой (Linux/macOS).
#   ./start.sh                — базовый стек
#   ./start.sh --voice        — + голосовой ввод (STT)
#   ./start.sh --docs         — + разбор PDF с картинками (docling)
#   ./start.sh --s3           — + объектное хранилище картинок (MinIO)
#   ./start.sh --ollama       — + своя LLM qwen3.8:27b в Ollama (скачивает ~18 ГБ)
#   API_SCALE=3 ./start.sh    — 3 инстанса API за nginx
set -euo pipefail

# Секреты генерируем, а не берём из примера: стенд, поднятый одной командой,
# раньше получал пароль admin12345 и такой же уезжал на сервер.
if [ ! -f .env ]; then
  cp .env.example .env
  rand() { LC_ALL=C tr -dc 'A-Za-z0-9' < /dev/urandom | head -c "$1"; }
  for var in APP_SECRET:48 ADMIN_PASSWORD:16 OPERATOR_PASSWORD:16 POSTGRES_PASSWORD:24; do
    name=${var%%:*}; length=${var##*:}; value=$(rand "$length")
    if grep -q "^${name}=" .env; then
      sed -i.bak "s|^${name}=.*|${name}=${value}|" .env && rm -f .env.bak
    else
      printf '%s=%s
' "$name" "$value" >> .env
    fi
  done
  echo "Создан .env со случайными секретами. Учётные данные администратора:"
  echo "  admin / $(grep '^ADMIN_PASSWORD=' .env | cut -d= -f2)"
  echo "  op_maria / $(grep '^OPERATOR_PASSWORD=' .env | cut -d= -f2)"
fi

PROFILES=()
LOCAL_LLM=0
for arg in "$@"; do
  case "$arg" in
    --voice) PROFILES+=(--profile voice) ;;
    --rerank) PROFILES+=(--profile rerank) ;;
    --docs) PROFILES+=(--profile docs) ;;
    --s3) PROFILES+=(--profile s3) ;;
    --ollama) PROFILES+=(--profile ollama); LOCAL_LLM=1 ;;
  esac
done

# Флаг --ollama разворачивает qwen3.8:27b рядом со стеком. Адрес модели пуст —
# подставляем локальную Ollama; заданный вручную (например, GPU-сервер) не трогаем.
# Адрес из .env применяется на первом старте — дальше его меняют в админке.
if [ "$LOCAL_LLM" = 1 ] && ! grep -Eq '^LLM_BASE_URL=.+' .env; then
  sed -i.bak 's|^LLM_BASE_URL=.*|LLM_BASE_URL=http://ollama:11434/v1|; s|^LLM_API_KEY=$|LLM_API_KEY=ollama|' .env
  grep -Eq '^LLM_MODEL=.+' .env || sed -i.bak 's|^LLM_MODEL=.*|LLM_MODEL=qwen3.8:27b|' .env
  rm -f .env.bak
  echo "LLM: локальная Ollama (http://ollama:11434/v1), модель $(grep '^LLM_MODEL=' .env | cut -d= -f2)"
fi

docker compose "${PROFILES[@]}" up -d --build --scale "api=${API_SCALE:-1}"

echo ""
echo "Валерчик запускается: http://localhost:8090"
echo "  оператор: /operator (op_maria) · админка: /admin (admin) — пароли в .env"
echo "Первый старт скачивает модель эмбеддингов — подождите 1-2 минуты."
