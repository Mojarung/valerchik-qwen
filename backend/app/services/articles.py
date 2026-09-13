"""Статьи базы знаний вне HTTP-слоя: роутер только переводит запрос и ответ."""

from dataclasses import dataclass
from typing import Any

from sqlalchemy import Select, desc, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import AuditLog, KBArticle
from app.schemas import ArticleBulkAction, ToKBRequest
from app.schemas.chat import ImageRef, SourceArticle
from app.schemas.kb import ArticleOrigin, ArticleOut, ArticlePage, CategoryCount
from app.services import audience as audience_of

SessionFactory = async_sessionmaker[AsyncSession]


async def public_article(
    session_factory: async_sessionmaker[AsyncSession], article_id: str
) -> SourceArticle | None:
    """Опубликованная статья для просмотра из цитаты в чате; каждый просмотр
    считается. Черновик или снятая с публикации — None: гость видит только то,
    что прошло проверку."""
    async with session_factory() as db:
        article = await db.get(KBArticle, article_id)
        if article is None or article.status != "active":
            return None
        article.views += 1
        await db.commit()
        return SourceArticle(
            id=article.id,
            question=article.question,
            answer=article.answer,
            category=article.category,
            line=article.line,
            # Иллюстрации из методички: текст инструкций ссылается на них прямо
            # («нажмите (Рисунок 26 (2))»), без картинки такой ответ бесполезен
            images=_images(article),
            updated_at=article.updated_at.isoformat(),
        )


async def create_operator_draft(session_factory: async_sessionmaker[AsyncSession], body: ToKBRequest) -> str:
    """Удачный ответ оператора — черновик статьи; в поиск он попадёт только
    после публикации администратором."""
    async with session_factory() as db:
        article = KBArticle(
            question=body.question,
            answer=body.answer,
            category=body.category,
            line=body.line,
            audience=body.audience,
            status="draft",
            source="operator",
        )
        db.add(article)
        await db.commit()
        return article.id


def _images(article: KBArticle) -> list[ImageRef]:
    return [ImageRef.model_validate(image) for image in article.images or []]


def _out(a: KBArticle) -> ArticleOut:
    return ArticleOut(
        id=a.id,
        question=a.question,
        answer=a.answer,
        category=a.category,
        keywords=a.keywords,
        line=a.line,
        status=a.status,
        source=a.source,
        audience=audience_of.normalize(a.audience),
        images=_images(a),
        origin=ArticleOrigin.model_validate(a.origin) if a.origin else None,
        views=a.views,
        helpful=a.helpful,
        not_helpful=a.not_helpful,
        updated_at=a.updated_at.isoformat(),
    )


DEFAULT_PAGE_SIZE = 20
# Больше двухсот строк таблица не показывает, а без потолка запрос с огромным
# page_size выгружал бы всю базу одним ответом.
MAX_PAGE_SIZE = 200


@dataclass(frozen=True)
class ArticleFilter:
    """Фильтры таблицы статей. Одни и те же у списка и у массовых действий:
    «применить ко всему по фильтру» обязано попадать ровно в то, что
    администратор видит в таблице."""

    query: str = ""
    category: str = ""
    status: str = ""
    audience: str = ""


def _filtered(filters: ArticleFilter) -> Select[tuple[KBArticle]]:
    """Выборка статей по фильтрам списка."""
    stmt = select(KBArticle)
    if filters.query:
        like = f"%{filters.query}%"
        stmt = stmt.where(or_(KBArticle.question.ilike(like), KBArticle.answer.ilike(like)))
    if filters.category:
        stmt = stmt.where(KBArticle.category == filters.category)
    if filters.status:
        stmt = stmt.where(KBArticle.status == filters.status)
    if filters.audience:
        # Пустая роль у старых строк — то же, что «для всех»
        if filters.audience == audience_of.ANY:
            stmt = stmt.where(or_(KBArticle.audience == audience_of.ANY, KBArticle.audience.is_(None)))
        else:
            stmt = stmt.where(KBArticle.audience == filters.audience)
    return stmt


