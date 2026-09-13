"""Источник pdf_figures: PDF через сервис разбора отдаёт и картинки, и текст.

Приложение не поднимаем: источник — чистая функция от файла и контекста, а сервис
разбора подменяется заглушкой, поэтому весь путь проверяется без docling и торча.
"""

import pytest

from app.adapters.docparse import ParsedDocument, ParsedPicture
from app.ingest.blob import FileBlob
from app.ingest.records import ArticleRecord, AssetRecord
from app.ingest.registry import MIN_CONFIDENCE, IngestContext
from app.ingest.sources.base import SourceMismatch
from app.ingest.sources.pdf_figures import PdfFiguresSource, picture_anchor

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32

MARKDOWN = """# Подписание контракта

Откройте карточку контракта в личном кабинете и нажмите кнопку «Подписать».
Выберите сертификат электронной подписи (Рисунок 26) и подтвердите действие.

# Отзыв подписи

Отозвать подпись можно до того, как её поставила вторая сторона. Позже отзыв
оформляется только через обращение в поддержку портала.
"""

DOCUMENT = ParsedDocument(
    markdown=MARKDOWN,
    pictures=[
        ParsedPicture(data=PNG, caption="Рисунок 26 – Выбор сертификата", page=7),
        # Подпись без номера — в реальных методичках таких большинство
        ParsedPicture(data=PNG, caption="", page=None, anchor_heading="Отзыв подписи"),
    ],
    pages=12,
)


class FakeDocParse:
    """Заглушка сервиса разбора: отдаёт готовый результат и запоминает вызовы."""

    def __init__(self, document: ParsedDocument | None) -> None:
        self.document = document
        self.calls: list[tuple[str, bool]] = []

    async def parse(self, filename: str, raw: bytes, *, do_ocr: bool = False):
        self.calls.append((filename, do_ocr))
        return self.document


class FakeRuntime:
    def __init__(self, docparse=None) -> None:
        self.docparse = docparse


def pdf_blob() -> FileBlob:
    return FileBlob(name="instrukciya.pdf", data=b"%PDF-1.7\n%fake")


async def collect(ctx: IngestContext) -> list:
    return [record async for record in PdfFiguresSource().parse(pdf_blob(), ctx)]


@pytest.mark.asyncio
async def test_sniff_takes_only_pdf():
    source = PdfFiguresSource()
    assert await source.sniff(pdf_blob()) == 0.85
    assert await source.sniff(pdf_blob()) > MIN_CONFIDENCE
    assert await source.sniff(FileBlob(name="metodichka.docx", data=b"PK\x03\x04")) == 0.0


@pytest.mark.asyncio
async def test_without_docparse_pdf_goes_to_text_source():
    """Профиль docs не поднят — источник отказывается, PDF достаётся document."""
    for ctx in (IngestContext(), IngestContext(rt=FakeRuntime(docparse=None)), IngestContext(rt=object())):
        with pytest.raises(SourceMismatch):
            await collect(ctx)


@pytest.mark.asyncio
async def test_pictures_and_sections_come_out_together():
    docparse = FakeDocParse(DOCUMENT)
    ctx = IngestContext(rt=FakeRuntime(docparse), params={"do_ocr": True, "audience": "supplier"})

    records = await collect(ctx)
    assets = [r for r in records if isinstance(r, AssetRecord)]
    articles = [r for r in records if isinstance(r, ArticleRecord)]

    # Параметры импорта доезжают до сервиса: OCR включают на сканах руками
    assert docparse.calls == [("instrukciya.pdf", True)]

    assert len(assets) == 2
    assert assets[0].data == PNG
    # Номер из подписи — тот же ключ, что даст ссылка «(Рисунок 26)» в тексте
    assert assets[0].anchor == {"figure": "рис:26", "page": 7}
    assert assets[0].origin.locator == "стр. 7"
    assert assets[0].origin.authored is True
    # Подпись без номера картинку не отменяет, а место в документе едет с ней
    assert assets[1].anchor == {"figure": "", "page": None, "heading": "Отзыв подписи"}
    assert assets[1].origin.locator == ""

    assert len(articles) == 2
    assert articles[0].question == "Подписание контракта"
    assert "Подписать" in articles[0].answer
    assert articles[0].category == "Подписание контракта"
    assert articles[0].audience == "supplier"
    assert articles[0].keywords
    assert articles[0].origin.locator == "фрагмент 1"
    assert articles[1].origin.locator == "фрагмент 2"
    # Абзац инструкции писали не как ответ — дословно отдавать его нельзя
    assert articles[0].origin.authored is False
    assert articles[0].origin.source_name == "pdf_figures"
    assert articles[0].origin.file == "instrukciya.pdf"


def test_caption_keeps_its_own_number():
    """«Рисунок 27 – продолжение рисунка 26»: свой номер стоит первым, он и нужен.

    Взять из подписи меньший номер значит показать скриншот 27-го шага человеку,
    который читает про 26-й, — то есть указать не на ту кнопку.
    """

    def anchor(caption, page=1):
        return picture_anchor(ParsedPicture(data=PNG, caption=caption, page=page))

    assert anchor("Рисунок 27 – продолжение рисунка 26")["figure"] == "рис:27"
    assert anchor("Рисунок 07 – Выбор сертификата")["figure"] == "рис:7"
    assert anchor("Таблица 3 – сроки, см. рисунок 5")["figure"] == "табл:3"


def test_page_comes_out_a_number():
    """Страница из JSON приезжает строкой; в статье она читается как число."""

    def page(value):
        return picture_anchor(ParsedPicture(data=PNG, caption="", page=value))["page"]

    assert page("7") == 7
    assert page(7.0) == 7
    assert page(None) is None
    assert page("титульный") is None


@pytest.mark.asyncio
async def test_import_parameters_reach_the_records():
    """Размер куска задают при импорте: методичка и её скан режутся одинаково."""
    long_md = "# Оплата\n\n" + "Поставщик подтверждает оплату в личном кабинете. " * 40
    document = ParsedDocument(markdown=long_md, pictures=[ParsedPicture(data=PNG, caption=None, page=None)])

    default = await collect(IngestContext(rt=FakeRuntime(FakeDocParse(document))))
    smaller = await collect(
        IngestContext(rt=FakeRuntime(FakeDocParse(document)), params={"max_chunk_chars": 200})
    )

    assert len([r for r in smaller if isinstance(r, ArticleRecord)]) > len(
        [r for r in default if isinstance(r, ArticleRecord)]
    )
    # Подписи у картинки нет — в запись едет пустая строка, а не None: дальше её
    # читают как текст
    assert [r for r in default if isinstance(r, AssetRecord)][0].caption == ""


@pytest.mark.asyncio
async def test_pdf_without_pictures_still_gives_articles():
    """Иллюстраций в документе нет — текст всё равно должен доехать до базы."""
    document = ParsedDocument(markdown=MARKDOWN, pictures=[])
    records = await collect(IngestContext(rt=FakeRuntime(FakeDocParse(document))))

    assert not [r for r in records if isinstance(r, AssetRecord)]
    assert [r.origin.locator for r in records] == ["фрагмент 1", "фрагмент 2"]
