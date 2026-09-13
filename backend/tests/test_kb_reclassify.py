"""Бэкофилл вида записи: подписи и оглавления, уже лежащие в базе, — не ответы."""

from sqlalchemy import select

from app.db import Database
from app.models import KBArticle, new_id
from app.services import kbkind, phrasings
from app.tools.kb_reclassify import reclassify

ARTICLES = [
    ("Рисунок 82 - Экспорт оферт", "active", None),
    ("СОДЕРЖАНИЕ", "active", None),
    ("Рисунок 7 - Карточка контракта", "archived", None),
    ("Как выгрузить УПД?", "active", None),
    # Справочник тем уже помечен — вид не переписываем, считать его нечего
    ("Рисунок 1 - Схема", "active", {"kind": kbkind.REFERENCE}),
]


async def _fill(url: str) -> None:
    db = Database(url)
    try:
        await db.create_all()
        async with db.session_factory() as session:
            for question, status, origin in ARTICLES:
                session.add(
                    KBArticle(
                        id=new_id(),
                        question=question,
                        answer="Текст статьи.",
                        category="Методичка",
                        status=status,
                        origin=origin,
                    )
                )
            await session.commit()
    finally:
        await db.dispose()


async def _kinds(url: str) -> dict[str, str]:
    db = Database(url)
    try:
        async with db.session_factory() as session:
            rows = (await session.scalars(select(KBArticle))).all()
    finally:
        await db.dispose()
    return {row.question: kbkind.of(row.origin) for row in rows}


async def _phrasings(url: str, question: str) -> list[str]:
    db = Database(url)
    try:
        async with db.session_factory() as session:
            rows = (await session.scalars(select(KBArticle))).all()
    finally:
        await db.dispose()
    row = next(r for r in rows if r.question == question)
    return phrasings.of(row.origin)


async def test_dry_run_counts_but_changes_nothing(tmp_path):
    url = f"sqlite+aiosqlite:///{tmp_path / 'db.sqlite3'}"
    await _fill(url)

    report = await reclassify(url, apply=False)

    assert report["junk"] == 3  # два активных и один архивный, помеченный не в счёт
    assert report["junk_by_status"] == {"active": 2, "archived": 1}
    assert all(kind == kbkind.ANSWER for kind in (await _kinds(url)).values() if kind != kbkind.REFERENCE)


async def test_apply_marks_captions_and_contents_as_reference(tmp_path):
    url = f"sqlite+aiosqlite:///{tmp_path / 'db.sqlite3'}"
    await _fill(url)

    await reclassify(url, apply=True)

    kinds = await _kinds(url)
    assert kinds["Рисунок 82 - Экспорт оферт"] == kbkind.REFERENCE
    assert kinds["СОДЕРЖАНИЕ"] == kbkind.REFERENCE
    assert kinds["Рисунок 7 - Карточка контракта"] == kbkind.REFERENCE
    # Статья с настоящим вопросом остаётся ответом
    assert kinds["Как выгрузить УПД?"] == kbkind.ANSWER
    # Повторный прогон ничего не находит: правило то же самое
    assert (await reclassify(url, apply=False))["junk"] == 0


async def test_existing_glossary_becomes_articles_per_term(tmp_path):
    """Глоссарий уже в базе одной статьёй: определения лежат в теле, и «что такое
    УПД» их не находит. Разбор заводит статью на термин, а таблицу оставляет
    справочником — повторный прогон терминов не удваивает."""
    url = f"sqlite+aiosqlite:///{tmp_path / 'db.sqlite3'}"
    table = (
        "| Сокращение | Расшифровка |\n| --- | --- |\n"
        "| УПД | Универсальный передаточный документ |\n"
        "| МЧД | Машиночитаемая доверенность |\n"
    )
    db = Database(url)
    try:
        await db.create_all()
        async with db.session_factory() as session:
            session.add(
                KBArticle(
                    id=new_id(),
                    question="Таблица 1 - Перечень терминов и сокращений",
                    answer=table,
                    category="Методичка",
                    status="active",
                )
            )
            await session.commit()
    finally:
        await db.dispose()

    report = await reclassify(url, apply=True)

    assert report["glossaries"] == 1
    assert report["term_questions"] == ["Что означает УПД?", "Что означает МЧД?"]
    assert await _phrasings(url, "Что означает УПД?") == ["УПД", "что такое УПД", "расшифровка УПД"]
    kinds = await _kinds(url)
    assert kinds["Что означает УПД?"] == kbkind.ANSWER
    assert kinds["Таблица 1 - Перечень терминов и сокращений"] == kbkind.REFERENCE
    repeat = await reclassify(url, apply=False)
    assert repeat["terms"] == 0 and repeat["enriched"] == 0


async def test_terms_from_an_earlier_run_get_their_phrasings(tmp_path):
    """Прошлый прогон завёл определения без формулировок, и агентский запрос «упд»
    их не находил. Повторный прогон дописывает формулировки, а не плодит копии."""
    url = f"sqlite+aiosqlite:///{tmp_path / 'db.sqlite3'}"
    db = Database(url)
    try:
        await db.create_all()
        async with db.session_factory() as session:
            session.add(
                KBArticle(
                    id=new_id(),
                    question="Таблица 1 - Перечень терминов и сокращений",
                    answer="| УПД | Универсальный передаточный документ |\n",
                    category="Методичка",
                )
            )
            session.add(
                KBArticle(
                    id=new_id(),
                    question="Что означает УПД?",
                    answer="УПД — Универсальный передаточный документ.",
                    category="Методичка",
                    source="manual",
                    origin={"source": "kb_reclassify"},
                )
            )
            await session.commit()
    finally:
        await db.dispose()

    report = await reclassify(url, apply=True)

    assert report["terms"] == 0 and report["enriched"] == 1
    assert await _phrasings(url, "Что означает УПД?") == ["УПД", "что такое УПД", "расшифровка УПД"]
