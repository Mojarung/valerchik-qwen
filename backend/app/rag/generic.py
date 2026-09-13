"""Прочие форматы для дозагрузки: таблица «вопрос — ответ», JSON, текстовые документы."""

import io
import json
import re
from typing import Any

import openpyxl

from app.rag.passages import KIND_FAQ, KIND_SECTION, Figure, Passage, chunk_blocks
from app.rag.pdf import audience_for, doc_title
from app.rag.text import clean_markup

_MD_HEADING = re.compile(r"^(#{1,4})\s+(.+?)\s*#*$")
_QUESTION_KEYS = ("question", "вопрос", "title", "заголовок", "тема")
_ANSWER_KEYS = ("answer", "ответ", "text", "текст", "решение", "content")


def _pick(record: dict[str, Any], keys: tuple[str, ...]) -> str:
    lowered = {str(k).strip().lower(): v for k, v in record.items()}
    for key in keys:
        for name, value in lowered.items():
            if name.startswith(key) and value not in (None, ""):
                return clean_markup(str(value))
    return ""


def _faq(records: list[dict[str, Any]], filename: str) -> list[Passage]:
    title = doc_title(filename)
    passages = []
    for record in records:
        question, answer = _pick(record, _QUESTION_KEYS), _pick(record, _ANSWER_KEYS)
        if question and answer:
            audience = str(record.get("audience") or audience_for(title))
            category = _pick(record, ("category", "категория", "раздел"))
            passages.append(
                Passage(
                    title=question, text=answer, doc=title, path=category, kind=KIND_FAQ, audience=audience
                )
            )
    return passages


def parse_table(data: bytes, filename: str) -> list[Passage]:
    book = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    try:
        rows = list(book.active.iter_rows(values_only=True))
    finally:
        book.close()
    if not rows:
        return []
    header = [str(v or "").strip() for v in rows[0]]
    return _faq([dict(zip(header, row, strict=False)) for row in rows[1:]], filename)


def parse_json(data: bytes, filename: str) -> list[Passage]:
    payload = json.loads(data.decode("utf-8-sig"))
    if isinstance(payload, dict):
        payload = next((v for v in payload.values() if isinstance(v, list)), [payload])
    return _faq([r for r in payload if isinstance(r, dict)], filename)


def parse_markdown(text: str, filename: str) -> list[Passage]:
    """Текстовый документ → разделы по заголовкам markdown, раздел → фрагменты."""
    title = doc_title(filename)
    audience = audience_for(title)
    sections: list[tuple[list[str], list[str]]] = [([], [])]
    stack: list[tuple[int, str]] = []
    for line in clean_markup(text).split("\n"):
        m = _MD_HEADING.match(line)
        if m:
            level = len(m[1])
            while stack and stack[-1][0] >= level:
                stack.pop()
            stack.append((level, m[2].strip()))
            sections.append(([t for _, t in stack], []))
        elif line.strip():
            sections[-1][1].append(line.strip())
    passages = []
    for path, lines in sections:
        if not lines:
            continue
        blocks: list[tuple[int, str | Figure]] = [(0, line) for line in lines]
        heading = path[-1] if path else title
        for chunk, _, _ in chunk_blocks(blocks):
            passages.append(
                Passage(
                    title=heading,
                    text=chunk,
                    doc=title,
                    path=" › ".join(path),
                    kind=KIND_SECTION,
                    audience=audience,
                )
            )
    return passages


def parse_document(data: bytes, filename: str) -> list[Passage]:
    lower = filename.lower()
    if lower.endswith((".md", ".txt")):
        return parse_markdown(data.decode("utf-8-sig", errors="replace"), filename)
    from markitdown import MarkItDown, UnsupportedFormatException

    try:
        result = MarkItDown().convert_stream(io.BytesIO(data), file_extension="." + lower.rsplit(".", 1)[-1])
    except UnsupportedFormatException:
        # Формат не знает никто — это «фрагментов нет», а не падение: задача объяснит отказ словами
        return []
    return parse_markdown(result.text_content or "", filename)
