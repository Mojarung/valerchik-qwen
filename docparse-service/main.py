"""Разбор PDF с пониманием вёрстки: docling (CPU, локально, без единого внешнего вызова).

Зачем отдельным контейнером: docling тянет torch и веса моделей разметки — это
больше гигабайта, и держать их в образе API и воркера незачем, они от этого
только дольше собираются. Тот же приём, что у stt-service: тяжёлая ML-зависимость
живёт отдельно и поднимается профилем compose. Если сервис не поднят, импорт
методичек в API молча деградирует до markitdown — без картинок, но работает.

Что даёт docling поверх обычного извлечения текста: модель размечает блоки
страницы (заголовок раздела, текст, таблица, иллюстрация, подпись), поэтому
иллюстрация приезжает вместе со связанной с ней подписью — «Рисунок 26 – Выбор
сертификата». Восстанавливать эту связь по номерам и координатам не нужно.

Картинки пишем в общий том, а не отдаём в теле ответа: на трёхсотстраничной
инструкции их шестьсот, и base64 такого объёма в одном JSON — гарантированный
таймаут.
"""

import logging
import os
import re
import shutil
import tempfile
import time
import uuid
from pathlib import Path

from fastapi import FastAPI, Form, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool

logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))
log = logging.getLogger("docparse")

# Общий с воркером том: сюда кладём вырезанные иллюстрации, оттуда их забирает
# импорт и переносит в постоянное хранилище (S3 или том данных).
SPOOL_DIR = Path(os.environ.get("DOCPARSE_SPOOL", "/data/docparse"))
IMAGES_SCALE = float(os.environ.get("DOCPARSE_IMAGES_SCALE", "2"))
# Отбрасываем декоративную мелочь: логотипы в колонтитуле, линейки, иконки.
MIN_IMAGE_SIDE = int(os.environ.get("DOCPARSE_MIN_IMAGE_SIDE", "120"))
# Сколько часов держать выгруженные картинки, если импорт их не забрал
SPOOL_TTL_HOURS = float(os.environ.get("DOCPARSE_SPOOL_TTL_HOURS", "6"))

app = FastAPI(title="Valerchik DocParse")
_converters: dict[bool, object] = {}


def _converter(do_ocr: bool):
    """Конвертер на каждый режим OCR: сборка тянет модели, поэтому кэшируем.

    OCR по умолчанию выключен: в методичках Портала поставщиков текстовый слой
    есть, и распознавание давало ровно тот же результат вчетверо дольше.
    """
    if do_ocr not in _converters:
        from docling.datamodel.base_models import InputFormat
        from docling.datamodel.pipeline_options import PdfPipelineOptions
        from docling.document_converter import DocumentConverter, PdfFormatOption

        options = PdfPipelineOptions()
        options.do_ocr = do_ocr
        options.generate_picture_images = True
        options.images_scale = IMAGES_SCALE
        _converters[do_ocr] = DocumentConverter(
            format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=options)}
        )
        log.info("Конвертер собран (ocr=%s)", do_ocr)
    return _converters[do_ocr]


def _parse_sync(path: str, do_ocr: bool):
    """Синхронная часть разбора — уезжает в поток целиком, вместе со сборкой конвертера."""
    return _converter(do_ocr).convert(path).document


def _cleanup_spool() -> None:
    """Подчищаем выгрузки, которые никто не забрал: упавший импорт не должен
    оставлять гигабайты картинок в томе."""
    if not SPOOL_DIR.exists():
        return
    deadline = time.time() - SPOOL_TTL_HOURS * 3600
    for path in SPOOL_DIR.iterdir():
        try:
            if path.is_dir() and path.stat().st_mtime < deadline:
                shutil.rmtree(path, ignore_errors=True)
        except OSError:
            log.warning("Не удалось убрать %s", path)


@app.on_event("startup")
def sweep_on_startup() -> None:
    """Убираем выгрузки, оставшиеся от прошлой жизни контейнера.

    Клиент удаляет свой каталог сам, но при обрыве связи или падении импорта до
    этого не доходит, а сборка мусора внутри /parse срабатывает только на новом
    запросе — то есть никогда, если документы больше не грузят.
    """
    _cleanup_spool()


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "ready": bool(_converters), "spool": str(SPOOL_DIR)}


# Подпись «Рисунок 26 – Выбор сертификата» в методичках Портала сверстана
# обычным абзацем под картинкой. docling связывает её с картинкой не всегда, и
# подпись приезжала пустой — а без номера импорт не знал, к какому куску текста
# картинка относится: на стенде из полутора тысяч статей методичек ни одна не
# получила скриншота.
_CAPTION_LINE = re.compile(r"^\s*рис(?:унок|\.)?\s*№?\s*\d{1,3}", re.IGNORECASE)


