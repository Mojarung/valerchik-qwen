"""Иллюстрации методички: связь куска текста с рисунком по авторской нумерации.

Номер и есть связь: автор документа проставил её за нас, угадывать не нужно.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from app.ingest.chunking import Section, _normalized_text

log = logging.getLogger(__name__)

# Больше трёх картинок под одним ответом — это уже не иллюстрация, а свалка
MAX_IMAGES_PER_SECTION = 3

_FIGURE_REF = re.compile(r"\b(рис(?:унок|унк\w*|\.)?|табл(?:ица|иц\w*|\.)?)\s*№?\s*(\d{1,3})", re.IGNORECASE)


def figure_numbers(text: str) -> set[str]:
    """-> {'рис:26', 'табл:3'} — ключи, по которым сходятся подпись и ссылка."""
    out = set()
    for kind, number in _FIGURE_REF.findall(text):
        prefix = "табл" if kind.lower().startswith("табл") else "рис"
        out.add(f"{prefix}:{int(number)}")
    return out


def attach_images(sections: list[Section], images: list[dict[str, Any]]) -> int:
    """Раскладывает иллюстрации по кускам текста.

    Первый способ — авторский: под картинкой стоит «Рисунок 26 – …», а в тексте
    «(Рисунок 26)». Совпадение номеров надёжнее любой догадки по координатам.

    Второй — по соседству, для картинок без номера в подписи (в реальных
    методичках таких большинство). Сервис разбора отдаёт, под каким заголовком
    и после какого абзаца картинка стоит в документе; кусок с тем же
    заголовком и тем же абзацем и есть её место. Это слабее авторской связи,
    но заведомо лучше пустоты с висящей в тексте ссылкой «см. скриншот».
    """
    by_figure: dict[str, dict[str, Any]] = {}
    for image in images:
        key = image.get("figure")
        if key and key not in by_figure:
            by_figure[key] = image

    attached = 0
    placed: set[int] = set()
    for section in sections:
        wanted = sorted(figure_numbers(section.text) & by_figure.keys())
        for key in wanted[:MAX_IMAGES_PER_SECTION]:
            section.images.append(by_figure[key])
            placed.add(id(by_figure[key]))
            attached += 1

    for image in images:
        if id(image) in placed:
            continue
        target = _section_by_anchor(sections, image.get("anchor") or {})
        if target is None or len(target.images) >= MAX_IMAGES_PER_SECTION:
            continue
        target.images.append(image)
        placed.add(id(image))
        attached += 1
    return attached


def _section_by_anchor(sections: list[Section], anchor: dict[str, Any]) -> Section | None:
    """Кусок, под чьим заголовком и после чьего абзаца стояла картинка.

    Заголовок в методичке повторяется у нескольких кусков (длинный раздел режется
    на части), поэтому среди одноимённых выбираем тот, где есть сам абзац.
    """
    heading = _normalized_text(str(anchor.get("heading") or ""))
    before = _normalized_text(str(anchor.get("before") or ""))[:80]
    if not heading and not before:
        return None
    named = [s for s in sections if heading and _normalized_text(s.heading or "") == heading]
    pool = named or sections
    if before:
        for section in pool:
            if before in _normalized_text(section.text):
                return section
    return named[0] if named else None


# Ссылка на рисунок в тексте: «(Рисунок 26)», «(см. рис. 3)», «(Рисунок 26 (2))»,
# «как на Рисунке 5» — и строка-подпись «Рисунок 26 – Выбор сертификата».
_FIGURE_IN_PARENS = re.compile(
    r"\s*\((?:см\.\s*)?рис(?:унок|унк\w*|\.)?\s*№?\s*(?P<n>\d{1,3})(?:\s*\(\d+\))?\)",
    re.IGNORECASE,
)
_FIGURE_CAPTION_LINE = re.compile(
    r"^[ \t]*рис(?:унок|\.)?\s*№?\s*(?P<n>\d{1,3})\s*(?:[-–—:.]\s*)?[^\n]*\n?",
    re.IGNORECASE | re.MULTILINE,
)
_FIGURE_INLINE = re.compile(
    r"(?:см\.\s*)?\bрис(?:унок|унк\w*|\.)?\s*№?\s*(?P<n>\d{1,3})\b",
    re.IGNORECASE,
)


def strip_dangling_figures(section: Section) -> int:
    """Убирает из текста ссылки на рисунки, которых к куску не приложилось.

    Инструкция говорит «нажмите кнопку, как на скриншоте (Рисунок 26)», а
    картинки нет — сервис разбора не поднят или подпись без номера. Человек
    читает ссылку в никуда, а модель послушно переносит её в ответ. Ссылки на
    приложенные картинки не трогаем: они на месте. Таблицы тоже — они лежат в
    тексте разметкой и никуда не пропадают. Возвращает число убранных ссылок.
    """
    present = {img.get("figure") for img in section.images}
    missing = {key for key in figure_numbers(section.text) if key.startswith("рис:") and key not in present}
    if not missing:
        return 0
    removed = 0

    def gone(match: re.Match[str]) -> bool:
        return f"рис:{int(match.group('n'))}" in missing

    def drop(match: re.Match[str]) -> str:
        nonlocal removed
        if not gone(match):
            return match.group(0)
        removed += 1
        return ""

    def soften(match: re.Match[str]) -> str:
        nonlocal removed
        if not gone(match):
            return match.group(0)
        removed += 1
        return "иллюстрации в исходном документе"

    body = _FIGURE_CAPTION_LINE.sub(drop, section.body)
    body = _FIGURE_IN_PARENS.sub(drop, body)
    body = _FIGURE_INLINE.sub(soften, body)
    body = re.sub(r"[ \t]+([.,;:!?])", r"\1", body)
    body = re.sub(r"[ \t]{2,}", " ", body)
    body = re.sub(r"\n{3,}", "\n\n", body)
    section.body = body.strip()
    return removed
