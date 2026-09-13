"""Колонтитулы и служебные строки PDF: убрать до нарезки, а не после.

PDF не знает, где кончается страница текста и начинается её оформление: конвертер
отдаёт колонтитул тем же текстом, что и абзац. В выгрузке КонсультантПлюс шапка
приказа, «Документ предоставлен КонсультантПлюс», «Дата сохранения» и «Страница 7
из 57» стоят на каждой из 57 страниц — и без чистки они разрезают пункты
регламента посередине фразы, попадают в ответы и сближают между собой все куски
документа, потому что у каждого есть одни и те же пять строк.

Правила консервативные намеренно: выброшенную строку тела уже не вернуть, а
лишний колонтитул только шумит. Поэтому повтор считается по строке целиком — без
замены всех цифр на «#»: «(Рисунок 26).» и «(Рисунок 27).» — разные строки, и в
инструкции на 370 страниц таких ссылок десятки на каждую главу. Отбрасываем только
номер страницы, приклеенный к колонтитулу с краю. И правило повтора работает
только там, где видны страницы: у .md, .txt и .docx колонтитулов в тексте нет, и
повтор там — содержание («Если ошибка сохраняется, обратитесь в поддержку» в
конце каждого раздела).
"""

from __future__ import annotations

import math
import re
from collections import Counter

# Голый номер страницы: «7», «- 7 -». Не больше трёх цифр: «2026» отдельной
# строкой — год с титульного листа («Москва / 2026»), а не страница.
_PAGE_NUMBER = re.compile(r"^[-–—]?\s*\d{1,3}\s*[-–—]?$")
# «Страница 7 из 57», «стр. 7 / 57» — в том числе внутри строки колонтитула
# («www.consultant.ru Страница 2 из 57»).
_PAGE_OF = re.compile(r"(?:страница|стр\.)\s*\d{1,4}\s*(?:из|/)\s*(\d{1,4})", re.IGNORECASE)
_PAGE_ALONE = re.compile(r"^(?:страница|стр\.)\s*\d{1,4}$", re.IGNORECASE)
# Строка оглавления: заголовок, отточие и номер страницы («Уведомления ...... 108»),
# или отточие, перенесённое без номера. Разбор инструкций нарезал из оглавлений
# десятки кусков, и один из них на замере вышел первым на жалобу про контракт.
# Пять точек подряд: обычное многоточие («Нажмите «Далее»…») их не даёт.
_TOC_LINE = re.compile(r"\.{5,}\s*\d{0,4}\s*$")
# Строки выгрузки КонсультантПлюс: к документу они отношения не имеют, а стоят
# на каждой странице. Отдельным списком, а не только правилом повтора: на
# титульном листе они встречаются один раз, и повтором их не поймать.
_SERVICE = (
    re.compile(r"^документ предоставлен консультантплюс$", re.IGNORECASE),
    re.compile(r"^консультантплюс$", re.IGNORECASE),
    re.compile(r"^(?:https?://)?(?:www\.)?consultant\.ru/?$", re.IGNORECASE),
    re.compile(r"^над[её]жная правовая поддержка$", re.IGNORECASE),
    re.compile(r"^дата сохранения:\s*\d{1,2}\.\d{1,2}\.\d{2,4}$", re.IGNORECASE),
)
# Колонтитул — короткая строка. Длиннее — абзац, и повтор у него случайный.
_RUNNING_MAX_CHARS = 160
# Номер страницы по краю колонтитула («Инструкция для поставщика 12»): считаем
# повтор без него, иначе у каждой страницы была бы своя «уникальная» шапка.
_EDGE_NUMBER = re.compile(r"^\d{1,4}\s+|\s+\d{1,4}$")
# Разметка, повтор которой — структура, а не колонтитул: строки таблиц и их
# разделители, заголовки markdown, заглушки картинок docling («<!-- image -->»),
# пункты списков («− Все;» в инструкции повторяется в каждом фильтре) и голые
# нумерованные заголовки. Последние без номера по краю (_key) сливаются в одну
# строку: «Приложение 1» … «Приложение 5» — пять повторов «Приложение», и на
# коротком акте регламент терял все границы приложений как «колонтитул».
_STRUCTURAL = re.compile(
    r"^(?:\||#|<!--|!\[|[-−–—•*·]\s|\d{1,3}[.)]\s"
    r"|(?:приложение|раздел|глава|часть|статья|таблица|рисунок)(?:\s+N?\s*\d{1,3}[.)]?)?$)",
    re.IGNORECASE,
)
_LETTER = re.compile(r"[А-Яа-яЁёA-Za-z]")
_TERMINAL = (".", "!", "?", ":", ";")

