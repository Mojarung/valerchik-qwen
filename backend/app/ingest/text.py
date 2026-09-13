"""Починка текста, приехавшего в чужой кодировке."""

# Кириллица, прочитанная как latin-1/cp1252: «Выгрузка» → «Âûãðóçêà». Так
# приезжают названия из старых Windows-программ и из multipart-форм, где клиент
# не пометил кодировку: браузер отдаёт байты как есть, а Starlette читает их
# latin-1. Заголовок задачи импорта из-за этого показывался в админке мусором.
_MOJIBAKE_MARKERS = "ÀÁÂÃÄÅÆÇÈÉÊËÌÍÎÏÐÑÒÓÔÕÖ×ØÙÚÛÜÝÞßàáâãäåæçèéêëìíîïðñòóôõö÷øùúûüýþÿ"


def _longest_marker_run(text: str) -> int:
    longest = run = 0
    for ch in text:
        run = run + 1 if ch in _MOJIBAKE_MARKERS else 0
        longest = max(longest, run)
    return longest


# Столько знаков-маркеров подряд — уже не «Grüße», а кириллица, прочитанная как latin-1
_MIN_MOJIBAKE_RUN = 4


def fix_mojibake(text: str) -> str:
    """Вернуть исходную кириллицу, если строка — результат чтения UTF-8/cp1251 как latin-1.

    Возвращает вход без изменений, когда строка уже читаема: обычная кириллица,
    латиница и цифры проходят насквозь. Правка делается только тогда, когда
    перекодировка даёт заметно больше кириллицы, чем было, — иначе итальянское
    «città» или немецкое «Grüße» испортились бы ради ложного срабатывания.
    """
    if not text or _longest_marker_run(text) < _MIN_MOJIBAKE_RUN:
        # Признак — не отдельный «умляут», а длинная череда таких знаков: каждая
        # кириллическая буква превращается в два байта, то есть в два символа
        # подряд. Немецкое «Grüße» и итальянское «città» дают одиночки и сюда
        # не попадают — иначе починка портила бы нормальный текст.
        return text

    def cyrillic(value: str) -> int:
        return sum("а" <= ch.lower() <= "я" or ch in "ёЁ" for ch in value)

    best = text
    for encoding in ("cp1252", "latin-1"):
        for decoding in ("cp1251", "utf-8"):
            try:
                candidate = text.encode(encoding, errors="strict").decode(decoding, errors="strict")
            except UnicodeEncodeError, UnicodeDecodeError:
                continue
            if cyrillic(candidate) > cyrillic(best):
                best = candidate
    return best
