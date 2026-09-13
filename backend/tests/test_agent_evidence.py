"""Чем подкреплён ответ агента: порог, источники и повторный поиск.

Оба сценария ниже проходят мимо порога уверенности разными путями и оба
заканчиваются тем, что человек получает неверный ответ, помеченный как верный.
Скриптованная модель офлайн-режима так себя не ведёт — она всегда ищет ровно
один раз, — поэтому здесь модель подменяется своей.
"""

import asyncio
import re
from types import SimpleNamespace

import pytest
from pydantic_ai import ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import FunctionModel

from app.services import agent as agent_module
from app.services.agent import NO_ANSWER, AgentRequest, run_support_agent
from app.services.retrieval import RetrievedDoc


class _Retriever:
    """Отдаёт заранее заданную выдачу — по одной на каждый вызов поиска."""

    def __init__(self, *rounds: list[RetrievedDoc]) -> None:
        self.rounds = list(rounds)
        self.queries: list[str] = []

    async def search(self, query: str, context: str = "", audience: str = "") -> list[RetrievedDoc]:
        self.queries.append(query)
        return self.rounds.pop(0) if self.rounds else []


class _Classifier:
    async def predict(self, text: str):
        raise AssertionError("эскалации здесь не ожидается")


def _doc(score: float) -> RetrievedDoc:
    return RetrievedDoc(
        article_id="a1",
        question="Как отозвать оферту?",
        answer="Оферту можно отозвать в личном кабинете.",
        category="Оферты",
        line=1,
        score=score,
    )


def _model(*responses: ModelResponse) -> FunctionModel:
    steps = iter(responses)

    def reply(messages: list, info) -> ModelResponse:
        return next(steps)

    return FunctionModel(reply)


async def test_answer_without_a_single_tool_call_is_not_an_answer(client, monkeypatch):
    """Промпт требует обязательного search_kb — ответ мимо него ничем не подтверждён.

    Порог к такому ответу не применялся: применять его было не к чему, поиска не
    было. Раньше это означало, что придуманный моделью текст уходил человеку как
    обычный ответ, и отличить его от найденного в базе знаний было невозможно.
    """
    monkeypatch.setattr(
        agent_module,
        "build_model",
        lambda dyn: _model(ModelResponse(parts=[TextPart("Регистрация занимает один рабочий день.")])),
    )
    retriever = _Retriever()

    outcome = await run_support_agent(
        client.app.state.rt.dyn, retriever, _Classifier(), AgentRequest("сколько идёт регистрация")
    )

    assert outcome.answered is False
    assert outcome.citations == []
    assert retriever.queries == [], "модель в базу знаний не ходила"


async def test_second_search_does_not_erase_a_good_first_one(client, monkeypatch):
    """Агент часто уточняет формулировку и ищет повторно.

    Пустая вторая попытка затирала удачную первую — и ответ, который уже был
    найден и показан бы с источниками, отбраковывался порогом как ненайденный.
    Держим лучшее из попыток.
    """
    rt = client.app.state.rt
    threshold = float(rt.dyn.get("agent.confidence_threshold"))
    good = round(threshold + 0.3, 3)
    monkeypatch.setattr(
        agent_module,
        "build_model",
        lambda dyn: _model(
            ModelResponse(parts=[ToolCallPart("search_kb", {"query": "как отозвать оферту"})]),
            ModelResponse(parts=[ToolCallPart("search_kb", {"query": "оферта сроки отзыва"})]),
            ModelResponse(parts=[TextPart("Оферту можно отозвать в личном кабинете.")]),
        ),
    )
    retriever = _Retriever([_doc(good)], [])  # вторая попытка пустая

    outcome = await run_support_agent(rt.dyn, retriever, _Classifier(), AgentRequest("как отозвать оферту"))

    assert len(retriever.queries) == 2, "сценарий теста — именно два поиска"
    assert outcome.answered is True
    assert outcome.confidence == pytest.approx(good)
    assert [c["article_id"] for c in outcome.citations] == ["a1"], (
        "источники удачной попытки обязаны пережить неудачную"
    )


