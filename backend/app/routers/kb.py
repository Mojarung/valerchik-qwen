"""CRUD базы знаний + импорт/экспорт. Изменения сразу попадают в поиск (переиндексация на лету)."""

import hashlib
import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Annotated, Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, Response, UploadFile
from fastapi.responses import StreamingResponse

from app.adapters.storage import safe_filename
from app.ingest.text import fix_mojibake
from app.models import new_id
from app.rag.generic import parse_markdown
from app.rag.passages import Passage
from app.schemas import ArticleBulkAction, ArticleImport, ArticleIn, DocumentIngest
from app.schemas.base import Created
from app.schemas.kb import (
    ArticlePage,
    ArticleRemoved,
    ArticleUpdated,
    BulkResult,
    CategoryCount,
    ImportResult,
    IngestJobOut,
    IngestPreview,
    JobQueued,
    MediaUploaded,
    SectionPreview,
)
from app.security import AdminUser, StaffUser
from app.services import articles, ingest_queue
from app.services import audience as audience_of
from app.services.exports import export_kb_csv, export_kb_json
from app.tasks.jobs import ingest_document_task
from app.uploads import MAX_DOCUMENT_BYTES, read_limited

if TYPE_CHECKING:
    from app.runtime import AppRuntime

router = APIRouter(prefix="/api/kb", tags=["kb"])


def _article_fields(body: ArticleIn) -> dict[str, Any]:
    """Поля статьи из запроса в виде для ORM: картинки — списком словарей."""
    fields = body.model_dump()
    fields["images"] = [image.model_dump() for image in body.images]
    return fields


@router.get("", response_model=ArticlePage)
async def list_articles(
    request: Request,
    user: StaffUser,
    filters: Annotated[articles.ArticleFilter, Depends()],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=articles.MAX_PAGE_SIZE)] = articles.DEFAULT_PAGE_SIZE,
) -> ArticlePage:
    return await articles.list_page(request.app.state.rt.db.session_factory, filters, page, page_size)


@router.get("/categories", response_model=list[CategoryCount])
async def categories(request: Request, user: StaffUser) -> list[CategoryCount]:
    return await articles.category_counts(request.app.state.rt.db.session_factory)


@router.post("", response_model=Created)
async def create_article(request: Request, user: AdminUser, body: ArticleIn) -> Created:
    rt = request.app.state.rt
    article_id = await articles.create(rt.db.session_factory, _article_fields(body), actor=user.username)
    await rt.kb.index_article(article_id)
    return Created(id=article_id)


@router.put("/{article_id}", response_model=ArticleUpdated)
async def update_article(
    request: Request, article_id: str, user: AdminUser, body: ArticleIn
) -> ArticleUpdated:
    rt = request.app.state.rt
    fields = _article_fields(body)
    # Иллюстрации трогаем, только если их прислали: импорт JSON без поля
    # images не должен затирать картинки, привязанные при разборе
    # методички. Редактор присылает поле всегда — и пустой список из него
    # значит «убрать все».
    if "images" not in body.model_fields_set:
        del fields["images"]
    if not await articles.update(rt.db.session_factory, article_id, fields, actor=user.username):
        raise HTTPException(404, "Статья не найдена")
    await rt.kb.index_article(article_id)
    return ArticleUpdated(status="updated")


@router.delete("/{article_id}", response_model=ArticleRemoved)
async def archive_article(
    request: Request, article_id: str, user: AdminUser, purge: bool = Query(False)
) -> ArticleRemoved:
    """Снимает статью с публикации.

    По умолчанию — в архив: статья уходит из поиска, но остаётся видна в админке
    и её можно вернуть. `purge=true` удаляет строку насовсем — это нужно для
    мусора, который незачем хранить (обрывки страниц из неудачного импорта).
    Иллюстрации в хранилище при этом не трогаем: один файл может быть привязан
    к нескольким статьям того же документа.
    """
    rt = request.app.state.rt
    if not await articles.remove(rt.db.session_factory, article_id, actor=user.username, purge=purge):
        raise HTTPException(404, "Статья не найдена")
    await rt.kb.remove_article(article_id)
    return ArticleRemoved(status="deleted" if purge else "archived")


