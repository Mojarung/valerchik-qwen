"""Случаи для оператора: предложить специалиста вместо чужой отписки.

Выгрузка поддержки полна обращений, где ответом было действие специалиста
(«на момент проверки учётная запись разблокирована»). Такой текст нельзя отдать
новому человеку, но и молча отказать нельзя: бот предлагает оператора на ту
линию, где похожее решали раньше, а переводит — только после согласия.
"""

import time

import pytest
from sqlalchemy import select

from app.chat import steps
from app.chat.turn import Turn
from app.models import ChatSession, KBArticle
from app.services import kbkind
from app.services.retrieval import RetrievedDoc
from app.services.routing import RoutePrediction

CASE_QUESTION = "Учетную запись заблокировали после смены руководителя организации"
CASE_ANSWER = "На момент проверки учетная запись разблокирована, уведомление направлено на почту."
CASE_TOPIC = "Блокировка учетной записи"
CASE_LINE = 2
OFF_TOPIC = "Какая завтра погода в Москве?"


async def _case_article(client) -> str:
    """Случай для оператора в базе — один на модуль, как его кладёт импорт выгрузки."""
    rt = client.app.state.rt
    async with rt.db.session_factory() as db:
        found = (
            await db.execute(select(KBArticle.id).where(KBArticle.question == CASE_QUESTION))
        ).scalar_one_or_none()
        if found:
            return found
        article = KBArticle(
            question=CASE_QUESTION,
            answer=CASE_ANSWER,
            category=CASE_TOPIC,
            keywords=[],
            line=CASE_LINE,
            audience="all",
            status="active",
            source="import",
            origin={
                "file": "stp.xlsx",
                "locator": "строка 7",
                "source": "support_export",
                "kind": "operator_case",
            },
        )
        db.add(article)
        await db.commit()
        await db.refresh(article)
    await rt.kb.index_article(article.id)
    return article.id


async def _session(client) -> str:
    """Новый диалог через сервис, а не через API: модуль заводит их десятками, и
    ограничение частоты на ручке создания диалога к проверкам здесь отношения не имеет."""
    return (await client.app.state.rt.chat.create_session())["session_id"]


def _last_reply_meta(client, sid: str) -> dict:
    replies = [m for m in client.get(f"/api/chat/sessions/{sid}/messages").json() if m["role"] == "assistant"]
    return replies[-1]["meta"]


async def _session_row(client, sid: str) -> ChatSession:
    async with client.app.state.rt.db.session_factory() as db:
        return await db.get(ChatSession, sid)


async def test_new_question_after_offer_goes_the_usual_way(client):
    await _case_article(client)
    rt = client.app.state.rt
    sid = await _session(client)
    await rt.chat.handle_user_message(sid, CASE_QUESTION)

    result = await rt.chat.handle_user_message(sid, "Как зарегистрироваться на портале?")

    assert result["escalated"] is False
    # Обычный путь: ответ или вопрос о роли — регистрация у сторон сделки разная
    assert result["status"] in ("answered", "clarify")
    assert _last_reply_meta(client, sid).get("kind") != "operator_offer"
    assert (await _session_row(client, sid)).status == "active"


async def test_decline_is_acknowledged_not_searched(client):
    await _case_article(client)
    rt = client.app.state.rt
    sid = await _session(client)
    await rt.chat.handle_user_message(sid, CASE_QUESTION)

    result = await rt.chat.handle_user_message(sid, "нет, не надо")

    assert result["escalated"] is False
    assert result["answer"] == rt.dyn.get("routing.case_decline_message")
    assert (await _session_row(client, sid)).status == "active"


async def test_kill_switch(client, monkeypatch):
    await _case_article(client)
    rt = client.app.state.rt
    monkeypatch.setitem(rt.dyn._values, "routing.case_offer_enabled", False)
    sid = await _session(client)

    result = await rt.chat.handle_user_message(sid, CASE_QUESTION)

    assert _last_reply_meta(client, sid).get("kind") != "operator_offer"
    assert CASE_ANSWER not in result["answer"], "случай для оператора ответом не бывает и без предложения"