# Колонтитул стоит почти на каждой странице, но бывает только на чётных или
# только на нечётных — отсюда доля 0.4, а не «на каждой». Строка тела
# повторяется на трети страниц только в особых случаях, и ниже её держит
# условие разброса.
PAGE_SHARE = 0.4
# Нижняя граница порога: на коротком документе 40 % страниц — это два-три
# повтора, а столько раз может встретиться и обычная фраза.
MIN_REPEATS = 6
# Колонтитул есть и в начале, и в конце документа. Повтор, собранный в одной
# главе (таблица фильтров в разделе поиска), колонтитулом не бывает.
SPREAD = 0.2
# Следующий номер страницы — не дальше стольких от предыдущего: страница без
# номера (титул, вклейка со схемой) не рвёт последовательность.
PAGE_STEP = 3
# Номер страницы стоит у разрыва страницы — в паре строк от него.
BREAK_REACH = 2


def _key(line: str) -> str:
    """Строка для подсчёта повторов: без краевого номера страницы и лишних пробелов."""
    return _EDGE_NUMBER.sub("", " ".join(line.split()))


def _is_service(line: str) -> bool:
    if _PAGE_ALONE.match(line) or _TOC_LINE.search(line):
        return True
    if len(line) <= _RUNNING_MAX_CHARS and _PAGE_OF.search(line):
        return True
    return any(pattern.match(line) for pattern in _SERVICE)


def _can_repeat(line: str) -> bool:
    """Может ли строка оказаться колонтитулом по правилу повтора."""
    return len(line) <= _RUNNING_MAX_CHARS and bool(_LETTER.search(line)) and not _STRUCTURAL.match(line)


def _split_pages(raw: str) -> tuple[list[str], set[int]]:
    """Строки текста (без краевых пробелов) и номера строк, которые начинают страницу («\\f»)."""
    lines: list[str] = []
    breaks: set[int] = set()
    for number, page in enumerate(raw.split("\f")):
        if number:
            breaks.add(len(lines))
        lines.extend(line.strip() for line in page.split("\n"))
    return lines, breaks


def _page_numbers(lines: list[str], breaks: set[int]) -> set[int]:
    """Строки — номера страниц: голое число, продолжающее нумерацию страниц.

    Голое число бывает и номером строки таблицы: в инструкции по электронному
    актированию таблица сокращений развернулась в строки, и «14» стоит отдельной
    строкой между 13-й и 15-й. Вырезать его — слепить строку 14 со строкой 13.
    Номер страницы от него отличает место: он продолжает последовательность
    (5, 6, 7 — а не 6, 14, 7), стоит в серии с соседом или у разрыва страницы.
    """
    found = [(i, int(re.sub(r"\D", "", line))) for i, line in enumerate(lines) if _PAGE_NUMBER.match(line)]
    # Одно и то же число отдельной строкой на многих страницах — тоже подвал
    # (номер тома, «1» у конвертера на каждой странице), а не строка таблицы:
    # у строк таблицы номера разные.
    repeated = {number for number, count in Counter(n for _, n in found).items() if count >= MIN_REPEATS}
    accepted: set[int] = set()
    last = 0
    for k, (index, number) in enumerate(found):
        neighbours = {found[j][1] for j in (k - 1, k + 1) if 0 <= j < len(found)}
        in_series = bool(neighbours & {number - 1, number + 1})
        near_break = any(abs(index - b) <= BREAK_REACH for b in breaks)
        if number in repeated:
            accepted.add(index)
        elif in_series or near_break or last < number <= last + PAGE_STEP:
            accepted.add(index)
            last = max(last, number)
    return accepted


def _page_count(lines: list[str], breaks: set[int]) -> int:
    totals = [int(match.group(1)) for line in lines for match in _PAGE_OF.finditer(line)]
    pages = len(breaks) + 1 if breaks else 0
    return max(pages, max(totals, default=0), len(_page_numbers(lines, breaks)))


