"""Тесты клиента каталога. Сеть не трогаем: портал подменяется MockTransport.

Отдельно и подробно проверяется ASCII-кодирование фильтра — это не стилистика,
а тот самый инвариант, при нарушении которого портал отвечает 200 и мусором
вместо товаров, то есть ломается молча.
"""

import asyncio
import time

import httpx
import pytest

import portal as portal_module
from portal import (
    OUR_BUG,
    PortalClient,
    PortalError,
    SkuQuery,
    compact_card,
    compact_product,
    original_url,
    product_url,
    thumbnail_url,
)


def _client_with(handler, **options) -> PortalClient:
    client = PortalClient(rps=0, **options)  # без троттлинга: в тестах ждать нечего
    client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return client


async def test_cyrillic_is_escaped_to_ascii_in_the_url():
    """Ни одного не-ASCII байта в URL.

    Правильный UTF-8 портал тоже понимает, но битая кодировка ломается здесь молча:
    ответ 200, а выдача не по делу. ASCII-only убирает весь класс этих ошибок —
    перекодировать по дороге нечего.
    """
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["filter"] = request.url.params["queryFilter"]
        return httpx.Response(200, json={"count": 1, "items": []})

    client = _client_with(handler)
    await client.search(SkuQuery(keyword="ноутбук"))

    assert seen["url"].isascii(), "в URL просочилась кириллица"
    assert "\\u043d\\u043e\\u0443" in seen["filter"], "keyword должен быть \\u-экранирован"


async def test_filter_is_built_from_verified_keys_only():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        import json

        seen.update(json.loads(request.url.params["queryFilter"]))
        return httpx.Response(200, json={"count": 0, "items": []})

    client = _client_with(handler)
    await client.search(
        SkuQuery(keyword="стол", take=5, category_id=42, only_with_offers=True, moscow_only=True)
    )

    assert seen["take"] == 5
    assert seen["withCount"] is True
    assert seen["filter"] == {
        "keyword": "стол",
        "isExact": False,
        "productionDirectoryId": {"values": [42]},
        "hasOffers": True,
        "isMoscowProduction": True,
    }


async def test_price_range_uses_start_end_form():
    """Форма значения у полей разная: диапазон — {start,end}, список — {values}.
    Ключ не той формы портал либо игнорирует, либо отвечает 500."""
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        import json

        seen.update(json.loads(request.url.params["queryFilter"])["filter"])
        return httpx.Response(200, json={"count": 0, "items": []})

    client = _client_with(handler)
    await client.search(SkuQuery(keyword="стол", price_from=100, price_to=5000))
    assert seen["referencePrice"] == {"start": 100, "end": 5000}

    seen.clear()
    await client.search(SkuQuery(keyword="стол", price_to=5000))
    assert seen["referencePrice"] == {"end": 5000}, "открытый снизу диапазон допустим"

    seen.clear()
    await client.search(SkuQuery(keyword="стол"))
    assert "referencePrice" not in seen, "без границ фильтра цены быть не должно"


async def test_repeated_query_is_served_from_cache():
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json={"count": 1, "items": [{"id": 1}]})

    client = _client_with(handler)
    await client.search(SkuQuery(keyword="стул"))
    await client.search(SkuQuery(keyword="стул"))
    assert calls == 1
    assert client.stats.cache_hits == 1


async def test_stale_cache_beats_an_error():
    """Портал упал — отдаём прошлый ответ, а не ошибку: для поддержки это разные вещи."""
    state = {"alive": True}

    def handler(request: httpx.Request) -> httpx.Response:
        if state["alive"]:
            return httpx.Response(200, json={"count": 1, "items": [{"id": 7}]})
        return httpx.Response(503, text="down")

    client = _client_with(handler)
    client._search_ttl = 0  # мгновенно протухает, чтобы не ждать в тесте
    first = await client.search(SkuQuery(keyword="лампа"))
    state["alive"] = False
    second = await client.search(SkuQuery(keyword="лампа"))

    assert second == first
    assert client.stats.stale_hits == 1


async def test_error_propagates_when_nothing_is_cached():
    client = _client_with(lambda request: httpx.Response(503, text="down"))
    with pytest.raises(PortalError):
        await client.search(SkuQuery(keyword="что-то новое"))


async def test_500_on_our_own_bad_filter_is_not_retried():
    """500 с этим телом означает «мы прислали ерунду» — долбить портал незачем."""
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(500, json={"message": OUR_BUG})

    client = _client_with(handler)
    with pytest.raises(PortalError):
        await client.search(SkuQuery(keyword="стол"))
    assert calls == 1