async def test_request_for_operator_without_offer_escalates_as_before(client):
    await _case_article(client)
    rt = client.app.state.rt
    sid = await _session(client)

    result = await rt.chat.handle_user_message(sid, "Позовите живого оператора, у меня не работает ЭЦП")

    assert result["escalated"] is True
    assert (await _session_row(client, sid)).status == "waiting_operator"


async def test_refusal_offers_operator_and_yes_escalates(client, monkeypatch):
    """Отказ базы знаний тоже предлагает оператора — и «да» на него работает так же.

    Линию тут никто не назвал, поэтому её решает классификатор — по исходному
    вопросу, а не по слову «да».
    """
    rt = client.app.state.rt
    sid = await _session(client)
    result = await rt.chat.handle_user_message(sid, OFF_TOPIC)
    assert result["status"] == "no_answer"
    meta = _last_reply_meta(client, sid)
    assert meta["offer_operator"] is True, "отказ обязан предлагать оператора"
    assert meta["question"] == OFF_TOPIC

    asked: list[str] = []

    async def classifier(text, **kwargs):
        asked.append(text)
        return RoutePrediction(line=1, proba=0.9, source="model")

    monkeypatch.setattr(rt.classifier, "predict", classifier)
    result = await rt.chat.handle_user_message(sid, "да")

    assert result["escalated"] is True
    assert asked and all(question == OFF_TOPIC for question in asked)
    session = await _session_row(client, sid)
    assert (session.line, session.escalation_reason) == (1, OFF_TOPIC)


async def test_thanks_after_an_answer_is_not_consent(client):
    """После ответа по существу «хорошо, спасибо» — прощание, а не согласие.

    Агент может приложить предложение оператора и к ответу, когда опора в базе
    неполная. «Хорошо» на такой ответ значит «понял», и ставить человека в
    очередь из-за вежливости нельзя: диалог завершается. Явное «да, передайте» —
    переводит.
    """
    rt = client.app.state.rt
    question = "Где посмотреть мои контракты?"
    offer = {"answered": True, "escalated": False, "offer_operator": True, "question": question}
    text = "Откройте раздел «Мои контракты». Если не поможет — передам обращение оператору."

    sid = await _session(client)
    await rt.chat._save_message(sid, "assistant", text, meta=offer)
    result = await rt.chat.handle_user_message(sid, "хорошо, спасибо")
    assert result["escalated"] is False
    session = await _session_row(client, sid)
    assert (session.status, session.escalated_at) == ("resolved", None)

    sid = await _session(client)
    await rt.chat._save_message(sid, "assistant", text, meta=offer)
    result = await rt.chat.handle_user_message(sid, "да, передайте")
    assert result["escalated"] is True
    assert (await _session_row(client, sid)).escalation_reason == question


@pytest.mark.parametrize("refusal", ["нет, оператор не нужен", "Не надо оператора", "оператора не зовите"])
async def test_refusal_with_the_word_operator_is_a_refusal(client, refusal):
    """«Оператор не нужен» — отказ, хотя слово «оператор» в нём есть.

    Раньше слово перевешивало: человек прямо отказывался, а его ставили в очередь.
    """
    await _case_article(client)
    rt = client.app.state.rt
    sid = await _session(client)
    await rt.chat.handle_user_message(sid, CASE_QUESTION)

    result = await rt.chat.handle_user_message(sid, refusal)

    assert result["escalated"] is False
    assert result["answer"] == rt.dyn.get("routing.case_decline_message")
    assert (await _session_row(client, sid)).status == "active"


