"""Импорт документов в базу знаний: сборка результата разбора.

Разбор переехал в пакет `app.ingest`: нарезка, таблицы, вопросы, рисунки и
форматы файлов живут отдельными модулями. Здесь остаётся сборка — документ на
входе, статьи на выходе, — и реэкспорт публичных имён: их импортируют роутеры,
фоновые задачи и два десятка тестов.

Вопрос формулирует LLM, но она здесь не обязательна: без ключа берём заголовок
раздела, а если и его нет — первое предложение. База наполнится в любом случае,
просто с менее естественными формулировками.
"""

import asyncio
import logging
import re
from dataclasses import dataclass, fields, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

from app.ingest.chunking import (  # noqa: F401
    MAX_CHUNK_CHARS,
    MAX_HEADING_CHARS,
    MIN_CHUNK_CHARS,
    MIN_MEANINGFUL_CHARS,
    Section,
    answerless_heading,
    split_sections,
)
from app.ingest.documents import (  # noqa: F401
    LLM_CONCURRENCY,
    SUPPORTED_EXTENSIONS,
    extract_document,
    extract_text_from_file,
    store_pictures,
)
from app.ingest.figures import (  # noqa: F401
    MAX_IMAGES_PER_SECTION,
    attach_images,
    figure_numbers,
    strip_dangling_figures,
)
from app.ingest.questions import (
    extract_keywords,
    fallback_question,
    generate_question,
)
from app.ingest.tables import (  # noqa: F401
    MIN_TABLE_ROWS,
    TABLE_DOMINANCE,
    glossary_terms,
    parse_article_table,
)
from app.models import KBArticle, new_id
from app.services import audience as audience_of

if TYPE_CHECKING:
    from app.adapters.docparse import ParsedPicture
    from app.ingest.blob import FileBlob
    from app.ingest.pipeline import ProgressCallback
    from app.ingest.records import Record
    from app.ingest.registry import IngestContext
    from app.runtime import AppRuntime

log = logging.getLogger(__name__)


@dataclass
class IngestRequest:
    """Документ текстом и как его разобрать: название, категория, линия, статус, роль.

    Роль одна на весь документ: «Инструкция для заказчика» целиком адресована
    заказчикам. Линия None — её для каждого куска подскажет классификатор.
    """

    title: str
    text: str
    category: str | None = None
    line: int | None = None
    status: str = "draft"
    audience: str = "all"
    use_llm: bool = True
    max_chunk_chars: int = MAX_CHUNK_CHARS
    # Иллюстрации из сервиса разбора: привязываются к кускам по номерам рисунков
    pictures: list[ParsedPicture] | None = None

    @classmethod
    def from_params(cls, title: str, text: str, params: dict[str, Any]) -> IngestRequest:
        """Из параметров задачи. Флаги разбора файла (do_ocr и прочие) текстовому
        импорту не нужны — берём только своё."""
        known = {field.name for field in fields(cls)} - {"title", "text"}
        return cls(title=title, text=text, **{key: value for key, value in params.items() if key in known})


async def _line_of(rt: AppRuntime, line: int | None, text: str) -> int:
    """Линия из параметров, а без неё — от классификатора, который маршрутизирует обращения."""
    if line is not None:
        return line
    try:
        return (await rt.classifier.predict(text)).line
    except Exception:  # noqa: BLE001 — классификатор может быть ещё не обучен
        return 1


async def _ingest_table(
    rt: AppRuntime, rows: list[dict[str, Any]], request: IngestRequest, on_progress: ProgressCallback | None
) -> dict[str, Any]:
    """Строки готовой выгрузки → статьи. Без LLM: вопрос и ответ уже написаны людьми."""
    from app.ingest.pipeline import index_new

    created: list[dict[str, Any]] = []
    article_ids: list[str] = []
    total = len(rows)
    async with rt.db.session_factory() as db:
        for i, row in enumerate(rows, 1):
            section = Section(heading=row["question"], body=row["answer"])
            target_line = await _line_of(rt, request.line, section.text)
            article_id = new_id()
            db.add(
                KBArticle(
                    id=article_id,
                    question=row["question"],
                    answer=row["answer"],
                    category=request.category or request.title.strip() or "Импорт",
                    keywords=extract_keywords(section),
                    line=target_line,
                    audience=request.audience,
                    status=request.status,
                    source="ingest",
                    # Вопрос и ответ написаны человеком — такой ответ можно отдавать
                    # пользователю дословно (см. services/verbatim.py).
                    origin={
                        "file": request.title,
                        "locator": f"строка {i}",
                        "source": "qa_table",
                        "authored": True,
                    },
                )
            )
            article_ids.append(article_id)
            created.append(
                {
                    "question": row["question"],
                    "heading": None,
                    "chars": len(row["answer"]),
                    "line": target_line,
                }
            )
            if on_progress and (i % 25 == 0 or i == total):
                await on_progress(i, total)
        await db.commit()

    await index_new(rt, request.status, article_ids, request.title)
    log.info("Импорт «%s»: %d статей из готовой таблицы", request.title, len(created))
    return {
        "created": len(created),
        "status": request.status,
        "articles": created,
        "article_ids": article_ids,
        "mode": "table",
    }