def estimate_pages(text: str) -> int:
    """Сколько страниц было в PDF — по разрывам, «из N» и номерам страниц. 0 — не видно.

    Номера страниц — самая частая улика: markitdown разрывов страниц не ставит, а
    номер внизу страницы превращается в строку из одних цифр.
    """
    return _page_count(*_split_pages(text.replace("\r\n", "\n").replace("\r", "\n")))


def _running_keys(lines: list[str], pages: int) -> set[str]:
    """Строки, которые повторяются по всему документу, — колонтитулы.

    Страниц не видно — колонтитулов нет: у текстовых форматов их не выводит ни
    один конвертер, и повтор там — содержание, а не оформление.
    """
    if not pages:
        return set()
    positions: dict[str, list[int]] = {}
    for index, line in enumerate(lines):
        if line and _can_repeat(line):
            positions.setdefault(_key(line), []).append(index)
    threshold = max(MIN_REPEATS, math.ceil(PAGE_SHARE * pages))
    total = max(len(lines), 1)
    running: set[str] = set()
    for key, where in positions.items():
        if len(where) < threshold:
            continue
        if where[0] <= SPREAD * total and where[-1] >= (1 - SPREAD) * total:
            running.add(key)
    return running


def boilerplate_report(text: str) -> tuple[str, list[str]]:
    """Текст без колонтитулов и список выброшенных строк (для отчёта и проверки)."""
    raw = text.replace("\r\n", "\n").replace("\r", "\n")
    lines, breaks = _split_pages(raw)
    numbers = _page_numbers(lines, breaks)
    running = _running_keys(lines, _page_count(lines, breaks))
    dropped = [
        bool(line)
        and (index in numbers or _is_service(line) or (_can_repeat(line) and _key(line) in running))
        for index, line in enumerate(lines)
    ]
    removed = [line for line, drop in zip(lines, dropped, strict=True) if drop]
    if not removed:
        return text, []
    originals = raw.replace("\f", "\n").split("\n")
    return _heal([None if drop else line for line, drop in zip(originals, dropped, strict=True)]), removed


def strip_boilerplate(text: str) -> str:
    """Убрать колонтитулы, номера страниц и служебные строки выгрузки."""
    return boilerplate_report(text)[0]


def _heal(lines: list[str | None]) -> str:
    """Склеить шов страницы, оставшийся на месте выброшенного колонтитула.

    Колонтитул стоит посреди фразы: «…контролю качества работы АИС "Портал» —
    конец страницы, шапка следующей — «поставщиков".». Если просто вырезать
    его, между половинами фразы остаётся пустая строка, и нарезка по абзацам
    честно разрежет пункт надвое. Поэтому шов, на котором предыдущая строка не
    закончила фразу, а следующая начинается со строчной буквы, зашиваем без
    пустой строки; иначе оставляем одну пустую строку, если она там была.
    """
    out: list[str] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        if line is not None and (line.strip() or not _touches_removed(lines, index)):
            out.append(line)
            index += 1
            continue
        # Шов: подряд идущие выброшенные и пустые строки
        end = index
        had_blank = False
        while end < len(lines) and (lines[end] is None or not (lines[end] or "").strip()):
            had_blank = had_blank or lines[end] is not None
            end += 1
        before = next((item for item in reversed(out) if item.strip()), "")
        after = (lines[end] or "") if end < len(lines) else ""
        continues = (
            before and after and not before.rstrip().endswith(_TERMINAL) and after.lstrip()[:1].islower()
        )
        if had_blank and not continues and out and out[-1].strip():
            out.append("")
        index = end
    return "\n".join(out)


def _touches_removed(lines: list[str | None], index: int) -> bool:
    """Пустая строка входит в шов, если рядом с ней (через пустые) выброшена строка."""
    for step in (-1, 1):
        probe = index + step
        while 0 <= probe < len(lines) and lines[probe] is not None and not (lines[probe] or "").strip():
            probe += step
        if 0 <= probe < len(lines) and lines[probe] is None:
            return True
    return False


def removed_summary(removed: list[str], limit: int = 20) -> list[tuple[str, int]]:
    """Выброшенные строки с числом повторов — для лога и замера.

    Цифры здесь заменяем на «#»: «Страница 7 из 57» и «Страница 8 из 57» в
    отчёте — одна строка, иначе список забивают полсотни номеров страниц.
    """
    return Counter(re.sub(r"\d+", "#", _key(line)) for line in removed).most_common(limit)
