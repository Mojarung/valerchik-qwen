"""Внешние инструменты (MCP): что из их ответа доходит до человека.

Прод: сервер не достаёт до портала, search_products отвечает
{"error": "каталог портала сейчас недоступен (…ConnectTimeout)", "products": []},
а модель пишет по памяти, как искать на портале: «откройте раздел Каталог,
фильтр Оперативная память, если он доступен». Это уходило человеку, потому что
сам факт вызова внешнего инструмента снимал и порог базы знаний, и сверку
фактов. Здесь — что помогает только инструмент, вернувший данные: ошибка даёт
честное «каталог не отвечает», пустая выдача — обычные проверки базы знаний.
"""

import json
from typing import Any

import pytest
from pydantic_ai import ModelResponse, TextPart, ToolCallPart
from pydantic_ai.exceptions import ModelRetry
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.toolsets import FunctionToolset

from app.services import agent as agent_module
from app.services.agent import AgentRequest, run_support_agent
from app.services.retrieval import RetrievedDoc

QUESTION = "найди на сайте ноутбуки на 16gb"
# Того же рода, что ответ с прода: дорога по порталу, которой в данных не было
INVENTED = (
    "Откройте раздел «Каталог» и в фильтре «Оперативная память» выберите 16 ГБ, если такой фильтр доступен."
)
DOWN = {"error": "каталог портала сейчас недоступен (портал не ответил: ConnectTimeout)", "products": []}
NOTHING = {
    "query": "ноутбук 16gb",
    "total": 0,
    "products": [],
    "source": "Портал поставщиков, zakupki.mos.ru",
    "hint": "По этому запросу в каталоге ничего нет. Попробуйте более общее слово.",
}


def _product(n: int) -> dict:
    return {
        "sku_id": n,
        "name": f"Ноутбук {n}",
        "url": f"https://zakupki.mos.ru/sku/view/{n}",
        "image": f"https://zakupki.mos.ru/newapi/api/Core/Thumbnail/{n}/300/300",
    }


FOUND = {"query": "ноутбук 16gb", "total": 2, "products": [_product(1), _product(2)]}
# Статья, которую поиск находит почти на любой вопрос: на импортированной базе
# оценка около 0.5 — выше порога, но о товарах в ней ни слова.
SMALL_PURCHASE = (
    "Закупку малого объёма заказчик публикует в разделе «Закупки», а поставщики подают "
    "предложения до окончания котировочной сессии."
)


def _small_purchase(score: float) -> RetrievedDoc:
    return RetrievedDoc(
        "smp", "Как провести закупку малого объёма?", SMALL_PURCHASE, "Закупки", 1, score=score
    )


class _Retriever:
    def __init__(self, docs: list[RetrievedDoc]) -> None:
        self.docs = docs

    async def search(self, query: str, context: str = "", audience: str = "") -> list[RetrievedDoc]:
        return list(self.docs)


class _Classifier:
    async def predict(self, text: str, audience: str | None = None):
        raise AssertionError("эскалации здесь не ожидается")


def _catalog(*answers: object) -> FunctionToolset:
    """Каталог с одним search_products: отвечает по очереди заданным, исключение — бросает.

    Последний ответ повторяется: модель вправе позвать инструмент ещё раз.
    """
    queue = list(answers)

    async def search_products(query: str) -> Any:
        """Найти товары в каталоге Портала поставщиков."""
        answer = queue.pop(0) if len(queue) > 1 else queue[0]
        if isinstance(answer, BaseException):
            raise answer
        return answer

    return FunctionToolset([search_products])


def _model(*responses: ModelResponse) -> FunctionModel:
    steps = iter(responses)
    return FunctionModel(lambda messages, info: next(steps))


def _search_kb_and_catalog() -> ModelResponse:
    """Как на проде: база знаний и каталог одним шагом."""
    return ModelResponse(
        parts=[
            ToolCallPart("search_kb", {"query": QUESTION}),
            ToolCallPart("search_products", {"query": "ноутбук 16gb"}),
        ]
    )


async def _ask(client, monkeypatch, model: FunctionModel, catalog: FunctionToolset, docs: list[RetrievedDoc]):
    monkeypatch.setattr(agent_module, "build_model", lambda dyn: model)
    return await run_support_agent(
        client.app.state.rt.dyn,
        _Retriever(docs),
        _Classifier(),
        AgentRequest(QUESTION, mcp_toolsets=[catalog]),
    )


@pytest.mark.parametrize(
    "failure",
    [
        pytest.param(DOWN, id="error-key"),
        pytest.param(json.dumps(DOWN, ensure_ascii=False), id="json-string"),
        pytest.param([{"type": "text", "text": json.dumps(DOWN, ensure_ascii=False)}], id="mcp-blocks"),
        # isError сервера MCP: pydantic-ai превращает его в ModelRetry
        pytest.param(ModelRetry("Tool 'search_products' returned an error"), id="is-error"),
        pytest.param(RuntimeError("соединение с MCP оборвалось"), id="exception"),
    ],
)
async def test_catalog_failure_is_reported_honestly_instead_of_a_guess(client, monkeypatch, failure):
    """Каталог не ответил — человек так и узнаёт, а не получает сочинённую инструкцию.

    Поиск по базе знаний при этом нашёл статью выше порога (на импортированной
    базе это почти любой вопрос): прежде этого хватало, чтобы выдумка ушла человеку.
    """
    dyn = client.app.state.rt.dyn
    model = _model(_search_kb_and_catalog(), ModelResponse(parts=[TextPart(INVENTED)]))

    outcome = await _ask(client, monkeypatch, model, _catalog(failure), [_small_purchase(0.5)])

    assert outcome.answer == dyn.get("mcp.unavailable_message")
    assert outcome.answered is False
    assert outcome.escalated is False
    assert outcome.offer_operator is True
    assert outcome.products == []
    assert outcome.citations == []
    assert {"tool": "search_products", "source": "mcp", "result": "error"} in outcome.tool_trace


