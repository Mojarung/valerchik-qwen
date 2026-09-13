"""Пределы ReAct-цикла и объём контекста агента.

Цикл ограничивал только таймаут в сто секунд, а в модель уходили полные тексты
статей и полные сообщения истории. Здесь — что зациклившаяся модель получает тот
же честный отказ, что и по таймауту, а сокращённая выдача не мешает сверке
фактов: та по-прежнему видит статьи целиком.
"""

import pytest
from pydantic_ai import ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import FunctionModel

from app.services import agent as agent_module
from app.services.agent import NO_ANSWER, AgentRequest, run_support_agent
from app.services.retrieval import RetrievedDoc


class _Retriever:
    """Одна и та же выдача на любой запрос; запросы запоминает."""

    def __init__(self, docs: list[RetrievedDoc]) -> None:
        self.docs = docs
        self.queries: list[str] = []

    async def search(self, query: str, context: str = "", audience: str = "") -> list[RetrievedDoc]:
        self.queries.append(query)
        return list(self.docs)


class _Classifier:
    async def predict(self, text: str, audience: str | None = None):
        raise AssertionError("эскалации здесь не ожидается")


def _doc(answer: str, score: float = 0.9) -> RetrievedDoc:
    return RetrievedDoc("a1", "Как подать жалобу на заказчика?", answer, "Жалобы", 1, score=score)


def _refusal(client) -> str:
    return str(client.app.state.rt.dyn.get("agent.no_answer_message"))


# --- предел шагов ---


def _endless_search(messages: list, info) -> ModelResponse:
    """Модель, которая никогда не отвечает: снова и снова переформулирует поиск."""
    return ModelResponse(parts=[ToolCallPart("search_kb", {"query": f"жалоба, попытка {len(messages)}"})])


async def test_endless_search_is_cut_by_the_step_limit(client, monkeypatch):
    """Не таймаут: на тридцать секунд модель не тратит, отказ приходит сразу."""
    dyn = client.app.state.rt.dyn
    monkeypatch.setattr(agent_module, "build_model", lambda dyn: FunctionModel(_endless_search))
    retriever = _Retriever([_doc("Жалобу подают в личном кабинете.")])

    outcome = await run_support_agent(
        dyn, retriever, _Classifier(), AgentRequest("как подать жалобу", timeout_s=30)
    )

    assert len(retriever.queries) == dyn.get("agent.max_steps")
    assert outcome.answered is False
    assert outcome.escalated is False
    assert outcome.offer_operator is True
    assert outcome.answer == _refusal(client)


async def test_parallel_calls_are_held_by_the_tool_call_limit(client, monkeypatch):
    """За один шаг модель зовёт несколько инструментов — шаги их число не держат."""
    dyn = client.app.state.rt.dyn
    monkeypatch.setitem(dyn._values, "agent.max_tool_calls", 3)

    def fan_out(messages: list, info) -> ModelResponse:
        return ModelResponse(
            parts=[ToolCallPart("search_kb", {"query": f"жалоба {len(messages)}-{i}"}) for i in range(2)]
        )

    monkeypatch.setattr(agent_module, "build_model", lambda dyn: FunctionModel(fan_out))
    retriever = _Retriever([_doc("Жалобу подают в личном кабинете.")])

    outcome = await run_support_agent(
        dyn, retriever, _Classifier(), AgentRequest("как подать жалобу", timeout_s=30)
    )

    assert len(retriever.queries) == 2, "третий и четвёртый вызов уже за пределом"
    assert outcome.answered is False
    assert outcome.answer == _refusal(client)


async def test_ordinary_answer_fits_the_limits(client, monkeypatch):
    """Поиск, уточнённый поиск и ответ — обычный путь, пределы его не задевают."""
    dyn = client.app.state.rt.dyn
    monkeypatch.setitem(dyn._values, "agent.max_steps", 3)
    steps = iter(
        [
            ModelResponse(parts=[ToolCallPart("search_kb", {"query": "жалоба"})]),
            ModelResponse(parts=[ToolCallPart("search_kb", {"query": "как подать жалобу"})]),
            ModelResponse(parts=[TextPart("Жалобу подают в личном кабинете.")]),
        ]
    )
    monkeypatch.setattr(agent_module, "build_model", lambda dyn: FunctionModel(lambda m, i: next(steps)))

    outcome = await run_support_agent(
        dyn, _Retriever([_doc("Жалобу подают в личном кабинете.")]), _Classifier(), AgentRequest("жалоба")
    )

    assert outcome.answered is True


def test_zero_means_no_limit(client, monkeypatch):
    dyn = client.app.state.rt.dyn
    monkeypatch.setitem(dyn._values, "agent.max_steps", 0)
    monkeypatch.setitem(dyn._values, "agent.max_tool_calls", 0)
    limits = agent_module._usage_limits(dyn)
    assert limits.request_limit is None
    assert limits.tool_calls_limit is None