@router.post("/bulk", response_model=BulkResult)
async def bulk_action(request: Request, user: AdminUser, body: ArticleBulkAction) -> BulkResult:
    """Одно действие сразу над пачкой статей: отмеченными или всеми по фильтру.

    Разбор методички создаёт статьи десятками, и публиковать (или выбрасывать)
    их по одной — работа на полчаса. Индекс правим одним проходом: публикация
    идёт через index_many, снятие с публикации и удаление — через remove_many.
    """
    rt = request.app.state.rt
    target = body.filter
    # Удаление насовсем по пустому фильтру снесло бы всю базу одной кнопкой.
    # Архив обратим, поэтому его такой запрет не касается.
    if (
        body.action == "purge"
        and target is not None
        and not (target.query or target.category or target.status or target.audience)
    ):
        raise HTTPException(400, "Удаление по фильтру требует хотя бы одного условия")
    affected = await articles.bulk(rt.db.session_factory, body, actor=user.username)
    if not affected:
        raise HTTPException(
            404,
            "Под фильтр не попала ни одна статья"
            if target is not None
            else "Ни одной из выбранных статей не найдено",
        )
    if body.action == "publish":
        await rt.kb.index_many(affected)
    else:
        await rt.kb.remove_many(affected)
    return BulkResult(action=body.action, affected=len(affected))


@router.post("/import", response_model=ImportResult)
async def import_articles(request: Request, user: AdminUser, body: ArticleImport) -> ImportResult:
    rt = request.app.state.rt
    new_ids = await articles.import_many(
        rt.db.session_factory, [_article_fields(item) for item in body.items], actor=user.username
    )
    # Индексируем только привезённое. Полная пересборка коллекции ждала бы в
    # HTTP-запросе, пока эмбеддер прогонит всю базу: на нескольких тысячах статей
    # это дольше nginx-таймаута в 120 с — админ получал 504, хотя статьи уже
    # записаны, и повторная попытка плодила дубли.
    indexed = await rt.kb.index_many(new_ids)
    return ImportResult(imported=len(new_ids), indexed=indexed)


async def _queue_job(rt: AppRuntime, new: ingest_queue.NewIngestJob) -> JobQueued:
    """Ставит импорт в очередь; разбор идёт в воркере.

    В запросе документ не разбирается: PDF с разметкой страниц занимает секунды
    на страницу, и трёхсотстраничная инструкция не уложится ни в один разумный
    HTTP-таймаут.

    Исключение — lite-режим: там брокер выполняет задачу на месте (await_inplace),
    и запрос ждёт окончания импорта. Это сделано ради тестов и офлайн-демо, где
    отдельного воркера нет; в full всё идёт через Redis и отдельный контейнер.
    """
    job_id = await ingest_queue.create(rt.db.session_factory, new)
    # Строка задачи уже зафиксирована, поэтому падение брокера нельзя оставлять
    # молча: иначе задача навсегда останется в «в очереди», а админка будет
    # опрашивать её вечно.
    try:
        await ingest_document_task.kiq(job_id)
    except Exception as exc:
        await ingest_queue.mark_unqueued(rt.db.session_factory, job_id, type(exc).__name__)
        raise HTTPException(503, "Очередь задач недоступна, импорт не запущен") from exc
    return JobQueued(job_id=job_id, sections=new.total, status="queued")


def _text_passages(body: DocumentIngest) -> list[Passage]:
    """Нарезка вставленного текста тем же разбором, что делает задача импорта (app/tasks/jobs.py):
    текст — markdown-документ, разделы — по его заголовкам."""
    return parse_markdown(body.text, f"{body.title or 'document'}.md")


@router.post("/ingest", response_model=JobQueued)
async def ingest(request: Request, user: AdminUser, body: DocumentIngest) -> JobQueued:
    """Неформализованный документ текстом → статьи БЗ (по умолчанию черновиками)."""
    rt = request.app.state.rt
    # Текст уже здесь, поэтому нарезку можно посчитать сразу и показать в ответе
    sections = _text_passages(body)
    if not sections:
        raise HTTPException(422, "Из документа не удалось выделить ни одного фрагмента")
    return await _queue_job(
        rt,
        ingest_queue.NewIngestJob(
            actor=user.username,
            title=body.title,
            filename="",
            text=body.text,
            total=len(sections),
            params={
                "category": body.category,
                "line": body.line,
                "status": body.status,
                "audience": body.audience,
                "use_llm": body.use_llm,
                "max_chunk_chars": body.max_chunk_chars,
            },
        ),
    )