async def ingest_document(
    rt: AppRuntime, request: IngestRequest, on_progress: ProgressCallback | None = None
) -> dict[str, Any]:
    """Документ → статьи БЗ. По умолчанию черновиками: формулировки проверяет человек.

    Линию поддержки для каждого куска предсказывает тот же классификатор, что
    маршрутизирует обращения, — импортированные статьи сразу попадают в нужную
    очередь, а не в общую кучу. Роль (`audience`) одна на весь документ:
    «Инструкция для заказчика» целиком адресована заказчикам.

    `on_progress(processed, total)` вызывается по мере готовности кусков: импорт
    методички идёт минутами, и администратору нужно видеть, что он движется.
    """
    from app.ingest.pipeline import index_new

    request = replace(request, audience=audience_of.normalize(request.audience))
    # Готовая выгрузка «вопрос — ответ» идёт прямым импортом: структура в ней уже
    # есть, и придумывать к ней вопросы значит портить авторские формулировки.
    table = parse_article_table(request.text)
    if table:
        return await _ingest_table(rt, table, request, on_progress)

    sections = split_sections(request.text, request.max_chunk_chars)
    if not sections:
        return {"created": 0, "articles": [], "skipped": "документ пуст"}

    attached = await _attach_pictures(rt, request, sections)
    prepared = await _prepare_sections(rt, request, sections, on_progress)
    created, article_ids = await _save_sections(rt, request, prepared)
    await index_new(rt, request.status, article_ids, request.title)
    log.info("Импорт «%s»: %d статей (%s)", request.title, len(created), request.status)
    return {
        "created": len(created),
        "status": request.status,
        "articles": created,
        "article_ids": article_ids,
        "images_attached": attached,
    }


async def _attach_pictures(rt: AppRuntime, request: IngestRequest, sections: list[Section]) -> int:
    """Иллюстрации из сервиса разбора — к кускам по номерам рисунков. Возвращает число привязок."""
    attached = 0
    if request.pictures:
        slug = re.sub(r"[^a-z0-9]+", "-", request.title.lower())[:40].strip("-") or "doc"
        stored = await store_pictures(rt, f"{slug}-{new_id()[:8]}", request.pictures)
        attached = attach_images(sections, stored)
        log.info("Импорт «%s»: иллюстраций %d, привязано к кускам %d", request.title, len(stored), attached)
    # Ссылки на рисунки, которых у куска нет, из текста убираем: без сервиса
    # разбора картинок нет вовсе, а с ним часть остаётся непривязанной
    dangling = sum(strip_dangling_figures(section) for section in sections)
    if dangling:
        log.info("Импорт «%s»: убрано ссылок на отсутствующие рисунки: %d", request.title, dangling)
    return attached


async def _prepare_sections(
    rt: AppRuntime, request: IngestRequest, sections: list[Section], on_progress: ProgressCallback | None
) -> list[dict[str, Any]]:
    """Вопрос и линия к каждому куску — параллельно, но не больше LLM_CONCURRENCY разом."""
    total = len(sections)
    done = 0
    semaphore = asyncio.Semaphore(LLM_CONCURRENCY)
    progress_lock = asyncio.Lock()

    async def prepare(section: Section) -> dict[str, Any]:
        nonlocal done
        async with semaphore:
            question = (
                (await generate_question(rt.dyn, section)) if request.use_llm else fallback_question(section)
            )
            target_line = await _line_of(rt, request.line, section.text)
        async with progress_lock:
            done += 1
            if on_progress and (done % 10 == 0 or done == total):
                await on_progress(done, total)
        return {"section": section, "question": question, "line": target_line}

    return list(await asyncio.gather(*(prepare(s) for s in sections)))


async def _save_sections(
    rt: AppRuntime, request: IngestRequest, prepared: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], list[str]]:
    """Куски с вопросами → статьи. Возвращает сводку созданного и id статей."""
    created: list[dict[str, Any]] = []
    article_ids: list[str] = []
    async with rt.db.session_factory() as db:
        for item in prepared:
            section: Section = item["section"]
            # id проставляем сами: после commit обращение к a.id перевыпустило бы
            # запрос в БД на каждую из полутора тысяч статей
            article_id = new_id()
            db.add(
                KBArticle(
                    id=article_id,
                    question=item["question"],
                    answer=section.body.strip(),
                    category=request.category or request.title.strip() or "Импорт",
                    keywords=extract_keywords(section),
                    line=item["line"],
                    audience=request.audience,
                    status=request.status,
                    source="ingest",
                    images=[{k: v for k, v in image.items() if k != "anchor"} for image in section.images],
                )
            )
            article_ids.append(article_id)
            created.append(
                {
                    "question": item["question"],
                    "heading": section.heading,
                    "chars": len(section.body),
                    "line": item["line"],
                    "images": len(section.images),
                }
            )
        await db.commit()
    return created, article_ids


