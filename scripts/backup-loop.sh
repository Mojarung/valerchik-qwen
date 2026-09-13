#!/bin/sh
# Планировщик резервных копий: крутится в контейнере backup.
#
# Расписание не зашито в cron, а лежит в тех же настройках, что правятся в
# админке (таблица settings_entries): раз в минуту цикл перечитывает их и
# сравнивает время последней копии с заданным интервалом. Так изменение
# «раз в сутки» → «раз в 6 часов» применяется без пересборки и перезапуска.
#
# Образ взят postgres:18-alpine намеренно: pg_dump обязан быть не старше
# сервера, а так они всегда одной версии.
set -eu

BACKUP_DIR="${BACKUP_DIR:-/backups}"
DATA_DIR="${DATA_DIR:-/data}"
TICK_SECONDS="${TICK_SECONDS:-60}"
# После неудачной копии следующая попытка — не на ближайшем тике, а через паузу:
# лежащая база иначе давала бы по записи об ошибке в минуту.
RETRY_SECONDS="${RETRY_SECONDS:-600}"

log() { echo "[backup] $(date '+%Y-%m-%d %H:%M:%S') $*"; }

# Значение настройки из БД. Настройки хранятся как {"v": <значение>}; если
# строки ещё нет (приложение не стартовало ни разу) — берём запасное значение.
setting() {
    value=$(psql -tAc "select value->>'v' from settings_entries where key='$1'" 2>/dev/null || true)
    [ -n "$value" ] && echo "$value" || echo "$2"
}

latest_auto_age_seconds() {
    newest=$(ls -1d "$BACKUP_DIR"/auto-* 2>/dev/null | sort | tail -1)
    [ -z "$newest" ] && { echo 999999999; return; }
    now=$(date +%s)
    made=$(date -r "$newest" +%s 2>/dev/null || echo 0)
    echo $((now - made))
}

make_backup() {
    name="auto-$(date +%Y%m%d-%H%M%S)"
    # Копия собирается под временным именем и становится auto-* только целиком.
    # Раньше каталог неудачной копии оставался как auto-*: планировщик считал его
    # свежей копией и не пробовал снова до конца интервала (сутки по умолчанию),
    # а ротация учитывала пустышку наравне с настоящими и вытесняла рабочую копию.
    out="$BACKUP_DIR/.partial-$name"
    mkdir -p "$out"
    log "снимаю копию → $BACKUP_DIR/$name"

    if ! pg_dump -Fc > "$out/support.dump" 2>"$out/error.log"; then
        log "ОШИБКА: pg_dump не отработал: $(tr '\n' ' ' < "$out/error.log")"
        rm -rf "$out"
        return 1
    fi
    # Картинки методичек и вложения операторов лежат в томе, а не в БД: без них
    # у статей останутся ссылки в никуда.
    if [ -d "$DATA_DIR/media" ]; then
        tar -cz -C "$DATA_DIR" media > "$out/media.tar.gz" 2>>"$out/error.log" || \
            log "предупреждение: файлы заархивировать не удалось"
    fi
    rm -f "$out/error.log"

    {
        echo "Валерчик — автоматическая резервная копия"
        echo "создана:        $(date '+%Y-%m-%d %H:%M:%S')"
        echo "база:           support.dump (pg_dump -Fc)"
        echo "файлы:          media.tar.gz (/data/media)"
        echo "восстановление: ./scripts/restore.sh backups/$name"
    } > "$out/manifest.txt"

    mv "$out" "$BACKUP_DIR/$name"
    log "готово: $(du -sh "$BACKUP_DIR/$name" | cut -f1)"
}

rotate() {
    keep="$1"
    # Чистим только auto-*: копии, снятые человеком через make backup, называются
    # manual-* и переживают любую ротацию — их удаление должно быть осознанным.
    # `head -n -N` — расширение GNU, а образ postgres:18-alpine несёт busybox:
    # там эта форма молча ничего не отбирала, и ротация не удаляла ничего.
    total=$(ls -1d "$BACKUP_DIR"/auto-* 2>/dev/null | wc -l)
    [ "$total" -le "$keep" ] && return 0
    ls -1d "$BACKUP_DIR"/auto-* 2>/dev/null | sort | head -n "$((total - keep))" | while read -r old; do
        log "удаляю старую копию: $(basename "$old")"
        rm -rf "$old"
    done
}

log "планировщик запущен, каталог копий: $BACKUP_DIR"
# Недописанные копии остаются, только если контейнер остановили посреди pg_dump
rm -rf "$BACKUP_DIR"/.partial-*
last_failure=0
while true; do
    enabled=$(setting backup.enabled true)
    if [ "$enabled" = "true" ]; then
        interval_hours=$(setting backup.interval_hours 24)
        age=$(latest_auto_age_seconds)
        since_failure=$(($(date +%s) - last_failure))
        if [ "$age" -ge $((interval_hours * 3600)) ] && [ "$since_failure" -ge "$RETRY_SECONDS" ]; then
            make_backup || last_failure=$(date +%s)
        fi
        # Чистка идёт каждый проход, а не только после новой копии: уменьшив
        # «хранить N» в админке, человек ждёт, что лишнее исчезнет сейчас, а не
        # через сутки, когда подойдёт очередная копия.
        rotate "$(setting backup.keep 7)"
    fi
    sleep "$TICK_SECONDS"
done
