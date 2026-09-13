"""Фоновые задачи: переиндексация БЗ, дообучение классификатора, аналитика отзывов.

В API-процессе runtime внедряется через set_runtime(); в отдельном воркере
он лениво создаётся при первой задаче (get_runtime).
"""

import asyncio
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from sqlalchemy import select

from app.models import ChatSession, KBArticle
from app.tasks.broker import broker

if TYPE_CHECKING:
    from collections.abc import Sequence

    from app.ingest.pipeline import ProgressCallback
    from app.runtime import AppRuntime

log = logging.getLogger(__name__)

_runtime = None
# Две задачи, стартовавшие одновременно, строили по своему рантайму: два набора
# подписчиков Redis, два диспетчера очереди и два слушателя перечитывания базы
# знаний. Замок делает создание однократным.
_runtime_lock = asyncio.Lock()


def set_runtime(rt: AppRuntime) -> None:
    global _runtime
    _runtime = rt


async def get_runtime() -> AppRuntime:
    global _runtime
    if _runtime is not None:
        return _runtime
    async with _runtime_lock:
        if _runtime is None:
            from app.config import Settings, get_settings
            from app.runtime import AppRuntime

            # Копия настроек, а не правка общего объекта: `get_settings` кэширует
            # его на процесс, и «воркер не сидирует» доставалось бы всем читателям.
            base = get_settings()
            settings = Settings(**{**base.model_dump(), "seed_on_startup": False})
            runtime = AppRuntime(settings)
            await runtime.start()
            _runtime = runtime
    return _runtime


@broker.task(task_name="kb.reindex")
async def reindex_kb_task() -> int:
    rt = await get_runtime()
    return await rt.kb.index_all()


@dataclass(frozen=True)
class _JobSnapshot:
    """Что задача знает о документе: заголовок, текст или файл в хранилище, параметры."""

    title: str
    text: str
    source_key: str
    filename: str
    params: dict[str, Any]


def _require_articles(result: dict[str, Any]) -> None:
    """Разбор, после которого в базе ничего не появилось, — отказ, а не успех."""
    from app.services.ingest import IngestFailed

    if not result["created"] and result.get("error"):
        raise IngestFailed(result["error"])
    # «Разобрали, но ничего не завели» — это тоже отказ, и молчать о нём
    # нельзя: в админке такая задача выглядит как «Готово, статей: 0», и
    # человек уходит демонстрировать базу, в которую ничего не приехало.
    if not any((result["created"], result.get("feedback"), result.get("dialogs"))):
        raise IngestFailed(
            f"источник {result.get('source') or '—'} разобрал файл "
            f"({result.get('records', 0)} записей), но ни одной статьи не вышло: "
            f"отброшено {result.get('skipped', 0)} — у записей нет вопроса "
            f"или нет ни текста ответа, ни картинки"
        )


async def _run_ingest(rt: AppRuntime, job: _JobSnapshot, on_progress: ProgressCallback) -> dict[str, Any]:
    """Разобрать задачу конвейером RAG: файл из хранилища или текст, вставленный в форму."""
    from app.rag.ingest import IngestOptions, ingest_bytes

    status = str((job.params or {}).get("status") or "active")
    if job.source_key:
        found = await rt.media.get(job.source_key)
        if found is None:
            raise FileNotFoundError(f"исходный файл {job.source_key} не найден в хранилище")
        filename, data = job.filename or job.source_key, found[0]
    else:
        filename, data = f"{job.title or 'document'}.md", (job.text or "").encode("utf-8")
    result = await ingest_bytes(
        rt, filename, data, IngestOptions(title=job.title or "", status=status, on_progress=on_progress)
    )
    _require_articles(result)
    return result