@pytest.mark.parametrize("thanks", ["хорошо, спасибо", "ок, спасибо", "ладно, спасибо", "спасибо"])
async def test_thanks_after_a_refusal_is_not_consent(client, thanks):
    """«Хорошо, спасибо» на отказ базы знаний — прощание, а не «переведите меня».

    Отказ предлагает оператора всегда, и вежливость в ответ на него ставила
    человека в очередь. Теперь диалог завершается прощанием, а не новым отказом
    по слову «спасибо».
    """
    rt = client.app.state.rt
    sid = await _session(client)
    await rt.chat.handle_user_message(sid, OFF_TOPIC)

    result = await rt.chat.handle_user_message(sid, thanks)

    assert result["escalated"] is False
    assert result["answer"] == rt.dyn.get("chat.thanks_message")
    session = await _session_row(client, sid)
    assert (session.status, session.escalated_at) == ("resolved", None)


NEW_DETAILS = "Позовите оператора, у меня не работает ЭЦП при подписании контракта"


def _classifier_by_details(asked: list[str]):
    """Классификатор, который по ЭЦП выбирает вторую линию, а иначе первую."""

    async def classifier(text, **kwargs):
        asked.append(text)
        return RoutePrediction(line=2 if "ЭЦП" in text else 1, proba=0.9, source="model")

    return classifier


async def test_request_with_new_details_after_a_refusal_keeps_them(client, monkeypatch):
    """Просьба об операторе с новым описанием проблемы не теряет это описание.

    Раньше причиной эскалации ставился прошлый вопрос («погода»), и по нему же
    выбиралась линия: оператор не узнавал про ЭЦП, а человек попадал не туда.
    """
    rt = client.app.state.rt
    sid = await _session(client)
    await rt.chat.handle_user_message(sid, OFF_TOPIC)
    asked: list[str] = []
    monkeypatch.setattr(rt.classifier, "predict", _classifier_by_details(asked))

    result = await rt.chat.handle_user_message(sid, NEW_DETAILS)

    assert result["escalated"] is True
    session = await _session_row(client, sid)
    assert session.line == 2, "линию решают новые подробности"
    assert NEW_DETAILS in session.escalation_reason
    assert OFF_TOPIC in session.escalation_reason, "прошлый вопрос тоже остаётся в причине"
    assert asked == [session.escalation_reason], "линия выбрана по тому же тексту, что в причине"


async def test_details_after_a_case_offer_outweigh_the_offered_line(client, monkeypatch):
    """Предложена линия 2 по похожему обращению, а человек описал другое — решает описание."""
    await _case_article(client)
    rt = client.app.state.rt
    sid = await _session(client)
    await rt.chat.handle_user_message(sid, CASE_QUESTION)
    asked: list[str] = []

    async def classifier(text, **kwargs):
        asked.append(text)
        return RoutePrediction(line=1, proba=0.9, source="model")

    monkeypatch.setattr(rt.classifier, "predict", classifier)
    result = await rt.chat.handle_user_message(sid, "Соедините с оператором, хочу уточнить про оплату")

    assert result["escalated"] is True
    session = await _session_row(client, sid)
    assert session.line == 1
    assert "про оплату" in session.escalation_reason
    assert CASE_QUESTION in session.escalation_reason
    assert len(asked) == 1


_REFUSAL_OFFER = {"offer_operator": True, "answered": False}
_CASE_OFFER = {"offer_operator": True, "answered": False, "kind": "operator_offer", "line": 2}
_ANSWER_OFFER = {"offer_operator": True, "answered": True}


