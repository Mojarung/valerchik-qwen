"""Сценарии, заведённые из админки: чтение из БД и наложение на каталог.

Заводской набор лежит файлом в репозитории (app/guides/portal.json) — его
правит разработчик вместе с кодом. Всё, что администратор записал с портала
или сочинил в панели, живёт в БД: селекторы переживают перезапуск, правки
видны всем инстансам, а файл остаётся эталоном, к которому можно вернуться
удалением записи.

Модуль намеренно отделён от services/guides.py: там чистый подбор сценария к
вопросу, без ORM и без сессий, и его зовут из чата на каждое сообщение.
"""

import logging
from typing import TYPE_CHECKING, Any

from sqlalchemy import select

from app.models import AuditLog, GuideRecord
from app.services import audience as audience_of
from app.services import guides

if TYPE_CHECKING:
    from app.db import SessionFactory

log = logging.getLogger(__name__)

#: Правка сценария в одном инстансе должна долететь до остальных — как у БЗ и настроек.
GUIDES_CHANNEL = "guides:changed"


def to_dict(row: GuideRecord) -> dict[str, Any]:
    """Запись БД в вид каталога (плюс служебные поля для админки)."""
    return {
        "id": row.id,
        "title": row.title,
        "audience": row.audience,
        "questions": list(row.questions or []),
        "steps": list(row.steps or []),
        "enabled": row.enabled,
        "source": row.source,
        "author": row.author,
        "updated_at": row.updated_at.isoformat() if row.updated_at else "",
    }


async def load_all(session_factory: SessionFactory) -> list[dict[str, Any]]:
    async with session_factory() as db:
        rows = (await db.execute(select(GuideRecord).order_by(GuideRecord.id))).scalars().all()
    return [to_dict(row) for row in rows]


async def refresh(session_factory: SessionFactory) -> int:
    """Перечитать записи и наложить их на каталог. Возвращает число записей."""
    records = await load_all(session_factory)
    guides.set_overrides(records)
    return len(records)


async def save(session_factory: SessionFactory, guide_id: str, payload: dict[str, Any], actor: str) -> None:
    """Создать или переписать запись сценария; правка — в журнал аудита."""
    async with session_factory() as db:
        record = await db.get(GuideRecord, guide_id)
        if record is None:
            db.add(GuideRecord(id=guide_id, **payload))
        else:
            for field, value in payload.items():
                setattr(record, field, value)
        db.add(
            AuditLog(
                actor=actor,
                action="guide.save",
                entity=guide_id,
                payload={"steps": len(payload["steps"]), "audience": payload["audience"]},
            )
        )
        await db.commit()


async def delete(session_factory: SessionFactory, guide_id: str, actor: str) -> bool:
    """Убрать запись админки; False — такой записи не было."""
    async with session_factory() as db:
        record = await db.get(GuideRecord, guide_id)
        if record is None:
            return False
        await db.delete(record)
        db.add(AuditLog(actor=actor, action="guide.delete", entity=guide_id))
        await db.commit()
    return True


async def set_enabled(session_factory: SessionFactory, guide_id: str, enabled: bool, actor: str) -> bool:
    """Включить или выключить сценарий, в том числе заводской; False — сценария нет."""
    source = guides.get(guide_id) or next(
        (g for g in guides.factory()["guides"] if g["id"] == guide_id), None
    )
    async with session_factory() as db:
        record = await db.get(GuideRecord, guide_id)
        if record is None:
            if source is None:
                return False
            # Выключение заводского — это запись-копия с погашенным флагом:
            # файл в репозитории мы из админки не трогаем.
            record = GuideRecord(
                id=guide_id,
                title=source.get("title", ""),
                audience=source.get("audience", audience_of.ANY),
                questions=source.get("questions") or [],
                steps=source.get("steps") or [],
                source="admin",
                author=actor,
            )
            db.add(record)
        record.enabled = enabled
        db.add(AuditLog(actor=actor, action="guide.enabled", entity=guide_id, payload={"enabled": enabled}))
        await db.commit()
    return True