# --- объём контекста ---

LEAD = "Жалобу на действия заказчика подают в личном кабинете."
FILLER = "Раздел описывает общий порядок работы с обращениями на портале."
TERM = "Комиссия рассматривает обращение в течение 10 рабочих дней."
KEY = "Комиссия рассматривает жалобу в течение 10 рабочих дней."


def _long_article(tail: str) -> str:
    """Статья регламента: вводный абзац, три десятка пунктов и нужное место в середине."""
    points = [f"Пункт {i}. {FILLER}" for i in range(1, 31)]
    return "\n".join([LEAD, *points[:15], tail, *points[15:]])


def test_long_article_is_cut_to_the_paragraphs_closest_to_the_query():
    text = _long_article(KEY)

    excerpt = agent_module._excerpt(text, "за сколько комиссия рассматривает жалобу", 400)

    assert len(excerpt) <= 400
    assert excerpt.startswith(LEAD), "вводный абзац — предмет статьи, он остаётся всегда"
    assert KEY in excerpt, "нужное место из середины статьи модель видит"
    assert "\n…\n" in excerpt, "пропуск помечен: выдержку не выдать за статью целиком"
    # короткая статья и нулевой предел — как есть
    assert agent_module._excerpt(LEAD, "жалоба", 400) == LEAD
    assert agent_module._excerpt(text, "жалоба", 0) == text


def test_one_huge_paragraph_is_cut_by_sentences():
    """Статья без переносов строк — один абзац: режем по предложениям, а не на полуслове."""
    text = " ".join([LEAD, *(f"Пункт {i}. {FILLER}" for i in range(1, 31)), KEY])

    excerpt = agent_module._excerpt(text, "за сколько комиссия рассматривает жалобу", 300)

    assert len(excerpt) <= 300
    assert KEY in excerpt


async def test_model_sees_an_excerpt_but_grounding_sees_the_whole_article(client, monkeypatch):
    """Срок из хвоста статьи модель знает не из этой выдачи (скажем, из прошлого ответа
    в истории). Сверяй факты по выдержке — верный срок стал бы «неподтверждённым», и
    ответ заменился бы текстом статьи или отказом."""
    dyn = client.app.state.rt.dyn
    monkeypatch.setitem(dyn._values, "agent.tool_article_chars", 200)
    answer = "Комиссия рассматривает обращение в течение 10 рабочих дней."
    seen: list[str] = []

    def reply(messages: list, info) -> ModelResponse:
        returns = [p for m in messages for p in m.parts if p.part_kind == "tool-return"]
        if not returns:
            return ModelResponse(parts=[ToolCallPart("search_kb", {"query": "как подать жалобу"})])
        seen.append(returns[-1].content["results"][0]["answer"])
        return ModelResponse(parts=[TextPart(answer)])

    monkeypatch.setattr(agent_module, "build_model", lambda dyn: FunctionModel(reply))

    outcome = await run_support_agent(
        dyn, _Retriever([_doc(_long_article(TERM))]), _Classifier(), AgentRequest("как подать жалобу")
    )

    assert len(seen[0]) <= 200
    assert "10 рабочих дней" not in seen[0], "сценарий теста — срок за пределами выдержки"
    assert outcome.answered is True
    assert outcome.answer == answer
    assert outcome.grounding == {"unconfirmed": [], "fallback": None}


async def test_fallback_shows_the_whole_article_not_the_excerpt(client, monkeypatch):
    """Пересказ с выдуманным сроком заменяется статьёй — целой, а не тем, что видела модель."""
    dyn = client.app.state.rt.dyn
    monkeypatch.setitem(dyn._values, "agent.tool_article_chars", 200)
    steps = iter(
        [
            ModelResponse(parts=[ToolCallPart("search_kb", {"query": "как подать жалобу"})]),
            ModelResponse(parts=[TextPart("Жалобу подают в личном кабинете за 3 дня.")]),
        ]
    )
    monkeypatch.setattr(agent_module, "build_model", lambda dyn: FunctionModel(lambda m, i: next(steps)))

    outcome = await run_support_agent(
        dyn, _Retriever([_doc(_long_article(KEY))]), _Classifier(), AgentRequest("как подать жалобу")
    )

    assert outcome.grounding["fallback"] == "article"
    assert KEY in outcome.answer
    assert outcome.answer.endswith(f"Пункт 30. {FILLER}")


def test_history_messages_are_clipped_in_the_instructions(client, monkeypatch):
    dyn = client.app.state.rt.dyn
    monkeypatch.setitem(dyn._values, "agent.history_message_chars", 100)
    long_answer = "Шаг первый: откройте раздел. " * 40 + "ХВОСТ"
    request = AgentRequest("а сроки?", history=[("user", "Как подать жалобу?"), ("assistant", long_answer)])

    text = agent_module._instructions(dyn, request, "all")

    assert "Пользователь: Как подать жалобу?" in text
    assert "ХВОСТ" not in text
    line = next(row for row in text.splitlines() if row.startswith("Ассистент: "))
    assert len(line) <= len("Ассистент: ") + 100
    assert line.endswith("…")