async def list_page(
    session_factory: SessionFactory, filters: ArticleFilter, page: int = 1, page_size: int = DEFAULT_PAGE_SIZE
) -> ArticlePage:
    """Страница статей по фильтрам, свежие правки сверху, и общее число подходящих."""
    stmt = _filtered(filters)
    async with session_factory() as db:
        total = (await db.execute(select(func.count()).select_from(stmt.subquery()))).scalar() or 0
        rows = (
            (
                await db.execute(
                    stmt.order_by(desc(KBArticle.updated_at)).offset((page - 1) * page_size).limit(page_size)
                )
            )
            .scalars()
            .all()
        )
    return ArticlePage(total=total, page=page, items=[_out(a) for a in rows])


async def category_counts(session_factory: SessionFactory) -> list[CategoryCount]:
    async with session_factory() as db:
        rows = (
            await db.execute(
                select(KBArticle.category, func.count(KBArticle.id)).group_by(KBArticle.category)
            )
        ).all()
    return [CategoryCount(category=category, count=count) for category, count in rows]


async def detail(session_factory: SessionFactory, article_id: str) -> ArticleOut | None:
    async with session_factory() as db:
        article = await db.get(KBArticle, article_id)
        return _out(article) if article is not None else None


async def create(session_factory: SessionFactory, fields: dict[str, Any], actor: str) -> str:
    """Статья, заведённая руками; правка — в журнал аудита."""
    async with session_factory() as db:
        article = KBArticle(**fields, source="manual")
        db.add(article)
        db.add(
            AuditLog(
                actor=actor,
                action="kb.create",
                entity=article.id,
                payload={"question": fields["question"]},
            )
        )
        await db.commit()
        return article.id


async def update(
    session_factory: SessionFactory, article_id: str, fields: dict[str, Any], actor: str
) -> bool:
    """Переписать поля статьи; False — такой статьи нет."""
    async with session_factory() as db:
        article = await db.get(KBArticle, article_id)
        if article is None:
            return False
        for field, value in fields.items():
            setattr(article, field, value)
        db.add(
            AuditLog(
                actor=actor,
                action="kb.update",
                entity=article_id,
                payload={"question": fields["question"]},
            )
        )
        await db.commit()
    return True


async def remove(session_factory: SessionFactory, article_id: str, actor: str, purge: bool) -> bool:
    """Снять статью с публикации: в архив или (purge) насовсем. False — такой статьи нет."""
    async with session_factory() as db:
        article = await db.get(KBArticle, article_id)
        if article is None:
            return False
        db.add(
            AuditLog(
                actor=actor,
                action="kb.purge" if purge else "kb.archive",
                entity=article_id,
                payload={"question": article.question[:200]},
            )
        )
        if purge:
            await db.delete(article)
        else:
            article.status = "archived"
        await db.commit()
    return True


async def bulk(session_factory: SessionFactory, body: ArticleBulkAction, actor: str) -> list[str]:
    """Одно действие над пачкой статей — отмеченными или всеми по фильтру.
    Возвращает затронутые id; пусто — не нашлось ни одной."""
    if body.filter is not None:
        selector = _filtered(ArticleFilter(**body.filter.model_dump()))
    else:
        selector = select(KBArticle).where(KBArticle.id.in_(body.ids))
    async with session_factory() as db:
        rows = (await db.execute(selector)).scalars().all()
        if not rows:
            return []
        # Идентификаторы забираем до commit: после удаления объекты отвязаны от
        # сессии, и обращение к полю поднимет ObjectDeletedError.
        affected = [a.id for a in rows]
        for article in rows:
            if body.action == "purge":
                await db.delete(article)
            else:
                article.status = "active" if body.action == "publish" else "archived"
        db.add(
            AuditLog(
                actor=actor,
                action=f"kb.bulk.{body.action}",
                entity="batch",
                payload={"count": len(affected)},
            )
        )
        await db.commit()
    return affected


async def import_many(session_factory: SessionFactory, items: list[dict[str, Any]], actor: str) -> list[str]:
    """Пачка статей из импорта; возвращает их id."""
    async with session_factory() as db:
        rows = [KBArticle(**fields, source="import") for fields in items]
        db.add_all(rows)
        db.add(AuditLog(actor=actor, action="kb.import", entity="batch", payload={"count": len(items)}))
        # Идентификаторы известны до commit (клиентский default=new_id), поэтому
        # забираем их здесь — после commit объекты трогать незачем.
        new_ids = [a.id for a in rows]
        await db.commit()
    return new_ids