async def test_500_from_their_side_is_retried(monkeypatch):
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(500, json={"message": "Internal"})

    monkeypatch.setattr(portal_module.asyncio, "sleep", _no_sleep)
    client = _client_with(handler)
    with pytest.raises(PortalError):
        await client.search(SkuQuery(keyword="стол"))
    assert calls == 3


async def test_403_is_treated_as_a_hiccup_and_retried(monkeypatch):
    """Портал отдаёт 403 на всплеск и 200 на тот же запрос секундой позже."""
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(403, text="forbidden")
        return httpx.Response(200, json={"count": 1, "items": [{"id": 9}]})

    monkeypatch.setattr(portal_module.asyncio, "sleep", _no_sleep)
    client = _client_with(handler)
    result = await client.sku(9)
    assert result["items"][0]["id"] == 9
    assert calls == 2


async def _no_sleep(_seconds: float) -> None:
    return None


# --- приведение выдачи -------------------------------------------------------


def test_own_photo_and_category_placeholder_are_distinguishable():
    own = compact_product({"id": 1, "name": "Ноутбук", "skuImageId": 500, "skuImageIds": [500, 501]})
    assert own["own_image"] is True
    assert own["image"] == thumbnail_url(500, 300)
    assert own["image_full"] == original_url(500)
    assert own["images"] == [thumbnail_url(500, 300), thumbnail_url(501, 300)]

    fallback = compact_product(
        {
            "id": 2,
            "name": "Услуга",
            "skuImageId": None,
            "skuImageIds": [],
            "productionDirectoryDefaultImageId": 900,
        }
    )
    assert fallback["own_image"] is False, "заглушку категории нельзя выдавать за фото товара"
    assert fallback["image"] == thumbnail_url(900, 300)


def test_zero_and_negative_image_ids_mean_no_image():
    """Портал кодирует «картинки нет» тремя способами: null, 0 и -1."""
    for empty in (None, 0, -1):
        product = compact_product({"id": 3, "name": "Х", "skuImageId": empty, "skuImageIds": [empty]})
        assert product["image"] is None
        assert product["images"] == []


def test_price_kind_is_stated_explicitly():
    assert compact_product({"id": 1, "referencePrice": 100})["price_kind"] == "reference"
    assert compact_product({"id": 1, "medianPrice": 50})["price_kind"] == "median"
    assert compact_product({"id": 1, "costPerUnit": 7})["price_kind"] == "offer"
    empty = compact_product({"id": 1, "referencePrice": 0, "medianPrice": None})
    assert empty["price"] is None and empty["price_kind"] is None


def test_product_link_points_to_the_portal():
    assert product_url(47387580) == "https://zakupki.mos.ru/sku/view/47387580"


def test_characteristics_come_from_the_row_not_the_nested_value():
    """У числовых характеристик stringValue пустой, а справочные держат там id."""
    card = {
        "id": 5,
        "name": "Ноутбук",
        "images": [{"fileStorageId": 11}],
        "skuCharacteristics": [
            {
                "characteristicValueDecimalValue": 1.55,
                "productCharacteristicValue": {
                    "productCharacteristicName": "Вес",
                    "productCharacteristicUnitDesignationNational": "кг",
                    "stringValue": None,
                },
            },
            {
                "characteristicValueDecimalValue": 10.0,
                "productCharacteristicValue": {
                    "productCharacteristicName": "Ядер",
                    "productCharacteristicUnitDesignationNational": "шт",
                },
            },
            {
                "characteristicValueStringValue": "Mac os",
                "productCharacteristicValue": {
                    "productCharacteristicName": "ОС",
                    "stringValue": "1988260825.2017707918",
                },
            },
            {"productCharacteristicValue": {"productCharacteristicName": "Пустая"}},
        ],
    }
    result = compact_card(card)
    assert result["characteristics"] == [
        {"name": "Вес", "value": "1.55 кг"},
        {"name": "Ядер", "value": "10 шт"},  # без «10.0»
        {"name": "ОС", "value": "Mac os"},  # не внутренний идентификатор
    ]
    assert result["url"] == product_url(5)
    assert result["image"] == thumbnail_url(11, 600)


@pytest.mark.parametrize("status", [502, 503, 504, 429])
async def test_gateway_hiccups_are_retried(monkeypatch, status):
    """Перегрузка и сбой шлюза проходят за секунду — такой ответ повторяем,
    а не превращаем сразу в отказ «каталог недоступен»."""
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(status, text="busy")
        return httpx.Response(200, json={"count": 1, "items": [{"id": 3}]})

    monkeypatch.setattr(portal_module.asyncio, "sleep", _no_sleep)
    client = _client_with(handler)
    result = await client.search(SkuQuery(keyword="бумага"))

    assert calls == 2
    assert result["items"] == [{"id": 3}]


# --- портал не виден из нашей сети -------------------------------------------