async def test_verbatim_answer_is_the_whole_article(client, monkeypatch):
    """Дословный ответ идёт мимо модели — предел выдачи агенту его не касается."""
    rt = client.app.state.rt
    monkeypatch.setitem(rt.dyn._values, "agent.tool_article_chars", 100)
    article = _long_article(KEY)
    doc = RetrievedDoc("v1", "Как подать жалобу на заказчика?", article, "Жалобы", 1, score=0.95)

    async def authored(ids):
        return {"v1": {"authored": True}}, {}

    monkeypatch.setattr(rt.chat, "_provenance_of", authored)
    sid = client.post("/api/chat/sessions").json()["session_id"]

    result = await rt.chat._maybe_verbatim(sid, "Как подать жалобу на заказчика?", [doc], 0.0)

    assert result is not None
    assert result["answer"] == article


# --- продолжение прошлого вопроса ---


async def test_follow_up_reaches_the_model_with_the_previous_question(client, monkeypatch):
    """Модель получает реплику вместе с прошлым вопросом и пояснение, что это одна мысль."""
    captured: list = []

    def reply(messages: list, info) -> ModelResponse:
        if len(messages) == 1:
            captured.append(messages[0])
        return ModelResponse(parts=[TextPart(NO_ANSWER)])

    monkeypatch.setattr(agent_module, "build_model", lambda dyn: FunctionModel(reply))
    dyn = client.app.state.rt.dyn
    previous = "Как зарегистрироваться на портале?"

    await run_support_agent(
        dyn, _Retriever([]), _Classifier(), AgentRequest("а для заказчика?", follow_up_of=previous)
    )
    await run_support_agent(dyn, _Retriever([]), _Classifier(), AgentRequest("а для заказчика?"))

    follow_up, standalone = captured
    prompt = next(p.content for p in follow_up.parts if p.part_kind == "user-prompt")
    assert prompt == f"{previous}\nа для заказчика?"
    assert "продолжение его прошлого вопроса" in (follow_up.instructions or "")
    assert next(p.content for p in standalone.parts if p.part_kind == "user-prompt") == "а для заказчика?"
    assert "продолжение его прошлого вопроса" not in (standalone.instructions or "")


@pytest.mark.parametrize("text", ["как подать жалобу", ""])
def test_prompt_without_follow_up_is_the_reply_itself(text):
    assert agent_module._prompt(AgentRequest(text)) == text


@pytest.mark.parametrize("initial", [NO_ANSWER, "Необоснованный ответ без поиска."])
async def test_model_skipping_search_is_retried_before_final_answer(client, monkeypatch, initial):
    steps = iter(
        [
            ModelResponse(parts=[TextPart(initial)]),
            ModelResponse(parts=[ToolCallPart("search_kb", {"query": "жалоба"})]),
            ModelResponse(parts=[TextPart("Жалобу подают в личном кабинете.")]),
        ]
    )
    retriever = _Retriever([_doc("Жалобу подают в личном кабинете.")])
    monkeypatch.setattr(agent_module, "build_model", lambda dyn: FunctionModel(lambda m, i: next(steps)))
    outcome = await run_support_agent(
        client.app.state.rt.dyn, retriever, _Classifier(), AgentRequest("жалоба")
    )
    assert retriever.queries == ["жалоба"]
    assert outcome.answered is True
    assert outcome.citations[0]["article_id"] == "a1"
    assert outcome.answer == "Жалобу подают в личном кабинете."


@pytest.mark.parametrize(
    "generated",
    [
        "Подпишите УКЭП (уникальная криптографическая электронная подпись).",
        "Подпишите укреплённой квалифицированной электронной подписью (УКЭП).",
    ],
)
async def test_generated_abbreviation_expansion_is_removed_before_answer_return(
    client, monkeypatch, generated
):
    steps = iter(
        [
            ModelResponse(parts=[ToolCallPart("search_kb", {"query": "подпись"})]),
            ModelResponse(parts=[TextPart(generated)]),
        ]
    )
    monkeypatch.setattr(agent_module, "build_model", lambda dyn: FunctionModel(lambda m, i: next(steps)))
    source = _doc("Подпишите УКЭП.")
    outcome = await run_support_agent(
        client.app.state.rt.dyn, _Retriever([source]), _Classifier(), AgentRequest("Как подписать документ?")
    )
    assert outcome.answered is True
    assert outcome.answer == "Подпишите УКЭП."
    assert source.answer == "Подпишите УКЭП."