@broker.task(task_name="kb.ingest")
async def ingest_document_task(job_id: str) -> dict[str, Any]:
    """Импорт методички: минуты работы, поэтому и живёт в воркере, а не в запросе."""
    import contextlib
    from datetime import UTC, datetime

    from app.models import IngestJob

    rt = await get_runtime()

    async def set_fields(**values: Any) -> None:
        async with rt.db.session_factory() as db:
            job = await db.get(IngestJob, job_id)
            if job is None:
                return
            for key, value in values.items():
                setattr(job, key, value)
            await db.commit()

    async with rt.db.session_factory() as db:
        job = await db.get(IngestJob, job_id)
        if job is None:
            return {"skipped": True, "reason": "задача не найдена"}
        snapshot = _JobSnapshot(
            title=job.title,
            text=job.text,
            source_key=job.source_key,
            filename=job.filename,
            params=dict(job.params or {}),
        )

    await set_fields(status="running")

    async def on_progress(processed: int, total: int) -> None:
        await set_fields(processed=processed, total=total)

    try:
        result = await _run_ingest(rt, snapshot, on_progress)
    except Exception as exc:
        from app.services.ingest import IngestFailed

        log.exception("Импорт «%s» не удался", snapshot.title)
        # Наше объяснение показываем как есть, чужое падение — с типом: без него
        # «» или «(0, 'unknown')» в админке не говорят вообще ничего.
        text = str(exc) if isinstance(exc, IngestFailed) else f"{type(exc).__name__}: {exc}"
        await set_fields(status="error", error=text[:1000], finished_at=datetime.now(UTC))
        return {"status": "error"}
    finally:
        # Исходник нужен был только на время разбора; иначе каждая загрузка
        # навсегда оставляет в хранилище десятки мегабайт.
        if snapshot.source_key:
            with contextlib.suppress(Exception):
                await rt.media.delete_prefix(snapshot.source_key.rsplit("/", 1)[0])

    await set_fields(
        status="done",
        created_articles=result["created"],
        processed=result["created"],
        total=result["created"],
        # чистим текст ошибки: он мог остаться от прошлой попытки
        # или от уборщика брошенных задач
        error="",
        # текст больше не нужен, а методичка на 300 КБ в каждой строке
        # раздувает и таблицу, и выгрузки
        text="",
        finished_at=datetime.now(UTC),
    )
    return {"status": "done", "created": result["created"]}


# Меньше шести примеров — классификатору линий не на чем учиться
MIN_CLASSIFIER_SAMPLES = 6


async def _retrain_topics(rt: AppRuntime, articles: Sequence[KBArticle]) -> dict[str, Any]:
    """Модель темы: корпус выгрузки СТП плюс статьи БЗ, чья рубрика — тема справочника.

    Корпус кладёт в DATA_DIR команда `lines_eval train`: самой выгрузки на стенде
    нет, а без корпуса дообучать нечего. Статьи добавляют то, чего в выгрузке не
    было: новая инструкция приносит свои слова, и модель узнаёт вопросы по ней.
    """
    from app.services import lines
    from app.services.routing import load_corpus

    corpus = await asyncio.to_thread(load_corpus, rt.classifier.data_dir)
    if not corpus:
        return {"skipped": True, "reason": "нет корпуса выгрузки: сначала `app.tools.lines_eval train`"}
    extra = [
        (f"{a.question} {' '.join(a.keywords or [])}", topic)
        for a in articles
        if (topic := lines.normalize_topic(a.category or ""))
    ]
    samples = corpus + extra
    return await rt.classifier.train_topics(
        [text for text, _ in samples],
        [topic for _, topic in samples],
        extra={"corpus": len(corpus), "kb_articles": len(extra)},
    )


@broker.task(task_name="classifier.retrain")
async def retrain_classifier_task() -> dict[str, Any]:
    """Динамическое дообучение: модель линии — статьи БЗ + эскалации, модель темы — выгрузка + статьи."""
    rt = await get_runtime()
    async with rt.db.session_factory() as db:
        articles = (await db.execute(select(KBArticle).where(KBArticle.status == "active"))).scalars().all()
        escalated = (
            (
                await db.execute(
                    select(ChatSession).where(
                        ChatSession.escalated_at.is_not(None), ChatSession.line.is_not(None)
                    )
                )
            )
            .scalars()
            .all()
        )
    samples: list[tuple[str, int]] = [
        (f"{a.question} {' '.join(a.keywords or [])}", a.line) for a in articles
    ]
    samples += [(s.escalation_reason, s.line) for s in escalated if s.escalation_reason and s.line]
    result: dict[str, Any] = (
        await rt.classifier.train(samples)
        if len(samples) >= MIN_CLASSIFIER_SAMPLES
        else {"skipped": True, "reason": "мало данных"}
    )
    # Модель темы от числа статей не зависит: её основа — выгрузка обращений
    return {**result, "topics": await _retrain_topics(rt, articles)}


@broker.task(task_name="feedback.report")
async def feedback_report_task() -> dict[str, Any]:
    rt = await get_runtime()
    from app.services.feedback import build_feedback_report

    report = await build_feedback_report(rt)
    return {"count": report["summary"].get("count", 0)}
