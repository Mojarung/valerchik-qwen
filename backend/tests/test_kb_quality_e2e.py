"""Стыки качества ответов целиком, на lite-стенде: от реплики человека до meta ответа.

Каждая область (поиск, агент, шаги диалога, импорт) проверена своими тестами.
Здесь — то, что видел жюри на стенде и что ломается ровно на стыках: отказ без
предложения оператора, молчаливый перевод, опечатка мимо статьи, чужая отписка
в ответе.
"""

from sqlalchemy import select

from app.models import ChatSession, KBArticle
from app.services import kbkind

CASE_QUESTION = "Не отображается подписанный документ о приемке в карточке исполнения контракта"
CASE_ANSWER = "На момент проверки документ о приемке отображается, заказчику направлено уведомление."


async def _session(client) -> str:
    return (await client.app.state.rt.chat.create_session())["session_id"]


def _last_assistant(client, sid: str) -> dict:
    replies = [m for m in client.get(f"/api/chat/sessions/{sid}/messages").json() if m["role"] == "assistant"]
    return replies[-1]


async def test_no_answer_notifies_and_offers_operator(client):
    """«Нет ответа» везде одинаково: уведомить и предложить оператора, не переводя."""
    rt = client.app.state.rt
    for question in ("Какая завтра погода в Москве?", "Как получить налоговый вычет за лечение?"):
        sid = await _session(client)

        result = await rt.chat.handle_user_message(sid, question)

        assert result["status"] == "no_answer"
        assert result["escalated"] is False, "оператора не просили — молча не переводим"
        assert "оператор" in result["answer"].lower()
        meta = _last_assistant(client, sid)["meta"]
        assert meta["offer_operator"] is True, "без флага под отказом нет кнопки «Позвать оператора»"
        async with rt.db.session_factory() as db:
            assert (await db.get(ChatSession, sid)).status == "active"


async def test_operator_request_and_button_text_escalate(client, monkeypatch):
    """Просьба человека и текст кнопки переводят без решения со стороны LLM."""
    rt = client.app.state.rt

    async def must_not_run(*args, **kwargs):
        raise AssertionError("явная просьба об операторе не должна доходить до LLM")

    monkeypatch.setattr("app.services.chat.run_support_agent", must_not_run)
    sid = await _session(client)
    request = "Позовите оператора, КриптоПро не видит сертификат"
    result = await rt.chat.handle_user_message(sid, request)
    assert result["escalated"] is True
    async with rt.db.session_factory() as db:
        session = await db.get(ChatSession, sid)
        assert session.escalation_reason == request

    # Кнопка под отказом шлёт «Позовите оператора» — тот же путь
    sid = await _session(client)
    # Сохраняем предложение напрямую: LLM замокан намеренно, проверяем именно
    # следующий текст кнопки и общий детерминированный путь передачи.
    await rt.chat._save_message(
        sid,
        "assistant",
        "Могу позвать оператора.",
        meta={"offer_operator": True, "answered": False, "question": "Исходный вопрос"},
    )
    assert (await rt.chat.handle_user_message(sid, "Позовите оператора"))["escalated"] is True
    async with rt.db.session_factory() as db:
        assert (await db.get(ChatSession, sid)).status == "waiting_operator"


async def test_question_with_typos_finds_the_article(client):
    rt = client.app.state.rt
    sid = await _session(client)

    result = await rt.chat.handle_user_message(
        sid, "Как зарегестрироватся на партале поставщеков?", audience="supplier"
    )

    assert result["status"] == "answered"
    assert any("регистр" in c["question"].lower() for c in result["citations"])


async def test_operator_case_is_never_the_answer(client):
    """Отписка по чужой заявке — не ответ новому человеку: поиск ответов её не
    видит, а на совпавший вопрос бот предлагает специалиста."""
    rt = client.app.state.rt
    async with rt.db.session_factory() as db:
        article = (
            await db.execute(select(KBArticle).where(KBArticle.question == CASE_QUESTION))
        ).scalar_one_or_none()
        if article is None:
            article = KBArticle(
                question=CASE_QUESTION,
                answer=CASE_ANSWER,
                category="Исполнение контракта",
                keywords=[],
                line=2,
                audience="all",
                status="active",
                source="import",
                origin={"file": "stp.xlsx", "source": "support_export", "kind": kbkind.OPERATOR_CASE},
            )
            db.add(article)
            await db.commit()
            await db.refresh(article)
    await rt.kb.index_article(article.id)

    found = await rt.retriever.search(CASE_QUESTION)
    assert article.id not in {doc.article_id for doc in found}
    assert {doc.kind for doc in found} <= {kbkind.ANSWER}

    sid = await _session(client)
    result = await rt.chat.handle_user_message(sid, CASE_QUESTION)

    assert CASE_ANSWER not in result["answer"]
    assert result["escalated"] is False
    assert _last_assistant(client, sid)["meta"]["offer_operator"] is True