async def test_better_second_search_replaces_the_first(client, monkeypatch):
    """Обратный случай: вторая попытка лучше — показываем её, а не первую."""
    rt = client.app.state.rt
    monkeypatch.setattr(
        agent_module,
        "build_model",
        lambda dyn: _model(
            ModelResponse(parts=[ToolCallPart("search_kb", {"query": "оферта"})]),
            ModelResponse(parts=[ToolCallPart("search_kb", {"query": "как отозвать оферту"})]),
            ModelResponse(parts=[TextPart("Оферту можно отозвать в личном кабинете.")]),
        ),
    )
    weak, strong = _doc(0.45), _doc(0.92)
    strong.article_id = "a2"

    outcome = await run_support_agent(
        rt.dyn, _Retriever([weak], [strong]), _Classifier(), AgentRequest("как отозвать оферту")
    )

    assert outcome.confidence == pytest.approx(0.92)
    assert [c["article_id"] for c in outcome.citations] == ["a2"]


@pytest.mark.parametrize(
    "text, expected",
    [
        ("позовите оператора", True),
        ("хочу поговорить с живым человеком", True),
        ("соедините меня со специалистом", True),
        ("переключите на менеджера", True),
        ("можно сотрудника позвать?", True),
        # обычные вопросы к базе знаний, а не просьба о человеке
        ("как добавить сотрудника в личный кабинет организации", False),
        ("какой специалист рассматривает заявку на регистрацию", False),
        ("нужен ли сотруднику снилс для приглашения", False),
    ],
)
def test_wants_operator_needs_a_request_not_just_a_word(text, expected):
    """«Сотрудник» в вопросе — это ещё не просьба позвать человека: демо-модель
    уводила «как добавить сотрудника» к оператору вместо поиска по базе."""
    assert agent_module.wants_operator(text) is expected


def _refusal(client) -> str:
    return str(client.app.state.rt.dyn.get("agent.no_answer_message"))


async def test_model_failure_becomes_honest_refusal(client, monkeypatch):
    """Упавшая модель — честный отказ, а не пятисотка и не пустой пузырь."""

    def broken(messages: list, info) -> ModelResponse:
        raise RuntimeError("модель недоступна")

    monkeypatch.setattr(agent_module, "build_model", lambda dyn: FunctionModel(broken))
    outcome = await run_support_agent(
        client.app.state.rt.dyn, _Retriever(), _Classifier(), AgentRequest("как отозвать оферту")
    )
    assert outcome.answered is False
    assert outcome.answer == _refusal(client)


async def test_slow_model_is_cut_by_timeout(client, monkeypatch):
    """Модель, не уложившаяся в таймаут, не держит человека вечно — отказ."""

    async def slow(messages: list, info) -> ModelResponse:
        await asyncio.sleep(5)
        return ModelResponse(parts=[TextPart("поздно")])

    monkeypatch.setattr(agent_module, "build_model", lambda dyn: FunctionModel(slow))
    outcome = await run_support_agent(
        client.app.state.rt.dyn,
        _Retriever(),
        _Classifier(),
        AgentRequest("как отозвать оферту", timeout_s=0.05),
    )
    assert outcome.answered is False
    assert outcome.answer == _refusal(client)


async def test_tool_arguments_printed_as_text_are_not_an_answer(client, monkeypatch):
    """Аргументы инструмента текстом — сорвавшийся вызов, показывать его нельзя."""
    monkeypatch.setattr(
        agent_module,
        "build_model",
        lambda dyn: _model(
            ModelResponse(parts=[ToolCallPart("search_kb", {"query": "отозвать оферту"})]),
            ModelResponse(parts=[TextPart('{"query": "отозвать оферту"}')]),
        ),
    )
    outcome = await run_support_agent(
        client.app.state.rt.dyn, _Retriever([_doc(0.92)]), _Classifier(), AgentRequest("как отозвать оферту")
    )
    assert outcome.answered is False
    assert outcome.citations == []


# --- сверка фактов и служебное в тексте (answer_guard) ---


def _searched_then(text: str) -> FunctionModel:
    return _model(
        ModelResponse(parts=[ToolCallPart("search_kb", {"query": "как отозвать оферту"})]),
        ModelResponse(parts=[TextPart(text)]),
    )


