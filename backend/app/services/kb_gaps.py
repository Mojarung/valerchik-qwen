"""Пробелы базы знаний: вопросы без ответа и их превращение в статьи."""

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import AuditLog, KBArticle, KBGap
from app.schemas import GapConvertRequest
from app.schemas.admin import GapOut

SessionFactory = async_sessionmaker[AsyncSession]
#: Сколько пробелов показывать: самые частые — сверху.
LIST_LIMIT = 100


async def open_gaps(session_factory: SessionFactory) -> list[GapOut]:
    async with session_factory() as db:
        rows = (
            (
                await db.execute(
                    select(KBGap).where(KBGap.status == "open").order_by(desc(KBGap.hits)).limit(LIST_LIMIT)
                )
            )
            .scalars()
            .all()
        )
    return [
        GapOut(
            id=g.id,
            query=g.query_example,
            hits=g.hits,
            best_score=g.best_score,
            last_seen=g.last_seen.isoformat(),
        )
        for g in rows
    ]


async def convert(
    session_factory: SessionFactory, gap_id: str, body: GapConvertRequest, actor: str
) -> str | None:
    """Вопрос без ответа -> статья БЗ, опубликованная или черновиком. None — пробела нет."""
    async with session_factory() as db:
        gap = await db.get(KBGap, gap_id)
        if gap is None:
            return None
        article = KBArticle(
            question=gap.query_example,
            answer=body.answer,
            category=body.category,
            line=body.line,
            audience=body.audience,
            status="active" if body.publish else "draft",
            source="gap",
        )
        db.add(article)
        gap.status = "converted"
        db.add(
            AuditLog(
                actor=actor,
                action="gap.convert",
                entity=gap_id,
                payload={"article_question": gap.query_example},
            )
        )
        await db.commit()
        return article.id


async def dismiss(session_factory: SessionFactory, gap_id: str) -> bool:
    """Пометить пробел отклонённым; False — такого пробела нет."""
    async with session_factory() as db:
        gap = await db.get(KBGap, gap_id)
        if gap is None:
            return False
        gap.status = "dismissed"
        await db.commit()
    return True
