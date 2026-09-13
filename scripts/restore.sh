#!/usr/bin/env bash
# Восстановление из копии, сделанной scripts/backup.sh.
#
#   ./scripts/restore.sh backups/auto-20260810-150000
#   ./scripts/restore.sh backups/manual-20260810-150000
#
# Данные на стенде ЗАМЕЩАЮТСЯ: pg_restore идёт с --clean, то есть сносит
# существующие таблицы перед заливкой. Поэтому спрашиваем подтверждение —
# перепутать каталог копии легко, а откатить это уже нечем.
set -euo pipefail

cd "$(dirname "$0")/.."

SRC="${1:-}"
if [ -z "$SRC" ] || [ ! -f "$SRC/support.dump" ]; then
    echo "Укажите каталог копии, например:" >&2
    ls -1d backups/*/ 2>/dev/null | tail -5 | sed 's/^/  .\/scripts\/restore.sh /' >&2
    exit 1
fi

echo "Копия: $SRC"
[ -f "$SRC/manifest.txt" ] && sed 's/^/  /' "$SRC/manifest.txt"
read -r -p "Текущие данные стенда будут заменены. Продолжить? [y/N] " answer
[ "$answer" = "y" ] || [ "$answer" = "Y" ] || { echo "Отменено"; exit 1; }

echo "→ останавливаю api и worker, чтобы никто не писал в базу во время заливки…"
docker compose stop api worker >/dev/null

echo "→ база данных…"
docker compose exec -T postgres pg_restore -U support -d support --clean --if-exists \
    --no-owner < "$SRC/support.dump"

if [ -f "$SRC/media.tar.gz" ]; then
    echo "→ файлы…"
    docker compose start api >/dev/null
    # Ждём, пока контейнер поднимется: exec в стартующий контейнер падает
    for _ in $(seq 1 30); do
        docker compose exec -T api true 2>/dev/null && break
        sleep 1
    done
    # sh -c и здесь: см. пояснение про подмену путей в backup.sh
    docker compose exec -T api sh -c 'rm -rf /data/media && mkdir -p /data'
    docker compose exec -T api sh -c 'tar -xz -C /data' < "$SRC/media.tar.gz"
fi

echo "→ поднимаю сервисы…"
docker compose start api worker >/dev/null

echo "✔ восстановлено."
echo "  Векторный индекс не входит в копию — нажмите «Переиндексировать» в админке"
echo "  (База знаний → Переиндексировать), иначе поиск будет отвечать по старому индексу."