class IngestFailed(ValueError):
    """Импорт не удался, и текст ошибки написан для человека.

    Отдельный тип нужен ровно затем, чтобы задача не приписывала к готовому
    объяснению имя класса: «ValueError: ни один источник не разобрал файл» в
    админке читает администратор, а не разработчик.
    """


def _normalized_params(params: dict[str, Any]) -> dict[str, Any]:
    """Параметры формы приводим к типам один раз, на входе в конвейер.

    Из HTML-формы всё приезжает строками: `line="2"` доехал бы до `KBArticle.line`
    строкой, а до классификатора — вовсе не доехал. Чинить это в каждом из семи
    источников значило бы развести поведение между ними.
    """
    clean = dict(params)
    line = clean.get("line")
    if isinstance(line, str):
        clean["line"] = int(line) if line.strip().isdigit() else None
    for flag in ("use_llm", "do_ocr"):
        value = clean.get(flag)
        if isinstance(value, str):
            clean[flag] = value.strip().lower() in ("1", "true", "yes", "on", "да")
    category = clean.get("category")
    clean["category"] = (category or "").strip() if isinstance(category, str) else ""
    return clean


# Сколько файлов из архива разбираем. Ограничение от zip-бомбы, а не от объёма
# данных: выгрузка на сотню файлов — норма, на десять тысяч — уже не выгрузка.
MAX_ARCHIVE_FILES = 500


async def _records_from_archive(raw: bytes, filename: str, ctx: IngestContext) -> list[Record]:
    """Архив → записи всех файлов внутри, каждый своим источником.

    Провенанс сохраняет путь внутри архива: «vygruzka.xlsx» в отчёте бесполезно,
    когда в архиве три файла с таким именем в разных папках.
    """
    import io
    import zipfile

    from app.ingest.blob import FileBlob
    from app.ingest.registry import parse_blob

    records: list[Record] = []
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            names = [name for name in archive.namelist() if not name.endswith("/")]
            for name in names[:MAX_ARCHIVE_FILES]:
                try:
                    data = archive.read(name)
                except Exception:
                    log.exception("Файл %s из архива %s не прочитан", name, filename)
                    continue
                found = await parse_blob(FileBlob(name=Path(name).name, data=data), ctx)
                for record in found:
                    record.origin.file = name
                records.extend(found)
            if len(names) > MAX_ARCHIVE_FILES:
                log.warning("Архив %s: разобрано %d файлов из %d", filename, MAX_ARCHIVE_FILES, len(names))
    except zipfile.BadZipFile as exc:
        raise IngestFailed(f"Архив не читается: {exc}") from exc
    log.info("Архив %s: файлов %d, записей %d", filename, len(names), len(records))
    return records


async def ingest_file(
    rt: AppRuntime,
    blob: FileBlob,
    *,
    title: str,
    params: dict[str, Any],
    on_progress: ProgressCallback | None = None,
) -> dict[str, Any]:
    """Файл → записи → база. Формат выбирает реестр источников, а не расширение.

    Список расширений остался подсказкой в интерфейсе, но отсекать по нему до
    разбора нельзя: на хакатоне приедет выгрузка с непривычным именем, и отказать
    ей за расширение значит не принять данные вовсе.
    """
    from app.ingest import sources  # noqa: F401 — импорт регистрирует источники
    from app.ingest.pipeline import persist
    from app.ingest.registry import IngestContext, parse_blob

    ctx = IngestContext(rt=rt, params=_normalized_params(params))
    if blob.name.lower().endswith(".zip"):
        # Организаторы выкладывают именно архив, и разведка его уже читает.
        # Требовать распаковки руками ради загрузки — терять минуты там, где их
        # нет, и делать это в тот момент, когда все смотрят на экран.
        records = await _records_from_archive(blob.data, blob.name, ctx)
    else:
        records = await parse_blob(blob, ctx)
    if not records:
        log.warning("Файл «%s» не разобрал ни один источник", blob.name)
        return {
            "created": 0,
            "feedback": 0,
            "dialogs": 0,
            "images": 0,
            "skipped": 0,
            "status": params.get("status", "draft"),
            "article_ids": [],
            "source": "",
            "error": "ни один источник не разобрал файл",
        }

    # Имя источника берём у самих записей, а не у самого уверенного в распознавании.
    # Уверенный мог отказаться на разборе (pdf_figures без поднятого профиля `docs`),
    # и файл достался следующему — а в отчёт уезжало имя того, кто его не читал.
    result = await persist(rt, records, title=title, params=params, on_progress=on_progress)
    result["source"] = records[0].origin.source_name
    result["records"] = len(records)
    return result