async def test_catalog_that_answers_on_retry_is_an_answer(client, monkeypatch):
    """Первая попытка упала, вторая принесла товары: каталог помог — карточки и текст модели."""
    text = "Нашёл в каталоге подходящие ноутбуки, карточки ниже."
    model = _model(
        ModelResponse(parts=[ToolCallPart("search_products", {"query": "ноутбук 16gb"})]),
        ModelResponse(parts=[ToolCallPart("search_products", {"query": "ноутбук"})]),
        ModelResponse(parts=[TextPart(text)]),
    )

    outcome = await _ask(client, monkeypatch, model, _catalog(DOWN, FOUND), [])

    assert outcome.answered is True
    assert outcome.answer == text
    assert [p["sku_id"] for p in outcome.products] == [1, 2]
    assert [t.get("result") for t in outcome.tool_trace if t.get("source") == "mcp"] == ["error", "data"]


@pytest.mark.parametrize(
    "score, answered",
    [
        pytest.param(0.1, False, id="weak-article-refusal"),
        pytest.param(0.9, True, id="confident-article-shown-instead"),
    ],
)
async def test_empty_catalog_does_not_lift_the_knowledge_base_checks(client, monkeypatch, score, answered):
    """Каталог ответил, но ничего не нашёл — это не данные, и выдумка не проходит.

    Слабая статья — отказ по порогу. Уверенная, но не о том — сверка фактов не
    находит в ней ни «Каталога», ни «16 ГБ» и показывает саму статью. Текста
    модели человек не видит ни в том, ни в другом случае.
    """
    dyn = client.app.state.rt.dyn
    model = _model(_search_kb_and_catalog(), ModelResponse(parts=[TextPart(INVENTED)]))

    outcome = await _ask(client, monkeypatch, model, _catalog(NOTHING), [_small_purchase(score)])

    assert outcome.answered is answered
    assert "Оперативная память" not in outcome.answer
    assert outcome.products == []
    if answered:
        assert outcome.grounding["fallback"] == "article"
        assert {"«Каталог»", "«Оперативная память»"} <= set(outcome.grounding["unconfirmed"])
        assert outcome.answer.endswith(SMALL_PURCHASE)
    else:
        assert outcome.answer == dyn.get("agent.no_answer_message")
        assert outcome.offer_operator is True


async def test_empty_catalog_without_knowledge_base_search_is_a_refusal(client, monkeypatch):
    """Ни статей, ни товаров — ответу не на что опереться: отказ с предложением оператора."""
    dyn = client.app.state.rt.dyn
    model = _model(
        ModelResponse(parts=[ToolCallPart("search_products", {"query": "ноутбук 16gb"})]),
        ModelResponse(parts=[TextPart(INVENTED)]),
    )

    outcome = await _ask(client, monkeypatch, model, _catalog(NOTHING), [])

    assert outcome.answered is False
    assert outcome.answer == dyn.get("agent.no_answer_message")
    assert outcome.offer_operator is True


async def test_products_are_cards_and_the_models_text_is_the_answer(client, monkeypatch):
    """Настоящие товары — как раньше: карточки из инструмента и текст модели.

    Порог базы знаний тут ни при чём: низкая оценка статьи значит лишь «в базе
    знаний такого нет». И сверять «16 ГБ» не с чем — статей за ответом каталога нет.
    """
    text = "Нашёл в каталоге ноутбуки с 16 ГБ памяти — карточки ниже."
    model = _model(_search_kb_and_catalog(), ModelResponse(parts=[TextPart(text)]))

    outcome = await _ask(client, monkeypatch, model, _catalog(FOUND), [_small_purchase(0.1)])

    assert outcome.answered is True
    assert outcome.answer == text
    assert outcome.offer_operator is False
    assert outcome.grounding is None
    assert [p["sku_id"] for p in outcome.products] == [1, 2]
    assert outcome.products[0]["image"] == "/api/catalog/image/1?w=300&h=300"
    assert {"tool": "search_products", "source": "mcp", "result": "data"} in outcome.tool_trace


@pytest.mark.parametrize(
    "content, expected",
    [
        (DOWN, "error"),
        (json.dumps(DOWN), "error"),
        (NOTHING, "empty"),
        ([{"type": "text", "text": json.dumps(NOTHING)}], "empty"),
        (FOUND, "data"),
        # товары — данные, даже если рядом пометка об ошибке: карточки всё равно настоящие
        ({"error": "часть выдачи из кэша", "products": [_product(3)]}, "data"),
        ({"error": "", "products": [_product(4)]}, "data"),
        ({"alive": True, "catalog_size": 100}, "data"),
        ({"results": [], "note": ""}, "empty"),
        ("Курс доллара на сегодня — 90 рублей", "data"),
        ("", "empty"),
        (None, "empty"),
    ],
)
def test_what_counts_as_help_from_an_external_tool(content, expected):
    assert agent_module._tool_result(content) == expected