def _unreachable(failure: type[httpx.TransportError]):
    """Портал, до которого не доходит соединение; запоминает каждую попытку."""
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        raise failure("unreachable", request=request)

    return handler, calls


@pytest.mark.parametrize("failure", [httpx.ConnectTimeout, httpx.ConnectError])
async def test_no_connection_marks_the_portal_down_and_answers_at_once(failure):
    """Прод: гео-блок, и каждый вызов каталога трижды ждал connect-таймаут — около 18 с.

    Соединения нет — повтор его не даст: одна попытка, пометка, а следующие
    запросы получают ту же ошибку сразу, не трогая сеть.
    """
    handler, calls = _unreachable(failure)
    client = _client_with(handler)

    with pytest.raises(PortalError) as first:
        await client.search(SkuQuery(keyword="ноутбук"))
    assert len(calls) == 1, "соединения нет — повтор через секунду стоит ещё одного таймаута"
    assert client.down_for_s() > 0

    with pytest.raises(PortalError) as second:
        await client.sku(47387580)
    assert len(calls) == 1, "пока стоит пометка, в сеть не ходим"
    assert str(second.value) == str(first.value), "та же ошибка, что и в первый раз"
    assert failure.__name__ in str(first.value)
    assert client.stats.down_hits == 1


async def test_stale_cache_is_still_served_while_the_portal_is_down():
    """Пометка не отменяет stale-if-error: прошлый ответ лучше отказа."""
    state = {"reachable": True}

    def handler(request: httpx.Request) -> httpx.Response:
        if state["reachable"]:
            return httpx.Response(200, json={"count": 1, "items": [{"id": 7}]})
        raise httpx.ConnectTimeout("unreachable", request=request)

    client = _client_with(handler)
    client._search_ttl = 0  # мгновенно протухает, чтобы не ждать в тесте
    first = await client.search(SkuQuery(keyword="лампа"))
    state["reachable"] = False

    assert await client.search(SkuQuery(keyword="лампа")) == first  # соединения нет — ставим пометку
    assert await client.search(SkuQuery(keyword="лампа")) == first  # под пометкой — тоже кэш
    assert client.stats.stale_hits == 2


async def test_first_success_after_the_pause_clears_the_mark():
    state = {"reachable": False}

    def handler(request: httpx.Request) -> httpx.Response:
        if not state["reachable"]:
            raise httpx.ConnectTimeout("unreachable", request=request)
        return httpx.Response(200, json={"count": 1, "items": [{"id": 1}]})

    client = _client_with(handler)
    with pytest.raises(PortalError):
        await client.search(SkuQuery(keyword="стол"))
    state["reachable"] = True
    client.stats.down_until = time.monotonic() - 1  # пауза истекла

    assert (await client.search(SkuQuery(keyword="стол")))["count"] == 1
    assert client.down_for_s() == 0
    assert client.stats.down_reason == ""


async def test_success_in_flight_clears_the_mark_at_once():
    """Запрос ушёл до пометки и получил ответ — портал жив: пометку снимаем, не дожидаясь паузы."""
    seen = {"mark": False}

    async def handler(request: httpx.Request) -> httpx.Response:
        if not request.url.path.endswith("/GetSku"):
            raise httpx.ConnectTimeout("unreachable", request=request)
        # Отвечаем, только когда соседний запрос уже поставил пометку
        for _ in range(1000):
            if client.down_for_s():
                seen["mark"] = True
                break
            await asyncio.sleep(0)
        return httpx.Response(200, json={"id": 1})

    client = _client_with(handler)
    card, failed = await asyncio.gather(
        client.sku(1), client.search(SkuQuery(keyword="стол")), return_exceptions=True
    )

    assert seen["mark"], "сценарий теста — ответ пришёл, когда пометка уже стояла"
    assert card == {"id": 1}
    assert isinstance(failed, PortalError)
    assert client.down_for_s() == 0


async def test_portal_that_answers_with_an_error_is_not_marked_down(monkeypatch):
    """503 — портал жив и отвечает: пометки нет, повторы — как раньше."""
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(503, text="busy")

    monkeypatch.setattr(portal_module.asyncio, "sleep", _no_sleep)
    client = _client_with(handler)
    with pytest.raises(PortalError):
        await client.search(SkuQuery(keyword="бумага"))
    assert calls == 3
    assert client.down_for_s() == 0


async def test_zero_pause_turns_the_mark_off():
    handler, calls = _unreachable(httpx.ConnectError)
    client = _client_with(handler, down_pause_s=0)
    for _ in range(2):
        with pytest.raises(PortalError):
            await client.search(SkuQuery(keyword="стол"))
    assert len(calls) == 2, "без пометки каждый запрос идёт в сеть"
    assert client.down_for_s() == 0
