"""Краткое содержание контракта, приложенного пользователем в чат.

Пайплайн разбора документов один на весь проект — docling с фолбэком до
markitdown, `app/ingest/documents.py` — и до сих пор был подключён только к
админскому импорту базы знаний. Здесь его пользовательский сценарий: вместо
статей базы знаний на выходе несколько пунктов выжимки, чтобы человек не читал
контракт целиком сам.

Тот же принцип, что и у docparse и генерации вопроса к куску методички
(`app/ingest/questions.py`): внешний сервис (сначала docling, потом LLM) не
имеет права ломать сценарий. Не поднят один — берём другой; не настроена сама
модель — отдаём выжимку из найденных разделов без неё.
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import TYPE_CHECKING

from app.ingest.chunking import Section, split_sections
from app.services import agent as agent_module

if TYPE_CHECKING:
    from app.dynamic_settings import DynamicSettings
    from app.runtime import AppRuntime

log = logging.getLogger(__name__)

# Заголовки разделов, где в договоре обычно лежат предмет, цена, стороны и
# сроки. Простой поиск по подстроке в заголовке — не LLM-навигация по
# документу: контракт бывает на десятки страниц, и прогонять через модель
# каждый раздел ради поиска нужных было бы намного дороже вопроса к готовому
# тексту.
_KEY_SECTION_HINTS = (
    "предмет",
    "цена",
    "стоимост",
    "срок",
    "исполнен",
    "сторон",
    "заказчик",
    "поставщик",
    "подрядчик",
    "исполнитель",
    "ответственност",
    "расчет",
    "расчёт",
    "реквизит",
    "гаранти",
    "санкц",
)

# Сколько текста контракта попадает в модель. Начало документа — там обычно
# предмет и стороны — плюс разделы, чей заголовок похож на нужные, но не весь
# документ разом: методичка на сорок страниц кормила бы модель тем же объёмом,
# который и должен получиться выжимкой. Порядок величины — как в
# app/ingest/questions.py (там 2000 символов на один кусок перед моделью), но
# здесь пунктов сразу семь и нужен весь контракт целиком, а не один абзац.
LEAD_CHARS = 6000
MAX_SECTION_CHARS = 1200
MAX_CONTEXT_CHARS = 12000

# Пунктов в выжимке — по заданию: предмет, цена, стороны, срок исполнения и
# ключевые условия.
MAX_BULLETS = 7

SUMMARY_INSTRUCTIONS = (
    "Ты помогаешь участнику закупки быстро понять договор, не читая его целиком. "
    "Ниже — текст контракта (может быть неполным, вырезки из большого документа). "
    "Составь краткую выжимку на русском языке: 5-7 пунктов списком, каждый пункт — "
    "с новой строки, начинается с «- ». Обязательно укажи, если это есть в тексте: "
    "предмет закупки, цену контракта, стороны договора (заказчик и "
    "поставщик/подрядчик/исполнитель), срок исполнения. Остальные пункты — ключевые "
    "условия: порядок расчётов, ответственность сторон, санкции, гарантии. Пиши по "
    "существу, без вступления и без заключения — только сам список пунктов."
)


async def _extract_markdown(rt: AppRuntime, filename: str, raw: bytes) -> str:
    """Файл → markdown. Docling по возможности, иначе markitdown без иллюстраций.

    Тот же фолбэк, что при импорте базы знаний: docparse недоступен или
    ответил пустым текстом — берём markitdown. Иллюстрации выжимке не нужны,
    важен только текст.
    """
    docparse = getattr(rt, "docparse", None)
    # docparse (docling) — сервис разбора PDF, а docx и doc он не понимает: тот
    # же выбор, что и при импорте базы знаний (app/ingest/documents.py).
    if filename.lower().endswith(".pdf") and docparse is not None:
        parsed = await docparse.parse(filename, raw)
        if parsed is not None and parsed.markdown.strip():
            return str(parsed.markdown)
    from app.ingest.documents import extract_text_from_file

    # Синхронный конвертер на крупном файле работает секунды — в своём потоке,
    # чтобы не морозить цикл событий остальным запросам.
    return await asyncio.to_thread(extract_text_from_file, filename, raw)


def _matched_sections(sections: list[Section]) -> list[Section]:
    return [
        s for s in sections if s.heading and any(hint in s.heading.lower() for hint in _KEY_SECTION_HINTS)
    ]


def _build_context(markdown: str, matched: list[Section]) -> str:
    """Начало документа + явно релевантные разделы, обрезанные по лимиту."""
    lead = markdown[:LEAD_CHARS]
    parts = [lead]
    total = len(lead)
    for section in matched:
        block = f"### {section.heading}\n{section.body}"
        if block[:120] in lead:
            # раздел уже попал в начало документа целиком — не повторяем
            continue
        if total + len(block) > MAX_CONTEXT_CHARS:
            break
        parts.append(block)
        total += len(block)
    return "\n\n---\n\n".join(parts)


def _fallback_summary(matched: list[Section], markdown: str) -> str:
    """Выжимка без LLM: заголовки нужных разделов и начало их текста.

    Модель недоступна или не настроена — то же правило, что и везде в
    проекте: внешний сервис не имеет права оставить пользователя без ответа
    только потому, что он выключен.
    """
    bullets: list[str] = []
    for section in matched:
        heading = (section.heading or "").strip()
        if not heading:
            continue
        snippet = re.sub(r"\s+", " ", section.body).strip()[:200]
        bullets.append(f"- {heading}: {snippet}" if snippet else f"- {heading}")
        if len(bullets) >= MAX_BULLETS:
            break
    if not bullets:
        snippet = re.sub(r"\s+", " ", markdown).strip()[:300]
        bullets = [f"- {snippet}"] if snippet else ["- Не удалось выделить содержимое документа."]
    return "\n".join(bullets)


async def _summarize_text(dyn: DynamicSettings, context: str, matched: list[Section], markdown: str) -> str:
    if not agent_module.llm_configured(dyn):
        return _fallback_summary(matched, markdown)
    try:
        from pydantic_ai import Agent

        # Тем же механизмом, что и агент поддержки: общая настроенная модель,
        # только без инструментов — здесь один вопрос к готовому тексту, а не
        # ReAct-цикл с поиском. build_model зовём через модуль (не биндим имя
        # функции), чтобы тесты могли подменить его monkeypatch'ем, как это
        # уже делают тесты агента поддержки.
        agent: Agent[None, str] = Agent(agent_module.build_model(dyn), instructions=SUMMARY_INSTRUCTIONS)
        result = await asyncio.wait_for(agent.run(context), timeout=60.0)
        text = (result.output or "").strip()
        if text:
            return text
    except Exception as exc:  # noqa: BLE001 — отказ модели не должен ронять загрузку
        log.warning("LLM не собрала выжимку контракта (%s: %s), беру заголовки", type(exc).__name__, exc)
    return _fallback_summary(matched, markdown)


async def summarize_contract(rt: AppRuntime, filename: str, raw: bytes) -> str:
    """Файл контракта → 5-7 пунктов выжимки: предмет, цена, стороны, срок, условия."""
    markdown = await _extract_markdown(rt, filename, raw)
    if not markdown.strip():
        raise ValueError("Не удалось получить текст документа")
    sections = split_sections(markdown, max_chars=MAX_SECTION_CHARS)
    matched = _matched_sections(sections)
    context = _build_context(markdown, matched)
    return await _summarize_text(rt.dyn, context, matched, markdown)