async def test_invented_number_falls_back_to_article_text(client, monkeypatch):
    """Модель дописала срок, которого в статье нет, — человек получает текст статьи.

    Ровно так на стенде выглядел ответ про блокировку кабинета: в базе «обратитесь
    в СКК», у модели — «подождите, блокировка снимется сама через срок». Отказ тут
    был бы перегибом: уверенная статья в выдаче есть, и её текст верен.
    """
    monkeypatch.setattr(
        agent_module,
        "build_model",
        lambda dyn: _searched_then("Оферту можно отозвать в личном кабинете в течение 10 рабочих дней."),
    )
    dyn = client.app.state.rt.dyn
    outcome = await run_support_agent(
        dyn, _Retriever([_doc(0.92)]), _Classifier(), AgentRequest("как отозвать оферту")
    )

    assert outcome.answered is True
    assert (
        outcome.answer == f"{dyn.get('answer.extracted_prefix')}\n\nОферту можно отозвать в личном кабинете."
    )
    assert outcome.grounding == {
        "unconfirmed": ["10 рабочих дней"],
        "fallback": "article",
        "article_id": "a1",
    }
    assert [c["article_id"] for c in outcome.citations] == ["a1"]
    assert outcome.offer_operator is False


def _article(article_id: str, question: str, answer: str, score: float) -> RetrievedDoc:
    return RetrievedDoc(article_id, question, answer, "Котировочные сессии", 1, score=score)


async def test_leading_question_does_not_confirm_invented_term(client, monkeypatch):
    """Стенд: «снимут через 3 рабочих дня?» — модель поддакнула, а в базе срока нет.

    Слова человека подтверждают только его номера и контакты, не сроки: вместо
    поддакивания человек получает статью — «обратитесь в СКК».
    """
    article = "По вопросам блокировки личного кабинета обратитесь в СКК через форму обратной связи."
    monkeypatch.setattr(
        agent_module,
        "build_model",
        lambda dyn: _searched_then("Да, блокировка снимется автоматически через 3 рабочих дня."),
    )
    dyn = client.app.state.rt.dyn
    outcome = await run_support_agent(
        dyn,
        _Retriever([_article("blk", "Что делать при блокировке ЛК?", article, 0.9)]),
        _Classifier(),
        AgentRequest("Блокировку ЛК снимут автоматически через 3 рабочих дня?"),
    )
    assert outcome.answered is True
    assert outcome.answer == f"{dyn.get('answer.extracted_prefix')}\n\n{article}"
    assert outcome.grounding == {"unconfirmed": ["3 рабочих дня"], "fallback": "article", "article_id": "blk"}


_CUSTOMER = ("cust", "Как заказчику создать котировочную сессию?", "Заказчик создаёт КС в разделе «Закупки».")
_SUPPLIER = (
    "supp",
    "Как поставщику участвовать в котировочной сессии?",
    "Поставщик подаёт предложение в карточке КС на странице «Котировочные сессии».",
)


async def test_fallback_uses_the_article_the_model_retold(client, monkeypatch):
    """Верхняя статья — для заказчика; модель верно ответила по второй, для поставщика,
    но дописала срок. Откат — на вторую: верхняя дала бы уверенно неверный ответ."""
    monkeypatch.setattr(
        agent_module,
        "build_model",
        lambda dyn: _searched_then(
            "Поставщик подаёт предложение в карточке КС на странице «Котировочные сессии» в течение 2 часов."
        ),
    )
    outcome = await run_support_agent(
        client.app.state.rt.dyn,
        _Retriever([_article(*_CUSTOMER, 0.80), _article(*_SUPPLIER, 0.75)]),
        _Classifier(),
        AgentRequest("как участвовать в котировочной сессии"),
    )
    assert outcome.answered is True
    assert outcome.answer.endswith(_SUPPLIER[2])
    assert outcome.grounding == {"unconfirmed": ["2 часов"], "fallback": "article", "article_id": "supp"}
    assert [c["article_id"] for c in outcome.citations] == ["supp", "cust"]
    assert outcome.confidence == pytest.approx(0.75)


async def test_fallback_refuses_when_retold_article_is_unclear(client, monkeypatch):
    """Пересказ не похож ни на одну из уверенных статей — неясно, что пересказано: отказ."""
    monkeypatch.setattr(
        agent_module,
        "build_model",
        lambda dyn: _searched_then("Блокировка снимется автоматически через 3 рабочих дня."),
    )
    outcome = await run_support_agent(
        client.app.state.rt.dyn,
        _Retriever([_article(*_CUSTOMER, 0.80), _article(*_SUPPLIER, 0.75)]),
        _Classifier(),
        AgentRequest("когда снимут блокировку"),
    )
    assert outcome.answered is False
    assert outcome.offer_operator is True
    assert outcome.grounding == {"unconfirmed": ["3 рабочих дня"], "fallback": "refusal"}