@pytest.mark.parametrize(
    ("text", "offer", "verdict", "details"),
    [
        ("да", _REFUSAL_OFFER, steps.ACCEPT, False),
        ("Да, передайте оператору", _CASE_OFFER, steps.ACCEPT, False),
        ("Позовите оператора", _REFUSAL_OFFER, steps.ACCEPT, False),
        ("нет, позовите оператора", _REFUSAL_OFFER, steps.ACCEPT, False),
        ("Нет, мне нужен живой оператор", _REFUSAL_OFFER, steps.ACCEPT, False),
        ("ок", _CASE_OFFER, steps.ACCEPT, False),
        ("хорошо", _REFUSAL_OFFER, steps.ACCEPT, False),
        ("да, спасибо", _REFUSAL_OFFER, steps.ACCEPT, False),
        (NEW_DETAILS, _REFUSAL_OFFER, steps.ACCEPT, True),
        ("нет", _CASE_OFFER, steps.DECLINE, False),
        ("нет, спасибо", _REFUSAL_OFFER, steps.DECLINE, False),
        ("да не нужно, спасибо", _CASE_OFFER, steps.DECLINE, False),
        ("не переводите", _CASE_OFFER, steps.DECLINE, False),
        ("без оператора", _REFUSAL_OFFER, steps.DECLINE, False),
        ("хорошо, спасибо", _REFUSAL_OFFER, steps.THANKS, False),
        ("ок, спасибо большое", _CASE_OFFER, steps.THANKS, False),
    ],
)
def test_reading_a_reply_to_the_offer(text, offer, verdict, details):
    assert steps._read_offer_reply(text, offer) == steps.OfferReply(verdict, details)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Позовите оператора, КриптоПро не видит сертификат", True),
        ("Мне нужен оператор", True),
        ("оператор", True),
        ("оператор не нужен", False),
        ("не надо оператора", False),
        ("не переводите на оператора", False),
        ("да", False),
        ("как добавить сотрудника в кабинет", False),
    ],
)
def test_direct_operator_request_respects_negation(text, expected):
    assert steps.direct_operator_request(text) is expected


@pytest.mark.parametrize(
    ("text", "offer"),
    [
        ("хорошо, спасибо", _ANSWER_OFFER),  # после ответа по существу — благодарность
        ("да, спасибо", _ANSWER_OFFER),  # «да, помогло», а не «переведите»
        ("хорошо", _ANSWER_OFFER),
        ("пожалуйста", _REFUSAL_OFFER),  # ни согласия, ни отказа — обычная реплика
        ("Как передать УПД заказчику?", _REFUSAL_OFFER),  # «передать» в вопросе — не просьба
        ("Переведите страницу портала на английский язык", _REFUSAL_OFFER),
        ("оператор не нужен, как подать заявку на участие?", _REFUSAL_OFFER),  # новый вопрос
    ],
)
def test_replies_that_are_not_an_answer_to_the_offer(text, offer):
    assert steps._read_offer_reply(text, offer) is None


async def test_yes_without_offer_is_an_ordinary_message(client):
    """«Да» само по себе, без предложения, никого никуда не переводит."""
    rt = client.app.state.rt
    sid = await _session(client)

    result = await rt.chat.handle_user_message(sid, "да")

    assert result["escalated"] is False
    assert (await _session_row(client, sid)).status == "active"


def _doc(article_id: str, score: float, kind: str = kbkind.ANSWER) -> RetrievedDoc:
    return RetrievedDoc(
        article_id=article_id,
        question=f"Вопрос {article_id}",
        answer=f"Ответ {article_id}",
        category="Тема",
        line=2,
        score=score,
        kind=kind,
    )


@pytest.mark.parametrize(
    ("answer", "case", "offered"),
    [
        (0.30, 0.70, True),  # инструкции нет, похожее обращение уверенное
        (0.80, 0.90, True),  # инструкция есть, но обращение заметно ближе
        (0.80, 0.83, False),  # почти вровень — отвечаем инструкцией
        (0.10, 0.55, False),  # обращение ниже своего порога — совпадение случайное
    ],
)
async def test_offer_needs_a_strong_case_ahead_of_the_best_answer(client, monkeypatch, answer, case, offered):
    rt = client.app.state.rt

    async def cases(text, top_k=3, audience=""):
        return [_doc("case", case, kind=kbkind.OPERATOR_CASE)]

    monkeypatch.setattr(rt.retriever, "search_cases", cases)
    turn = Turn(
        await _session(client), "вопрос о моей заявке", time.perf_counter(), probe=[_doc("answer", answer)]
    )

    assert (await steps._maybe_offer_operator(rt.chat, turn) is not None) is offered
