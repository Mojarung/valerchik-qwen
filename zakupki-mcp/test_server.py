"""Слой инструментов MCP: то, что видит агент.

Тесты клиента портала есть отдельно (test_portal.py), но между клиентом и
агентом лежит слой, который никто не проверял: он приводит параметры к
допустимым значениям и превращает отказ портала в понятный ответ. Ошибка
загрузки этого модуля прошла бы `ruff check` и прежний `pytest` насквозь.
"""

import httpx
import pytest

import server
from portal import PortalStats


def _mock_portal(handler) -> None:
    """Подменить транспорт того клиента, которым пользуются инструменты."""
    server.portal._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))


@pytest.fixture(autouse=True)
def _restore_client():
    original = server.portal._client
    yield
    server.portal._client = original
    # Клиент портала один на процесс, и пометка «портал недоступен» живёт в нём.
    # Не сбросить её — следующий тест получит ошибку, не дойдя до своей подмены.
    server.portal.stats = PortalStats()


def _card(sku_id: int) -> dict:
    """Позиция выдачи в том виде, в каком её отдаёт портал."""
    return {
        "id": sku_id,
        "name": f"Ноутбук {sku_id}",
        "skuImageId": 100 + sku_id,
        "productionDirectoryName": "Техника",
    }


@pytest.mark.asyncio
async def test_search_limits_are_clamped():
    """limit приходит от модели: она способна попросить и сто, и минус один."""
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        return httpx.Response(200, json={"items": [_card(i) for i in range(30)], "count": 30})

    _mock_portal(handler)
    result = await server.search_products("ноутбук", limit=99)
    assert 0 < len(result["products"]) <= 20, "потолок выдачи не должен зависеть от фантазии модели"
    assert '"take": 20' in seen["url"] or "take%22%3A+20" in seen["url"]

    result = await server.search_products("ноутбук", limit=0)
    assert len(result["products"]) >= 1


@pytest.mark.asyncio
async def test_unknown_sort_is_explained_not_crashed():
    """Модель придумывает значения: «по цене», «cheapest».

    Ответ должен называть допустимые варианты — по нему модель исправится сама,
    а трассировка стека ей ничего не говорит.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"items": [_card(1)], "count": 1})

    _mock_portal(handler)
    result = await server.search_products("ноутбук", sort="самое дешёвое")
    assert "error" in result
    assert "relevance" in result["error"], "в ошибке должны быть перечислены допустимые сортировки"


@pytest.mark.asyncio
async def test_portal_failure_becomes_a_readable_answer():
    """Портал отвечает 500 — агент должен получить объяснение, а не трассировку."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="oops")

    _mock_portal(handler)
    result = await server.search_products("ноутбук")
    assert result.get("error"), "отказ портала обязан приезжать полем error"
    assert isinstance(result["error"], str) and result["error"].strip()


@pytest.mark.asyncio
async def test_status_reports_availability():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"items": [_card(1)], "count": 1})

    _mock_portal(handler)
    status = await server.catalog_status()
    assert "error" not in status or not status["error"]
    assert status["portal_down"] is False


@pytest.mark.asyncio
async def test_unreachable_portal_answers_at_once_and_status_shows_it():
    """Прод: сервер не достаёт до портала (гео-блок), каждый вызов ждал connect-таймаут.

    После первой неудачи соединения инструменты отвечают той же ошибкой сразу, а
    catalog_status показывает пометку — и сам в сеть тоже не ходит.
    """
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise httpx.ConnectTimeout("timed out", request=request)

    _mock_portal(handler)
    first = await server.search_products("ноутбук 16gb")
    again = await server.search_products("ноутбук на 16 гигабайт")
    status = await server.catalog_status()

    assert first["products"] == [] and "ConnectTimeout" in first["error"]
    assert again == {"error": first["error"], "products": []}, "та же ошибка, что и в первый раз"
    assert calls == 1, "пока стоит пометка, ни поиск, ни проверка статуса в сеть не ходят"
    assert status["alive"] is False
    assert status["portal_down"] is True
    assert status["down_for_s"] > 0
    assert "ConnectTimeout" in status["last_error"]
