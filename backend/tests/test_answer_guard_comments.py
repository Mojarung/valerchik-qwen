"""Пометки разбора PDF («<!-- image -->») не доходят до человека.

docling ставит их на место картинки. В статьи методичек они попали вместе с
текстом, и на проде ответ начинался с «По базе знаний: <!-- image --> МЧД…».
"""

import pytest

from app.adapters.docparse import ParsedDocument
from app.ingest.blob import FileBlob
from app.ingest.records import ArticleRecord
from app.ingest.registry import IngestContext
from app.ingest.sources.pdf_figures import PdfFiguresSource
from app.services.answer_guard import sanitize


def test_sanitize_drops_parser_comments():
    text = "По базе знаний: <!-- image --> МЧД добавится в таблицу.\n\n<!-- image -->\n\nГотово."
    assert sanitize(text) == "По базе знаний: МЧД добавится в таблицу.\n\nГотово."


def test_sanitize_keeps_ordinary_angle_brackets():
    assert sanitize("Сумма < 100 000 ₽ и > 0") == "Сумма < 100 000 ₽ и > 0"


class _FakeDocParse:
    async def parse(self, filename: str, raw: bytes, *, do_ocr: bool = False):
        return ParsedDocument(
            markdown="# Загрузка МЧД\n\n<!-- image -->\n\nМЧД добавится в таблицу, если есть подпись.\n",
            pictures=[],
        )


class _Runtime:
    docparse = _FakeDocParse()


@pytest.mark.asyncio
async def test_pdf_articles_come_without_parser_comments():
    blob = FileBlob(name="mchd.pdf", data=b"%PDF-1.7")
    records = [r async for r in PdfFiguresSource().parse(blob, IngestContext(rt=_Runtime()))]
    articles = [r for r in records if isinstance(r, ArticleRecord)]
    assert articles
    assert all("<!--" not in a.answer for a in articles)
