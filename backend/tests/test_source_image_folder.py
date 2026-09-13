"""Источник image_folder: вопрос берётся из имени файла, ответом служит сама картинка.

Проверяем чистыми функциями и прямым вызовом источника: поднимать приложение ради
разбора имени файла незачем.
"""

import pytest

from app.ingest.blob import FileBlob
from app.ingest.records import ArticleRecord, AssetRecord
from app.ingest.registry import IngestContext
from app.ingest.sources.base import SourceMismatch
from app.ingest.sources.image_folder import ImageFolderSource, caption_from_name

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


def test_caption_translit_from_latin_name():
    """Имена приезжают латиницей, а база русская — подпись надо вернуть в кириллицу."""
    assert caption_from_name("01_kak-najti-zakaz.png") == "Как найти заказ"


def test_caption_keeps_cyrillic_name():
    """Русское имя транслитерировать нечего — только чистим разделители."""
    assert caption_from_name("Как выгрузить УПД.png") == "Как выгрузить УПД"


def test_caption_with_date_keeps_digits():
    """Ведущая нумерация «02.» уходит, а дата в имени — нет: это часть вопроса."""
    assert caption_from_name("02. zakaz-ot-2026-09-11.png") == "Заказ от 2026 09 11"
    # Год — не порядковый номер: четыре цифры отрезать нельзя
    assert caption_from_name("2026-09-11-oplata.png") == "2026 09 11 оплата"


def test_caption_keeps_english_camera_name():
    """Имя от камеры или системы — английское слово, а не транслит: не трогаем."""
    assert caption_from_name("screenshot 2026-09-11.png") == "Screenshot 2026 09 11"


def test_caption_survives_download_duplicate_tail():
    """Скачали второй раз — «(1)» в имени не должно оставлять вопрос латиницей."""
    assert caption_from_name("kak-najti-zakaz (1).png") == "Как найти заказ"
    assert caption_from_name("kak-najti-zakaz(2).png") == "Как найти заказ"


def test_caption_never_empty():
    """Имя из одной нумерации: пустой вопрос конвейер выбросит вместе со снимком."""
    assert caption_from_name("01_.png") == "01"
    assert caption_from_name("   .png").strip() != ""


def test_caption_from_nested_path():
    """В siblings имена приезжают с папкой — подпись собирается по последнему сегменту."""
    assert caption_from_name("screens/kak-najti-zakaz.png") == "Как найти заказ"


@pytest.mark.asyncio
async def test_sniff_takes_only_images():
    source = ImageFolderSource()
    assert await source.sniff(FileBlob("kak-vygruzit-upd.png", PNG)) == 0.8
    assert await source.sniff(FileBlob("faq.xlsx", b"PK\x03\x04")) == 0.0


@pytest.mark.asyncio
async def test_parse_gives_asset_and_visual_answer():
    source = ImageFolderSource()
    records = [r async for r in source.parse(FileBlob("01_kak-najti-zakaz.png", PNG), IngestContext())]

    assert len(records) == 2
    asset, article = records
    assert isinstance(asset, AssetRecord)
    assert asset.data == PNG
    assert asset.caption == "Как найти заказ"
    # Привязку делает конвейер через bind_assets — источник только называет файл
    assert asset.anchor == {"ref": "01_kak-najti-zakaz.png"}

    assert isinstance(article, ArticleRecord)
    assert article.question == "Как найти заказ"
    assert article.answer == ""  # ответ — сама картинка
    assert article.images == []
    assert article.origin.authored is True
    assert article.origin.file == "01_kak-najti-zakaz.png"
    assert article.origin.source_name == "image_folder"


@pytest.mark.asyncio
async def test_parse_takes_markup_from_params():
    """Разметка папки задаётся при импорте: у скриншотов её взять больше неоткуда."""
    ctx = IngestContext(params={"category": "ЭДО", "audience": "supplier", "line": 2})
    records = [r async for r in ImageFolderSource().parse(FileBlob("oplata.png", PNG), ctx)]

    article = records[1]
    assert (article.category, article.audience, article.line) == ("ЭДО", "supplier", 2)


@pytest.mark.asyncio
async def test_parse_refuses_empty_file():
    """Нулевой файл ответом быть не может: статья вышла бы с битой картинкой."""
    records = ImageFolderSource().parse(FileBlob("kak-najti-zakaz.png", b""), IngestContext())
    with pytest.raises(SourceMismatch):
        await anext(records)


@pytest.mark.asyncio
async def test_parse_refuses_non_image():
    """Прямой вызов мимо sniff не должен превращать текст в картинку."""
    records = ImageFolderSource().parse(FileBlob("faq.txt", b"text"), IngestContext())
    with pytest.raises(SourceMismatch):
        await anext(records)
