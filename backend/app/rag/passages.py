"""Фрагмент базы знаний и нарезка раздела на фрагменты."""

import re
from dataclasses import dataclass, field
from typing import Any

KIND_SECTION = "section"
KIND_TERM = "term"
KIND_GLOSSARY = "glossary"
KIND_TICKET = "ticket"
KIND_FAQ = "faq"

PIPELINE = "rag"
CHUNK_CHARS = 1300
CARRY_MAX_CHARS = 300
PARAGRAPH_MAX_CHARS = 2400
MAX_FIGURES = 4
MIN_FILL = 0.4

_SENTENCE = re.compile(r"(?<=[.!?;])\s+")


@dataclass
class Figure:
    data: bytes
    digest: str
    page: int
    caption: str = ""


@dataclass
class Passage:
    title: str
    text: str
    doc: str
    path: str = ""
    kind: str = KIND_SECTION
    audience: str = "all"
    pages: list[int] = field(default_factory=list)
    figures: list[Figure] = field(default_factory=list)
    examples: list[str] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def category(self) -> str:
        return f"{self.doc} › {self.path}" if self.path else self.doc


Block = tuple[int, "str | Figure"]


def _split_long(text: str) -> list[str]:
    if len(text) <= PARAGRAPH_MAX_CHARS:
        return [text]
    parts: list[str] = []
    buf = ""
    for sentence in _SENTENCE.split(text):
        if buf and len(buf) + len(sentence) > PARAGRAPH_MAX_CHARS:
            parts.append(buf)
            buf = sentence
        else:
            buf = f"{buf} {sentence}".strip()
    if buf:
        parts.append(buf)
    return parts


def chunk_blocks(blocks: list[Block], limit: int = CHUNK_CHARS) -> list[tuple[str, list[Figure], list[int]]]:
    """Абзацы и картинки раздела → фрагменты не длиннее limit.

    Картинка остаётся во фрагменте, рядом с текстом которого стояла. Короткий
    последний абзац переносится в следующий фрагмент, чтобы шаг не рвался посередине.
    """
    chunks: list[tuple[str, list[Figure], list[int]]] = []
    texts: list[tuple[int, str]] = []
    figures: list[Figure] = []
    size = 0

    def flush() -> None:
        if texts:
            chunks.append(
                ("\n".join(t for _, t in texts), figures[:MAX_FIGURES], sorted({p for p, _ in texts}))
            )
        elif figures and chunks:
            chunks[-1][1].extend(figures[: MAX_FIGURES - len(chunks[-1][1])])

    for page, block in blocks:
        if isinstance(block, Figure):
            figures.append(block)
            continue
        for part in _split_long(block):
            if texts and size + len(part) > limit and size >= limit * MIN_FILL:
                flush()
                last_page, last = texts[-1]
                carry = len(last) <= CARRY_MAX_CHARS and not figures
                texts, figures = ([(last_page, last)] if carry else []), []
                size = len(last) if carry else 0
            texts.append((page, part))
            size += len(part) + 1
    flush()
    return chunks
