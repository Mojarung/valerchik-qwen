"""Файл → текст и картинки: markitdown для текста, docling для иллюстраций."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from app.ingest.figures import figure_numbers

if TYPE_CHECKING:
    from app.adapters.docparse import DocParseClient, ParsedPicture
    from app.runtime import AppRuntime

log = logging.getLogger(__name__)


async def extract_document(
    rt: AppRuntime, filename: str, raw: bytes, *, do_ocr: bool = False
) -> tuple[str, list[ParsedPicture]]:
    """Файл → (markdown, иллюстрации). PDF по возможности через docling.

    У PDF два пути. Основной — сервис разбора: модель размечает страницу и отдаёт
    иллюстрации вместе с подписями, из которых потом собирается связь «текст —
    картинка». Запасной — markitdown: текст тот же, картинок нет. Так стенд
    остаётся работоспособным, даже если тяжёлый профиль не поднят.
    """
    docparse: DocParseClient | None = getattr(rt, "docparse", None)
    if filename.lower().endswith(".pdf") and docparse is not None:
        parsed = await docparse.parse(filename, raw, do_ocr=do_ocr)
        if parsed is not None and parsed.markdown.strip():
            log.info(
                "Разбор «%s» через docling: %d стр., %d иллюстраций, %.1fс",
                filename,
                parsed.pages,
                len(parsed.pictures),
                parsed.seconds,
            )
            return parsed.markdown, parsed.pictures
    # markitdown синхронный и на большом PDF работает секунды: в цикле событий
    # воркера это заморозило бы и обновление прогресса, и остальные задачи.
    return await asyncio.to_thread(extract_text_from_file, filename, raw), []


SUPPORTED_EXTENSIONS = (
    ".txt",
    ".md",
    ".markdown",
    ".csv",
    ".html",
    ".htm",
    ".pdf",
    ".docx",
    ".doc",
    ".xlsx",
    ".xls",
    ".pptx",
    ".epub",
    ".json",
    ".xml",
)


def extract_text_from_file(filename: str, raw: bytes) -> str:
    """Методичка → Markdown через markitdown (microsoft/markitdown).

    Markdown, а не плоский текст: заголовки документа становятся `#`, таблицы —
    markdown-таблицами, и нарезка ниже работает по ним же. Библиотека полностью
    локальная — облачные бэкенды (Azure Document Intelligence, описание картинок
    через LLM) живут в отдельных экстрах, которые мы не ставим.

    txt и md отдаём как есть: гонять простой текст через конвертер незачем.
    """
    name = filename.lower()
    if name.endswith((".txt", ".md", ".markdown")):
        return raw.decode("utf-8", errors="replace")

    import io

    try:
        from markitdown import MarkItDown
    except ImportError as exc:  # pragma: no cover — зависимость обязательна, но пусть скажет
        raise ValueError("Разбор документов недоступен: не установлен markitdown") from exc

    if not name.endswith(SUPPORTED_EXTENSIONS):
        raise ValueError(f"Формат не поддерживается: {filename}")

    try:
        # stream_info через имя файла: без расширения markitdown угадывает тип по
        # содержимому и на старых .xls ошибается
        result = MarkItDown(enable_plugins=False).convert(
            io.BytesIO(raw),
            file_extension=Path(filename).suffix or None,
        )
    except Exception as exc:
        raise ValueError(f"Не удалось разобрать файл: {type(exc).__name__}: {exc}") from exc
    return result.text_content or ""


# Одновременных запросов к LLM за вопросами. Последовательно 1578 кусков — это
# больше часа; параллель упирается в лимиты провайдера, поэтому держим умеренной.
LLM_CONCURRENCY = 8


async def store_pictures(
    rt: AppRuntime, doc_slug: str, pictures: list[ParsedPicture]
) -> list[dict[str, Any]]:
    """Иллюстрации → постоянное хранилище. -> [{url, caption, page, figure}].

    Номер рисунка вытаскиваем из подписи: по нему кусок текста и находит свою
    картинку, потому что в тексте стоит ровно та же ссылка «(Рисунок 26)».
    """
    stored: list[dict[str, Any]] = []
    for index, picture in enumerate(pictures):
        key = f"kb/{doc_slug}/{index:04d}.png"
        try:
            url = await rt.media.put(key, picture.data, "image/png")
        except Exception:
            log.exception("Не удалось сохранить иллюстрацию %s", key)
            continue
        # Из подписи берём первую ссылку: «Рисунок 26 - Выбор сертификата» — это
        # рис:26, и ровно по этому ключу его найдёт текст со ссылкой «(Рисунок 26)».
        found = sorted(figure_numbers(picture.caption))
        image: dict[str, Any] = {
            "url": url,
            "caption": picture.caption,
            "page": picture.page,
            "figure": found[0] if found else None,
        }
        # под каким заголовком и после какого абзаца картинка стояла — для
        # привязки по соседству; в статью этот ключ не уходит
        anchor = {
            k: v for k, v in (("heading", picture.anchor_heading), ("before", picture.anchor_text)) if v
        }
        if anchor:
            image["anchor"] = anchor
        stored.append(image)
    return stored