@dataclass
class IngestFileForm:
    """Поля формы загрузки, кроме самого файла."""

    title: Annotated[str, Form()] = ""
    category: Annotated[str, Form()] = ""
    status: Annotated[str, Form()] = "draft"
    audience: Annotated[str, Form()] = "all"
    use_llm: Annotated[bool, Form()] = True
    do_ocr: Annotated[bool, Form()] = False

    def job_params(self) -> dict[str, Any]:
        """Параметры задачи разбора: статус и роль — только из допустимых значений."""
        return {
            "category": self.category.strip() or None,
            "status": self.status if self.status in ("draft", "active") else "draft",
            "audience": audience_of.normalize(self.audience),
            "use_llm": self.use_llm,
            "do_ocr": self.do_ocr,
        }


@router.post("/ingest/file", response_model=JobQueued)
async def ingest_file(
    request: Request,
    user: AdminUser,
    file: Annotated[UploadFile, File()],
    form: Annotated[IngestFileForm, Depends()],
) -> JobQueued:
    """Файл → записи базы знаний. Формат определяет реестр источников.

    Расширение больше не приговор: выгрузка с непривычным именем («dump.dat»,
    «faq.jsonl») попадёт к подходящему источнику, а если не возьмётся никто —
    задача завершится с понятной ошибкой. Отказывать до разбора значит не
    принять данные, которых мы заранее не видели.

    PDF при поднятом профиле `docs` разбирается с разметкой страниц и картинками,
    иначе — текстом. `do_ocr` нужен только для сканов: в обычных методичках
    текстовый слой есть, и распознавание вчетверо удлиняет разбор без пользы.
    """
    raw = await read_limited(file, MAX_DOCUMENT_BYTES, f"Файл больше {MAX_DOCUMENT_BYTES // 1024 // 1024} МБ")
    filename = file.filename or "document.txt"
    if not raw.strip():
        raise HTTPException(422, "Файл пуст")

    rt = request.app.state.rt
    # Заголовок из multipart-формы приезжает байтами: клиент кодировку не
    # помечает, и кириллица показывалась в админке мусором вида «Âûãðóçêà».
    doc_title = fix_mojibake(form.title.strip()) or fix_mojibake(filename.rsplit(".", 1)[0])
    # Файл кладём в хранилище: воркер живёт в отдельном контейнере, а гнать
    # мегабайты через строку в БД — плохая идея.
    source_key = f"ingest/{new_id()}/{safe_filename(filename, 'document')}"
    try:
        await rt.media.put(source_key, raw)
    except Exception as exc:
        raise HTTPException(503, "Хранилище недоступно, загрузка невозможна") from exc

    return await _queue_job(
        rt,
        ingest_queue.NewIngestJob(
            actor=user.username,
            title=doc_title,
            filename=filename,
            source_key=source_key,
            content_sha256=hashlib.sha256(raw).hexdigest(),
            params=form.job_params(),
        ),
    )


MAX_IMAGE_BYTES = 5 * 1024 * 1024
# Тип берём не из заголовка запроса, а из первых байт: файл потом отдаётся
# внутрь страницы как картинка, и подделанный тип означал бы чужой код в нашем
# origin
_IMAGE_SIGNATURES: tuple[tuple[bytes, str, str], ...] = (
    (b"\x89PNG\r\n\x1a\n", "image/png", "png"),
    (b"\xff\xd8\xff", "image/jpeg", "jpg"),
    (b"GIF87a", "image/gif", "gif"),
    (b"GIF89a", "image/gif", "gif"),
)


def _sniff_image(raw: bytes) -> tuple[str, str] | None:
    for magic, content_type, ext in _IMAGE_SIGNATURES:
        if raw.startswith(magic):
            return content_type, ext
    if raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
        return "image/webp", "webp"
    return None


