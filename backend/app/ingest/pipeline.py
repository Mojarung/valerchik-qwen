"""Записи → база. Единственное место, которое пишет результат разбора.

Разделение с источниками не косметическое: пока запись в базу жила внутри
разбора, добавить формат значило тронуть и то и другое. Теперь источник ничего
не знает про SQLAlchemy, а конвейер — про markitdown.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from sqlalchemy import select

from app.ingest.assets import bind_assets, has_visual_answer, store_assets
from app.ingest.chunking import Section
from app.ingest.questions import extract_keywords
from app.ingest.records import (
    ArticleRecord,
    AssetRecord,
    DialogRecord,
    FeedbackRecord,
    ImageRef,
    Record,
)
from app.models import ChatSession, Feedback, KBArticle, Message, User, new_id
from app.services import audience as audience_of
from app.services import phrasings

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.runtime import AppRuntime

log = logging.getLogger(__name__)

# Канал импортированных диалогов и отзывов. Аналитика и рейтинг операторов его
# отфильтруют: чужая выгрузка на тысячу обращений иначе подменила бы собой
# статистику стенда. Обучению классификатора линий он, наоборот, полезен.
IMPORT_CHANNEL = "import"
PROGRESS_EVERY = 20

ProgressCallback = Callable[[int, int], Awaitable[None]]


async def index_new(rt: AppRuntime, status: str, article_ids: list[str], title: str) -> None:
    """Проиндексировать только что созданные статьи, если они сразу идут в поиск.

    Полная переиндексация на объёме импорта — минуты работы эмбеддера впустую.
    Сбой индексации импорт не отменяет: статьи уже в базе, и пометить задачу
    упавшей значило бы соврать — при повторе она создала бы дубли. Индекс
    чинится кнопкой «Переиндексировать» в админке.
    """
    if status != "active" or not article_ids:
        return
    try:
        await rt.kb.index_many(article_ids)
    except Exception:
        log.exception("Статьи импорта «%s» созданы, но не проиндексированы", title)


async def unindex_replaced(rt: AppRuntime, article_ids: list[str], title: str) -> None:
    """Убрать из индекса векторы прошлой версии документа — все точки статьи,
    вместе с точками её формулировок.

    Поиск архивные статьи и так отбрасывает, но точки в хранилище оставались:
    на стенде после трёх переимпортов их набралась тысяча, и число статей в
    индексе в /api/health перестало совпадать с числом статей в базе. Сбой здесь
    импорт не отменяет — по той же причине, что и в index_new.
    """
    if not article_ids:
        return
    try:
        await rt.kb.remove_many(article_ids)
    except Exception:
        log.exception("Импорт «%s»: векторы прошлой версии не удалены", title)


@dataclass(frozen=True)
class _ArticleBatch:
    """Общее у всех статей одного импорта: документ, статус и ключ версии."""

    title: str
    status: str
    doc_key: str


async def _resolve_operator(db: AsyncSession, name: str, cache: dict[str, str]) -> str | None:
    """Имя специалиста из выгрузки → пользователь в базе.

    Отзывы приезжают с именем, а не с идентификатором, и без сопоставления
    рейтинг специалистов посчитать не по кому. Незнакомое имя заводим
    неактивным пользователем: он нужен как адресат оценок, а не для входа.
    """
    key = name.strip().lower()
    if not key:
        return None
    if key in cache:
        return cache[key]

    found = (
        (
            await db.execute(
                select(User).where((User.display_name == name) | (User.username == name)),
            )
        )
        .scalars()
        .first()
    )
    if found is None:
        found = User(
            id=new_id(),
            username=f"import-{new_id()[:8]}",
            password_hash="",
            role="operator",
            display_name=name.strip(),
            is_active=False,
        )
        db.add(found)
        await db.flush()
    cache[key] = found.id
    return found.id


async def _line_for(rt: AppRuntime, record: ArticleRecord) -> int:
    """Линию подсказывает тот же классификатор, что маршрутизирует обращения."""
    if record.line:
        return record.line
    try:
        return (await rt.classifier.predict(f"{record.question}\n{record.answer}")).line
    except Exception:  # noqa: BLE001 — классификатор может быть ещё не обучен
        return 1


def document_key(title: str) -> str:
    """Ключ документа для замены прошлой версии: нормализованный заголовок.

    Заголовок — то, чем документ назван в админке, и то, что видит человек в
    списке задач. Хеш содержимого для этого не годится: правленая методичка
    имеет другой хеш и перестала бы заменять свою же прошлую версию.

    Юникод приводим к одной форме (NFC): имя файла из браузера на macOS несёт
    «й» буквой и отдельным значком краткости. Выглядит так же, а ключ другой —
    и на стенде переимпорт «Темы_подтемы_обращений» не заменил прошлую версию,
    а лёг рядом с ней.

    Подчёркивание и дефис — те же пробелы: форма загрузки берёт название из
    имени файла и меняет «_» на пробел, а сборка базы из каталога берёт имя
    как есть. Один и тот же файл через кнопку ложился рядом с собой.
    """
    text = _SEPARATORS.sub(" ", unicodedata.normalize("NFC", title))
    return " ".join(text.split()).lower()[:64]


_SEPARATORS = re.compile(r"[_\-‐-―]+")


async def persist(
    rt: AppRuntime,
    records: list[Record],
    *,
    title: str,
    params: dict[str, Any],
    on_progress: ProgressCallback | None = None,
) -> dict[str, Any]:
    """Сохранить записи по своим таблицам. Возвращает сводку импорта."""
    articles = [r for r in records if isinstance(r, ArticleRecord)]
    assets = [r for r in records if isinstance(r, AssetRecord)]
    feedback = [r for r in records if isinstance(r, FeedbackRecord)]
    dialogs = [r for r in records if isinstance(r, DialogRecord)]

    images = await _bind_record_assets(rt, articles, assets, title)
    batch = _ArticleBatch(title=title, status=params.get("status", "draft"), doc_key=document_key(title))
    async with rt.db.session_factory() as db:
        replaced = await _archive_previous(db, batch.doc_key, title)
        article_ids, skipped = await _add_articles(db, rt, articles, batch, on_progress)
        await _add_feedback(db, feedback, title)
        _add_dialogs(db, dialogs)
        await db.commit()

    await unindex_replaced(rt, replaced, title)
    await index_new(rt, batch.status, article_ids, title)
    log.info(
        "Импорт «%s»: статей %d, отзывов %d, диалогов %d, пропущено %d",
        title,
        len(article_ids),
        len(feedback),
        len(dialogs),
        skipped,
    )
    return {
        "created": len(article_ids),
        "replaced": len(replaced),
        "feedback": len(feedback),
        "dialogs": len(dialogs),
        "images": len(images),
        "skipped": skipped,
        "status": batch.status,
        "article_ids": article_ids,
    }


async def _bind_record_assets(
    rt: AppRuntime, articles: list[ArticleRecord], assets: list[AssetRecord], title: str
) -> list[ImageRef]:
    """Сохранить картинки выгрузки и привязать их к записям. Возвращает сохранённые."""
    if not assets:
        return []
    images = await store_assets(rt, new_id()[:8], assets)
    # Явные ссылки: источник сам сказал, какая картинка к какой записи. Для
    # папки скриншотов это имя файла, общее у картинки и у статьи.
    refs = {id(record): [record.origin.file] for record in articles if record.origin.file}
    bound = bind_assets(articles, images, refs=refs)
    log.info("Импорт «%s»: картинок %d, привязано к записям %d", title, len(images), bound)
    return images


async def _archive_previous(db: AsyncSession, doc_key: str, title: str) -> list[str]:
    """Прошлая версия того же документа — в архив. Возвращает id ушедших статей.

    Id нужны вызывающему, чтобы убрать их векторы из индекса: архив в базе сам
    по себе точку в векторном хранилище не удаляет.

    Тот же документ, залитый повторно, — это новая версия, а не второй
    экземпляр. Прошлые статьи уходят в архив до вставки новых: иначе поиск
    видел два одинаковых ответа и цитировал случайный, а редактор правил копию,
    которую агент не использовал. Архив, а не удаление: ссылки из старых
    диалогов должны продолжать открываться.
    """
    if not doc_key:
        return []
    # Ключ у живых статей сверяем через тот же document_key, а не равенством
    # в SQL: статьи прошлых импортов хранят ключ, посчитанный прежней версией
    # функции («темы_подтемы_обращений»), и новая версия документа иначе их не
    # узнала бы. Живых статей — тысячи, пары «id, ключ» это не нагрузка.
    stored = (
        await db.execute(
            select(KBArticle.id, KBArticle.doc_key).where(
                KBArticle.doc_key != "", KBArticle.status != "archived"
            )
        )
    ).all()
    ids = [article_id for article_id, key in stored if document_key(key) == doc_key]
    previous = (
        (await db.execute(select(KBArticle).where(KBArticle.id.in_(ids)))).scalars().all() if ids else []
    )
    for article in previous:
        article.status = "archived"
    if previous:
        log.info("Импорт «%s»: прошлая версия документа — %d статей в архив", title, len(previous))
    return [article.id for article in previous]


def _origin(record: ArticleRecord) -> dict[str, Any]:
    """Происхождение статьи для колонки origin: вместе с формулировками вопроса.

    Формулировки кладём сюда, а не отдельной колонкой: это JSON, и схема базы
    на живом стенде от них не меняется.
    """
    origin = record.origin.as_dict()
    if record.phrasings:
        origin[phrasings.ORIGIN_KEY] = list(record.phrasings)
    return origin


async def _add_articles(
    db: AsyncSession,
    rt: AppRuntime,
    articles: list[ArticleRecord],
    batch: _ArticleBatch,
    on_progress: ProgressCallback | None,
) -> tuple[list[str], int]:
    """Записи-статьи → строки базы. Возвращает id созданных и число пропущенных."""
    article_ids: list[str] = []
    skipped = 0
    total = len(articles)
    for index, record in enumerate(articles, start=1):
        has_text = bool(record.answer.strip())
        if not record.question.strip() or not (has_text or has_visual_answer(record)):
            skipped += 1
            continue
        article_id = new_id()
        db.add(
            KBArticle(
                id=article_id,
                question=record.question.strip(),
                answer=record.answer.strip(),
                category=record.category.strip() or batch.title.strip() or "Импорт",
                keywords=record.keywords
                or extract_keywords(Section(heading=record.question, body=record.answer)),
                images=[image.as_dict() for image in record.images],
                line=await _line_for(rt, record),
                audience=audience_of.normalize(record.audience),
                status=batch.status,
                source="import",
                origin=_origin(record),
                doc_key=batch.doc_key,
            )
        )
        article_ids.append(article_id)
        if on_progress and (index % PROGRESS_EVERY == 0 or index == total):
            await on_progress(index, total)
    return article_ids, skipped


async def _add_feedback(db: AsyncSession, feedback: list[FeedbackRecord], title: str) -> None:
    """Отзывы выгрузки. Отзыву нужен диалог: session_id — внешний ключ, поэтому
    заводим один контейнер на всю выгрузку вместо тысячи пустых сессий."""
    if not feedback:
        return
    container = ChatSession(id=new_id(), channel=IMPORT_CHANNEL, status="resolved", topic=title.strip()[:128])
    db.add(container)
    await db.flush()
    operator_cache: dict[str, str] = {}
    for review in feedback:
        db.add(
            Feedback(
                session_id=container.id,
                specialist_kind="operator" if review.operator else "ai",
                operator_id=await _resolve_operator(db, review.operator, operator_cache),
                rating=max(1, min(5, int(review.rating))),
                comment=review.comment,
            )
        )


def _add_dialogs(db: AsyncSession, dialogs: list[DialogRecord]) -> None:
    """Диалоги выгрузки — обращения импортного канала вместе с перепиской."""
    for dialog in dialogs:
        chat = ChatSession(
            id=new_id(),
            channel=IMPORT_CHANNEL,
            status="resolved" if dialog.resolved else "active",
            line=dialog.line,
        )
        db.add(chat)
        for role, content in dialog.messages:
            db.add(Message(id=new_id(), session_id=chat.id, role=role, content=content))