async def test_grounded_answer_is_kept_and_cleaned(client, monkeypatch):
    """Подтверждённый пересказ остаётся пересказом — только без служебных пометок."""
    monkeypatch.setattr(
        agent_module,
        "build_model",
        lambda dyn: _searched_then(
            "Оферту можно отозвать в **личном кабинете** "
            "【3007420c9b1e4f0aa1b2c3d4e5f60718】 (score = 0.7228)."
        ),
    )
    outcome = await run_support_agent(
        client.app.state.rt.dyn, _Retriever([_doc(0.92)]), _Classifier(), AgentRequest("как отозвать оферту")
    )

    assert outcome.answered is True
    assert outcome.answer == "Оферту можно отозвать в **личном кабинете**."
    assert outcome.grounding == {"unconfirmed": [], "fallback": None}
    assert outcome.offer_operator is False


async def test_model_does_not_see_article_ids(client, monkeypatch):
    """Чего модель не видела, того она и не вставит в ответ как «【3007420c…】»."""
    seen: list = []

    def reply(messages: list, info) -> ModelResponse:
        returns = [p for m in messages for p in m.parts if p.part_kind == "tool-return"]
        if not returns:
            return ModelResponse(parts=[ToolCallPart("search_kb", {"query": "оферта"})])
        seen.append(returns[-1].content)
        return ModelResponse(parts=[TextPart("Оферту можно отозвать в личном кабинете.")])

    monkeypatch.setattr(agent_module, "build_model", lambda dyn: FunctionModel(reply))
    await run_support_agent(
        client.app.state.rt.dyn, _Retriever([_doc(0.92)]), _Classifier(), AgentRequest("как отозвать оферту")
    )
    assert seen and all("article_id" not in r for r in seen[0]["results"])


async def test_no_answer_with_explanation_is_a_refusal(client, monkeypatch):
    """Модель дописала к NO_ANSWER пояснение — это всё равно отказ, а не ответ."""
    monkeypatch.setattr(
        agent_module, "build_model", lambda dyn: _searched_then("В базе такого нет, поэтому NO_ANSWER")
    )
    outcome = await run_support_agent(
        client.app.state.rt.dyn, _Retriever([_doc(0.92)]), _Classifier(), AgentRequest("как отозвать оферту")
    )
    assert outcome.answered is False
    assert outcome.answer == _refusal(client)
    assert outcome.offer_operator is True


async def test_refusal_offers_operator_button(client, monkeypatch):
    """Отказ по порогу уведомляет и предлагает оператора — кнопкой, а не молчаливым переводом."""
    monkeypatch.setattr(
        agent_module, "build_model", lambda dyn: _searched_then("Оферту можно отозвать в личном кабинете.")
    )
    outcome = await run_support_agent(
        client.app.state.rt.dyn, _Retriever([_doc(0.05)]), _Classifier(), AgentRequest("как получить вычет")
    )
    assert outcome.answered is False
    assert outcome.escalated is False
    assert outcome.offer_operator is True
    assert "«Позвать оператора»" in outcome.answer


# --- эскалация только по просьбе ---