def _reading_anchors(document) -> dict[str, dict]:
    """Порядок чтения: под каким заголовком и после какого абзаца стоит каждая
    картинка, и подпись, если она идёт следующим абзацем. Подпись с номером
    есть не у всех, а место в документе — у всех, и по нему импорт кладёт
    картинку к нужному куску текста."""
    anchors: dict[str, dict] = {}
    last_heading = ""
    last_text = ""
    waiting = ""  # картинка, чья подпись может оказаться следующим абзацем
    for item, _level in document.iterate_items():
        label = getattr(getattr(item, "label", None), "name", "")
        text = (getattr(item, "text", "") or "").strip()
        if label == "PICTURE":
            waiting = getattr(item, "self_ref", "")
            anchors[waiting] = {"heading": last_heading, "before": last_text}
            continue
        if waiting and text:
            # Смотрим только на первый абзац после картинки: дальше «Рисунок 27»
            # — уже подпись следующей картинки или ссылка из текста
            if label in ("TEXT", "PARAGRAPH", "CAPTION") and _CAPTION_LINE.match(text):
                anchors[waiting]["caption"] = text[:200]
            waiting = ""
        if label == "SECTION_HEADER":
            last_heading = text
            last_text = ""
        elif label in ("TEXT", "PARAGRAPH", "LIST_ITEM") and text and not _CAPTION_LINE.match(text):
            last_text = text[:160]
    return anchors


def _save_pictures(document, out_dir: Path, anchors: dict[str, dict]) -> list[dict]:
    """Иллюстрации — в каталог выгрузки, с подписью, страницей и местом в тексте.
    Декоративную мелочь и битые врезки пропускаем."""
    pictures: list[dict] = []
    for index, picture in enumerate(document.pictures):
        try:
            image = picture.get_image(document)
        except Exception:  # noqa: BLE001 — одна битая врезка не отменяет документ
            image = None
        if image is None or min(image.size) < MIN_IMAGE_SIDE:
            continue
        name = f"{index:04d}.png"
        image.save(out_dir / name)
        page = None
        if picture.prov:
            page = picture.prov[0].page_no
        anchor = dict(anchors.get(getattr(picture, "self_ref", ""), {}))
        caption = (picture.caption_text(document) or "").strip() or anchor.pop("caption", "")
        anchor.pop("caption", None)
        pictures.append({"file": name, "caption": caption, "page": page, "anchor": anchor})
    return pictures


@app.post("/parse")
async def parse(file: UploadFile, do_ocr: bool = Form(False)) -> dict:
    """PDF → markdown + иллюстрации с подписями.

    Возвращает `batch` — каталог в общем томе, откуда импорт заберёт картинки
    и удалит его за собой.
    """
    raw = await file.read()
    if not raw:
        raise HTTPException(422, "Пустой файл")

    _cleanup_spool()
    batch = uuid.uuid4().hex
    out_dir = SPOOL_DIR / batch
    out_dir.mkdir(parents=True, exist_ok=True)

    started = time.time()
    with tempfile.NamedTemporaryFile(suffix=Path(file.filename or "doc.pdf").suffix, delete=False) as tmp:
        tmp.write(raw)
        tmp_path = tmp.name
    try:
        # Разбор — синхронная работа процессора на минуты. В цикле событий он
        # заморозил бы весь сервис: даже healthcheck перестал бы отвечать, и docker
        # объявил бы контейнер больным посреди нормальной работы.
        document = await run_in_threadpool(_parse_sync, tmp_path, do_ocr)
    except Exception as exc:
        shutil.rmtree(out_dir, ignore_errors=True)
        log.exception("Разбор не удался")
        raise HTTPException(422, f"Не удалось разобрать документ: {type(exc).__name__}") from exc
    finally:
        os.unlink(tmp_path)

    pictures = _save_pictures(document, out_dir, _reading_anchors(document))

    headings = [
        item.text
        for item, _ in document.iterate_items()
        if getattr(getattr(item, "label", None), "name", "") == "SECTION_HEADER"
    ]
    elapsed = round(time.time() - started, 1)
    log.info(
        "Разобран %s: %d стр., %d иллюстраций, %.1fс",
        file.filename,
        len(document.pages),
        len(pictures),
        elapsed,
    )
    return {
        "markdown": document.export_to_markdown(),
        "pages": len(document.pages),
        "headings": headings,
        "tables": len(document.tables),
        "pictures": pictures,
        "batch": batch,
        "seconds": elapsed,
    }


@app.delete("/batch/{batch}")
def drop_batch(batch: str) -> dict:
    """Импорт забрал картинки — каталог больше не нужен."""
    if "/" in batch or "\\" in batch or ".." in batch:
        raise HTTPException(400, "Недопустимый идентификатор")
    shutil.rmtree(SPOOL_DIR / batch, ignore_errors=True)
    return {"status": "deleted"}
