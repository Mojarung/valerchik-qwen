"""Досев демо-диалогов на стенд, который наполняли импортом.

Сидер наливает демо-данные только в пустую базу — иначе он затирал бы работу
администратора. Из-за этого стенд «дрейфует»: статьи приезжают импортом
методичек, условие «база пустая» перестаёт выполняться, и диалогов с отзывами
не появляется никогда. Снаружи это выглядит как неработающий раздел оценки
специалистов — а он закрывает целый пункт задания.

Файл отдельный намеренно: фикстура приложения модульная, и здешние тесты
выносят из базы все диалоги.
"""

from collections import Counter

from sqlalchemy import delete, func, select

from app.models import ChatSession, Feedback, KBArticle, KBGap, Message, new_id
from app.seed import data
from app.seed.seeder import _active_articles, _seed_dialogs, seed_if_empty


async def _counts(rt) -> tuple[int, int, int]:
    async with rt.db.session_factory() as db:
        articles = (await db.execute(select(func.count(KBArticle.id)))).scalar() or 0
        sessions = (await db.execute(select(func.count(ChatSession.id)))).scalar() or 0
        feedback = (await db.execute(select(func.count(Feedback.id)))).scalar() or 0
    return articles, sessions, feedback


async def _drop_all_dialogs(rt) -> None:
    async with rt.db.session_factory() as db:
        await db.execute(delete(Feedback))
        await db.execute(delete(Message))
        await db.execute(delete(ChatSession))
        await db.commit()


async def _one_manual_dialog(rt) -> None:
    """Диалог без отзыва — след ручной проверки стенда."""
    async with rt.db.session_factory() as db:
        session = ChatSession(id=new_id(), status="resolved", channel="web")
        db.add(session)
        db.add(Message(session_id=session.id, role="user", content="проверка связи"))
        await db.commit()


async def test_dialogs_are_seeded_onto_a_stand_that_already_has_articles(client):
    """Ровно состояние текущего стенда: статьи есть, диалоги от ручных проверок
    есть, отзывов нет ни одного — и раздел оценки специалистов пуст."""
    rt = client.app.state.rt
    await _drop_all_dialogs(rt)
    await _one_manual_dialog(rt)
    articles, sessions, feedback = await _counts(rt)
    assert articles > 0 and sessions == 1 and feedback == 0, "исходное состояние теста"

    await seed_if_empty(rt)

    articles_after, sessions_after, feedback_after = await _counts(rt)
    assert sessions_after > sessions, "диалоги должны досеяться поверх существующей базы"
    assert feedback_after > 0, "без отзывов раздел оценки специалистов остаётся пустым"
    assert articles_after == articles, "статьи не трогаем: их наливали импортом"


async def test_second_run_does_not_duplicate_anything(client):
    """Признак «ни одного отзыва» гаснет после первого досева, и ветка молчит."""
    rt = client.app.state.rt
    before = await _counts(rt)

    await seed_if_empty(rt)

    assert await _counts(rt) == before


async def test_dialog_scenarios_keep_their_shape(client):
    """Демо-диалоги — опора дашборда и оценки специалистов. У каждого сценария
    свой состав реплик, у «слабого» оператора — только низкие оценки, у
    остальных — высокие: на этом контрасте и держится раздел оценки."""
    rt = client.app.state.rt
    await _drop_all_dialogs(rt)
    async with rt.db.session_factory() as db:
        await db.execute(delete(KBGap))
        await db.commit()

    await _seed_dialogs(rt, await _active_articles(rt))

    async with rt.db.session_factory() as db:
        sessions = (await db.execute(select(ChatSession))).scalars().all()
        messages = (await db.execute(select(Message).order_by(Message.created_at))).scalars().all()
        feedback = (await db.execute(select(Feedback))).scalars().all()
        gaps = (await db.execute(select(KBGap))).scalars().all()
    roles: dict[str, list[str]] = {}
    for message in messages:
        roles.setdefault(message.session_id, []).append(message.role)

    assert Counter(s.status for s in sessions) == {
        "resolved": 78 + 20 + 3 * len(data.OFF_KB_QUESTIONS),
        "closed_violation": 2 * len(data.VIOLATION_MESSAGES),
    }
    escalated = [s for s in sessions if s.operator_id]
    assert len(escalated) == 20
    assert all(roles[s.id] == ["user", "assistant", "system", "operator"] for s in escalated)
    assert all(roles[s.id] == ["user", "system"] for s in sessions if s.status == "closed_violation")

    weak = "op_sergey".ljust(32, "0")
    weak_ratings = [f.rating for f in feedback if f.operator_id == weak]
    other_ratings = [f.rating for f in feedback if f.operator_id not in (None, weak)]
    assert weak_ratings and max(weak_ratings) <= 3
    assert other_ratings and min(other_ratings) >= 4
    assert sum(f.specialist_kind == "operator" for f in feedback) == len(escalated)
    assert any(f.specialist_kind == "ai" and f.operator_id is None for f in feedback)
    assert sorted(g.query_example for g in gaps) == sorted(data.OFF_KB_QUESTIONS)