class _Router:
    """Классификатор линии, который запоминает, звали ли его."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def predict(self, text: str, audience: str | None = None):
        # Роль и адресат — как у настоящего LineClassifier: агент передаёт роль
        # собеседника, а своей линией называет адресата, если он есть
        self.calls.append(text)
        return SimpleNamespace(line=2, proba=0.9, source="test", addressee_label=None)


async def test_escalation_without_request_becomes_refusal_with_offer(client, monkeypatch):
    """«Как получить налоговый вычет» молча уходил на линию 1 — человек оператора не просил.

    Теперь это отказ с кнопкой «Позвать оператора»: решает сам человек.
    """
    monkeypatch.setattr(
        agent_module,
        "build_model",
        lambda dyn: _model(
            ModelResponse(parts=[ToolCallPart("escalate_to_operator", {"reason": "вопрос вне базы"})]),
            ModelResponse(parts=[TextPart(NO_ANSWER)]),
        ),
    )
    router = _Router()
    outcome = await run_support_agent(
        client.app.state.rt.dyn, _Retriever(), router, AgentRequest("как получить налоговый вычет")
    )

    assert outcome.escalated is False
    assert outcome.answered is False
    assert outcome.offer_operator is True
    assert outcome.answer == _refusal(client)
    assert router.calls == [], "без просьбы до выбора линии дело не доходит"
    assert outcome.tool_trace[0]["declined"] is True


async def test_declined_escalation_cannot_promise_transfer(client, monkeypatch):
    """Эскалацию отклонили, а модель пишет «перевожу» — это ложь, показываем отказ."""
    monkeypatch.setattr(
        agent_module,
        "build_model",
        lambda dyn: _model(
            ModelResponse(parts=[ToolCallPart("escalate_to_operator", {"reason": "сложный вопрос"})]),
            ModelResponse(parts=[ToolCallPart("search_kb", {"query": "оферта"})]),
            ModelResponse(parts=[TextPart("Перевожу вас на оператора линии 1.")]),
        ),
    )
    outcome = await run_support_agent(
        client.app.state.rt.dyn, _Retriever([_doc(0.92)]), _Router(), AgentRequest("как отозвать оферту")
    )
    assert outcome.escalated is False
    assert outcome.answered is False
    assert outcome.offer_operator is True


@pytest.mark.parametrize(
    "text, offered",
    [
        ("позовите оператора, пожалуйста", False),
        # «да» в ответ на отказ базы знаний с предложением оператора — та же просьба
        ("да, давайте", True),
    ],
)
async def test_escalation_on_request(client, monkeypatch, text, offered):
    dyn = client.app.state.rt.dyn
    history = (
        [("user", "как получить вычет"), ("assistant", str(dyn.get("agent.no_answer_message")))]
        if offered
        else []
    )
    monkeypatch.setattr(
        agent_module,
        "build_model",
        lambda dyn: _model(
            ModelResponse(parts=[ToolCallPart("escalate_to_operator", {"reason": text})]),
            ModelResponse(parts=[TextPart("Перевожу на оператора.")]),
        ),
    )
    router = _Router()
    outcome = await run_support_agent(
        client.app.state.rt.dyn, _Retriever(), router, AgentRequest(text, history=history)
    )

    assert outcome.escalated is True
    assert outcome.line == 2
    assert outcome.offer_operator is False
    assert router.calls == [text]


async def test_line_is_chosen_by_the_person_not_by_model_paraphrase(client, monkeypatch):
    """Линию выбирают слова человека, а не пересказ модели в причине эскалации.

    На проде «не видит сертификат электронной подписи, позовите оператора» уехало на
    консультации: модель написала в причине «пользователь просит оператора», и
    классификатор не увидел в ней ни подписи, ни сертификата.
    """
    text = "не видит сертификат электронной подписи, позовите оператора"
    monkeypatch.setattr(
        agent_module,
        "build_model",
        lambda dyn: _model(
            ModelResponse(
                parts=[ToolCallPart("escalate_to_operator", {"reason": "Пользователь просит оператора"})]
            ),
            ModelResponse(parts=[TextPart("Перевожу на оператора.")]),
        ),
    )
    router = _Router()
    outcome = await run_support_agent(client.app.state.rt.dyn, _Retriever(), router, AgentRequest(text))

    assert outcome.escalated is True
    assert router.calls == [text]


def test_yes_is_a_request_only_after_a_real_offer(client):
    """«Да» — просьба об операторе только в ответ на предложение самой системы.

    Слова «оператор» в прошлой реплике мало: оно есть в приветствии («…переведу
    на оператора») и в статьях, а «хорошо» в ответ на них оператора не просит.
    """
    dyn = client.app.state.rt.dyn
    offers = agent_module._operator_offers(dyn)
    refusal = [("assistant", str(dyn.get("agent.no_answer_message")))]
    template = str(dyn.get("routing.case_offer_message"))
    case_offer = [("assistant", template.format(topic="УПД", line=2, line_label="техподдержка"))]
    # тема не известна — пустые скобки из текста предложения убраны (steps._maybe_offer_operator)
    no_topic = re.sub(r"\s*\(\s*\)", "", template.format(topic="", line=1, line_label="консультации"))
    assert agent_module.asked_for_operator("да", refusal, offers) is True
    assert agent_module.asked_for_operator("да, давайте", case_offer, offers) is True
    assert agent_module.asked_for_operator("хорошо", [("assistant", no_topic)], offers) is True
    assert agent_module.asked_for_operator("да, как отозвать оферту?", refusal, offers) is False

    greeting = [("assistant", str(dyn.get("chat.greeting")))]
    kb_answer = [("user", "не могу войти"), ("assistant", "Если не удалось, обратитесь к оператору СКК.")]
    for reply in ("хорошо", "да", "ок", "пожалуйста"):
        assert agent_module.asked_for_operator(reply, greeting, offers) is False, reply
        assert agent_module.asked_for_operator(reply, kb_answer, offers) is False, reply


async def test_ok_after_greeting_does_not_open_escalation(client, monkeypatch):
    """Приветствие обещает «переведу на оператора», но «ок» на него — не просьба о человеке."""
    dyn = client.app.state.rt.dyn
    monkeypatch.setattr(
        agent_module,
        "build_model",
        lambda dyn: _model(
            ModelResponse(parts=[ToolCallPart("escalate_to_operator", {"reason": "ок"})]),
            ModelResponse(parts=[TextPart(NO_ANSWER)]),
        ),
    )
    router = _Router()
    outcome = await run_support_agent(
        dyn, _Retriever(), router, AgentRequest("ок", history=[("assistant", str(dyn.get("chat.greeting")))])
    )
    assert outcome.escalated is False
    assert outcome.offer_operator is True
    assert router.calls == []


# --- обещание перевода и слова статьи ---


@pytest.mark.parametrize(
    "text, evidence, expected",
    [
        ("Перевожу вас на оператора линии 1.", [], True),
        ("Передаю ваше обращение в СКК.", [], True),
        ("Специалист скоро подключится к диалогу.", [], True),
        # слова инструкций и регламента, а не обещание бота
        ("Переведите переключатель «Активна» в выключенное положение.", [], False),
        ("Организатору передаются полномочия других заказчиков.", [], False),
        # фраза, дословно взятая из статьи, — пересказ, а не обещание
        (
            "Сотрудник заказчика подключится к сессии автоматически.",
            ["Как это работает?\nСотрудник заказчика подключится к сессии автоматически."],
            False,
        ),
    ],
)
def test_transfer_promise_is_first_person_and_not_from_article(text, evidence, expected):
    assert agent_module._promises_transfer(text, evidence) is expected


async def test_article_with_switch_word_survives_declined_escalation(client, monkeypatch):
    """«Переведите переключатель» из статьи после отклонённой эскалации — ответ, а не отказ."""
    article = "Чтобы скрыть оферту, переведите переключатель «Активна» в выключенное положение."
    doc = RetrievedDoc("c1", "Как скрыть оферту?", article, "Оферты", 1, score=0.9)
    monkeypatch.setattr(
        agent_module,
        "build_model",
        lambda dyn: _model(
            ModelResponse(parts=[ToolCallPart("escalate_to_operator", {"reason": "уточнить"})]),
            ModelResponse(parts=[ToolCallPart("search_kb", {"query": "скрыть оферту"})]),
            ModelResponse(parts=[TextPart(article)]),
        ),
    )
    outcome = await run_support_agent(
        client.app.state.rt.dyn, _Retriever([doc]), _Router(), AgentRequest("как скрыть оферту")
    )
    assert outcome.answered is True
    assert outcome.answer == article
    assert outcome.offer_operator is False


async def test_transfer_promise_without_escalation_is_refusal(client, monkeypatch):
    """Модель и не звала эскалацию, а пишет «передаю ваше обращение» — это ложь."""
    monkeypatch.setattr(
        agent_module,
        "build_model",
        lambda dyn: _searched_then(
            "Оферту можно отозвать в личном кабинете. Передаю ваше обращение оператору."
        ),
    )
    outcome = await run_support_agent(
        client.app.state.rt.dyn, _Retriever([_doc(0.92)]), _Classifier(), AgentRequest("как отозвать оферту")
    )
    assert outcome.answered is False
    assert outcome.escalated is False
    assert outcome.offer_operator is True