@router.post("/media", response_model=MediaUploaded)
async def upload_media(request: Request, user: AdminUser, file: UploadFile = File(...)) -> MediaUploaded:
    """Иллюстрация к статье руками: скриншот интерфейса, когда сервис разбора
    методичек не поднят или картинку не удалось привязать автоматически."""
    raw = await read_limited(file, MAX_IMAGE_BYTES, f"Картинка больше {MAX_IMAGE_BYTES // 1024 // 1024} МБ")
    kind = _sniff_image(raw)
    if kind is None:
        raise HTTPException(415, "Нужна картинка: PNG, JPEG, WebP или GIF")
    content_type, ext = kind
    rt = request.app.state.rt
    key = f"kb/manual/{new_id()}.{ext}"
    try:
        url = await rt.media.put(key, raw, content_type)
    except Exception as exc:
        raise HTTPException(503, "Хранилище недоступно, загрузка невозможна") from exc
    return MediaUploaded(url=url, caption="", page=None)


@router.get(
    "/media/{key:path}",
    response_class=Response,
    responses={
        200: {
            "content": {"image/png": {}, "image/jpeg": {}, "application/octet-stream": {}},
            "description": "Иллюстрация методички или вложение оператора",
        }
    },
)
async def media(request: Request, key: str) -> Response:
    """Файл из хранилища: иллюстрация методички или вложение оператора.

    Без авторизации: картинка идёт вместе с ответом в пользовательском чате, а
    S3 может быть закрыт от интернета. Ключ содержит случайный идентификатор,
    поэтому ссылку не подобрать — но по ссылке файл откроет кто угодно, и это
    осознанный размен.
    """
    if ".." in key:
        raise HTTPException(400, "Недопустимый путь")
    found = await request.app.state.rt.media.get(key)
    if found is None:
        raise HTTPException(404, "Файл не найден")
    data, content_type = found
    # Содержимое неизменяемо: ключ включает идентификатор импорта, перезаписи нет
    headers = {
        "Cache-Control": "public, max-age=604800, immutable",
        # Браузер не должен угадывать тип: файл лежит на нашем домене, и
        # угаданный text/html из чужого вложения — это исполнение чужого кода
        # в нашем origin.
        "X-Content-Type-Options": "nosniff",
    }
    if not content_type.startswith("image/"):
        # Документы только скачиваем, не показываем внутри страницы
        headers["Content-Disposition"] = "attachment"
    return Response(content=data, media_type=content_type, headers=headers)


@router.get("/ingest/jobs", response_model=list[IngestJobOut])
async def ingest_jobs(
    request: Request, user: StaffUser, limit: int = Query(20, ge=1, le=100)
) -> list[IngestJobOut]:
    # ge=1 обязателен: без нижней границы limit=-1 доезжает до SQL как LIMIT -1,
    # и PostgreSQL отвечает ошибкой вместо выборки.
    return await ingest_queue.recent(request.app.state.rt.db.session_factory, limit)


@router.get("/ingest/jobs/{job_id}", response_model=IngestJobOut)
async def ingest_job(request: Request, user: StaffUser, job_id: str) -> IngestJobOut:
    job = await ingest_queue.get(request.app.state.rt.db.session_factory, job_id)
    if job is None:
        raise HTTPException(404, "Задача импорта не найдена")
    return job


@router.post("/ingest/preview", response_model=IngestPreview)
async def ingest_preview(request: Request, user: AdminUser, body: DocumentIngest) -> IngestPreview:
    """Как документ ляжет в базу — без записи и без обращений к LLM."""
    sections = _text_passages(body)
    return IngestPreview(
        sections=len(sections),
        preview=[
            SectionPreview(heading=s.title, chars=len(s.text), excerpt=s.text[:220]) for s in sections[:20]
        ],
    )


@router.get(
    "/export.csv",
    response_class=StreamingResponse,
    responses={200: {"content": {"text/csv": {}}, "description": "База знаний в CSV"}},
)
async def export_csv(request: Request, user: StaffUser) -> StreamingResponse:
    return StreamingResponse(
        export_kb_csv(request.app.state.rt),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=knowledge_base.csv"},
    )


@router.get(
    "/export.json",
    response_class=StreamingResponse,
    responses={200: {"content": {"application/json": {}}, "description": "База знаний JSON-файлом"}},
)
async def export_json(request: Request, user: StaffUser) -> StreamingResponse:
    data = await export_kb_json(request.app.state.rt)
    payload = json.dumps(data, ensure_ascii=False, indent=2)
    return StreamingResponse(
        iter([payload]),
        media_type="application/json",
        headers={"Content-Disposition": "attachment; filename=knowledge_base.json"},
    )
