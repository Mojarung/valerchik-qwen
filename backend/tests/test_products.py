"""Карточки товаров портала: как они добираются от MCP-инструмента до интерфейса.

Соглашение простое: инструмент вернул ключ `products` — значит это товары, и они
поедут пользователю картинками. Здесь проверяется, что соглашение соблюдается на
всех трёх формах ответа MCP и что чужие инструменты под него не подпадают.
"""

import json
from dataclasses import dataclass

from sqlalchemy import select

from app.models import McpServer, Message
from app.services.agent import MAX_PRODUCTS, AgentOutcome, products_from_tools


@dataclass
class _Part:
    content: object
    part_kind: str = "tool-return"
    tool_name: str = "search_products"


@dataclass
class _Msg:
    parts: list


def _product(n: int) -> dict:
    return {
        "sku_id": n,
        "name": f"Ноутбук {n}",
        "url": f"https://zakupki.mos.ru/sku/view/{n}",
        "image": f"https://zakupki.mos.ru/newapi/api/Core/Thumbnail/{n}/300/300",
    }


def test_products_are_taken_from_a_plain_dict_result():
    messages = [_Msg([_Part({"total": 2, "products": [_product(1), _product(2)]})])]
    found = products_from_tools(messages)
    assert [p["sku_id"] for p in found] == [1, 2]


def test_products_are_taken_from_a_json_string_result():
    """Транспорт MCP может отдать результат строкой — это тот же ответ."""
    messages = [_Msg([_Part(json.dumps({"products": [_product(3)]}))])]
    assert products_from_tools(messages)[0]["sku_id"] == 3


def test_products_are_taken_from_mcp_content_blocks():
    """А может — списком текстовых блоков, как в протоколе."""
    blocks = [{"type": "text", "text": json.dumps({"products": [_product(4)]})}]
    assert products_from_tools([_Msg([_Part(blocks)])])[0]["sku_id"] == 4


def test_portal_image_links_are_rewritten_to_our_proxy():
    """Картинку браузер должен брать с нашего домена: до чужого между ним и нами
    стоит слой из блокировщиков и корпоративных прокси, которым мы не управляем."""
    raw = {
        "sku_id": 20,
        "name": "Ноутбук",
        "image": "https://zakupki.mos.ru/newapi/api/Core/Thumbnail/777/300/300",
        "image_full": "https://zakupki.mos.ru/newapi/api/FileStorage/Download?id=777",
        "images": ["https://zakupki.mos.ru/newapi/api/Core/Thumbnail/777/600/400"],
    }
    product = products_from_tools([_Msg([_Part({"products": [raw]})])])[0]
    assert product["image"] == "/api/catalog/image/777?w=300&h=300"
    assert product["image_full"] == "/api/catalog/image/777?original=true"
    assert product["images"] == ["/api/catalog/image/777?w=600&h=400"]
    # Ссылка на саму карточку — не картинка, её переписывать нечем и незачем
    assert products_from_tools([_Msg([_Part({"products": [_product(21)]})])])[0]["url"].startswith(
        "https://zakupki.mos.ru/sku/view/"
    )


def test_foreign_image_links_are_left_alone():
    """Прокси ходит только на портал. Чужой адрес в поле картинки не должен
    превращаться в наш — иначе ручка стала бы открытым прокси."""
    raw = {
        "sku_id": 22,
        "name": "Ноутбук",
        "image": "https://example.com/evil.png",
        "images": ["http://169.254.169.254/latest/meta-data"],
    }
    product = products_from_tools([_Msg([_Part({"products": [raw]})])])[0]
    assert product["image"] == "https://example.com/evil.png"
    assert product["images"] == ["http://169.254.169.254/latest/meta-data"]


def test_image_proxy_rejects_absent_photo(client):
    """У портала «картинки нет» — это 0 и -1, а не только null."""
    for empty in (0, -1):
        assert client.get(f"/api/catalog/image/{empty}").status_code == 404


def test_image_proxy_does_not_pass_json_errors_off_as_images(client, monkeypatch):
    """На несуществующий файл портал отвечает 200 и JSON-ом. Отдать его как
    картинку — показать пользователю сломанный квадрат вместо честного «нет фото»."""
    import httpx

    from app.routers import catalog

    async def fake_get(self, url, **kwargs):
        return httpx.Response(200, json={"message": "не найдено"}, request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)
    assert client.get("/api/catalog/image/777").status_code == 404
    assert catalog.THUMBNAIL_PATH == "/newapi/api/Core/Thumbnail"


def test_image_proxy_goes_to_configured_host(client, monkeypatch):
    """Портал отдаёт картинки только на адреса в РФ: сервер за границей берёт их
    через свой прокси. Хост — из настроек сервера, путь и id — только наши."""
    import httpx

    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "portal_image_base", "http://images.example:9201/")
    seen: list[str] = []

    async def fake_get(self, url, **kwargs):
        seen.append(url)
        return httpx.Response(
            200,
            content=b"\xff\xd8\xffjpeg",
            headers={"Content-Type": "image/jpeg"},
            request=httpx.Request("GET", url),
        )

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)
    assert client.get("/api/catalog/image/777?w=150").status_code == 200
    assert client.get("/api/catalog/image/777?original=true").status_code == 200
    assert seen == [
        "http://images.example:9201/newapi/api/Core/Thumbnail/777/150/150",
        "http://images.example:9201/newapi/api/FileStorage/Download?id=777",
    ]


def test_image_proxy_serves_bytes_with_a_week_of_cache(client, monkeypatch):
    import httpx

    async def fake_get(self, url, **kwargs):
        assert url == "https://zakupki.mos.ru/newapi/api/Core/Thumbnail/777/150/150"
        return httpx.Response(
            200,
            content=b"\xff\xd8\xff\xe0jpeg",
            headers={"Content-Type": "image/jpeg"},
            request=httpx.Request("GET", url),
        )

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)
    resp = client.get("/api/catalog/image/777?w=150")
    assert resp.status_code == 200
    assert resp.content.startswith(b"\xff\xd8\xff")
    assert resp.headers["content-type"] == "image/jpeg"
    assert "max-age=604800" in resp.headers["cache-control"]
    assert resp.headers["x-content-type-options"] == "nosniff"


def test_image_proxy_clamps_absurd_sizes(client, monkeypatch):
    """Просить у портала больше оригинала бессмысленно: он апскейлит, растёт вес."""
    import httpx

    seen: dict = {}

    async def fake_get(self, url, **kwargs):
        seen["url"] = url
        return httpx.Response(
            200, content=b"\x89PNG", headers={"Content-Type": "image/png"}, request=httpx.Request("GET", url)
        )

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)
    client.get("/api/catalog/image/777?w=99999&h=1")
    assert seen["url"].endswith("/777/2000/64")


def test_other_tools_are_not_mistaken_for_products():
    """Выдача базы знаний тоже список словарей — но это не товары."""
    kb = _Part(
        {"results": [{"article_id": "a1", "question": "Как войти"}], "threshold": 0.4}, tool_name="search_kb"
    )
    assert products_from_tools([_Msg([kb])]) == []


def test_answer_text_is_not_scanned_for_products():
    """Берём данные инструмента, а не то, что модель написала текстом."""
    text_part = _Part(json.dumps({"products": [_product(5)]}), part_kind="text")
    assert products_from_tools([_Msg([text_part])]) == []


def test_same_product_from_two_tool_calls_is_shown_once():
    messages = [
        _Msg([_Part({"products": [_product(6)]})]),
        _Msg([_Part({"products": [_product(6), _product(7)]})]),
    ]
    assert [p["sku_id"] for p in products_from_tools(messages)] == [6, 7]


def test_nameless_rows_are_dropped():
    """Без названия карточка бесполезна: показывать нечего."""
    messages = [_Msg([_Part({"products": [{"sku_id": 8, "url": "u"}, _product(9)]})])]
    assert [p["sku_id"] for p in products_from_tools(messages)] == [9]


def test_flood_of_products_is_capped():
    """Инструмент может вернуть сотню — в пузырь чата столько не нужно."""
    messages = [_Msg([_Part({"products": [_product(i) for i in range(100)]})])]
    assert len(products_from_tools(messages)) == MAX_PRODUCTS


def test_broken_result_does_not_break_the_answer():
    for junk in ("не json", None, 42, [{"type": "text"}], {"products": "не список"}):
        assert products_from_tools([_Msg([_Part(junk)])]) == []


# --- интеграция: карточки доезжают до сообщения и переживают перезагрузку -----


def test_products_land_in_message_meta(client, monkeypatch):
    """Карточки кладём в meta, а не в цитаты: цитата ведёт в статью базы знаний."""
    from app.services import chat as chat_service

    async def fake_agent(*args, **kwargs):
        return AgentOutcome(
            answer="Нашёл в каталоге портала два подходящих ноутбука.",
            answered=True,
            confidence=0.0,
            citations=[],
            escalated=False,
            line=None,
            tool_trace=[{"tool": "search_products", "source": "mcp"}],
            products=[_product(11), _product(12)],
        )

    monkeypatch.setattr(chat_service, "run_support_agent", fake_agent)
    sid = client.post("/api/chat/sessions").json()["session_id"]
    resp = client.post(f"/api/chat/sessions/{sid}/messages", json={"text": "найди ноутбук"})
    assert resp.status_code == 200

    messages = client.get(f"/api/chat/sessions/{sid}/messages").json()
    answer = [m for m in messages if m["role"] == "assistant"][-1]
    assert [p["sku_id"] for p in answer["meta"]["products"]] == [11, 12]
    assert answer["citations"] == []


def test_no_products_no_key_in_meta(client):
    """Обычный ответ по базе знаний не должен таскать пустой ключ."""
    sid = client.post("/api/chat/sessions").json()["session_id"]
    client.post(
        f"/api/chat/sessions/{sid}/messages", json={"text": "Как зарегистрироваться на Портале поставщиков?"}
    )
    messages = client.get(f"/api/chat/sessions/{sid}/messages").json()
    answer = [m for m in messages if m["role"] == "assistant"][-1]
    assert "products" not in answer["meta"]


# --- регистрация сервера каталога в реестре ---------------------------------


async def test_catalog_mcp_registers_itself_and_only_once(client, monkeypatch):
    """На уже развёрнутой базе запись тоже должна появиться — иначе после
    обновления интеграция молча не включится."""
    from app.config import get_settings
    from app.seed.seeder import ZAKUPKI_MCP_NAME, _register_zakupki_mcp

    runtime = client.app.state.rt
    monkeypatch.setattr(get_settings(), "zakupki_mcp_url", "http://zakupki-mcp:9200/mcp")
    try:
        await _register_zakupki_mcp(runtime)
        await _register_zakupki_mcp(runtime)

        async with runtime.db.session_factory() as db:
            rows = (
                (await db.execute(select(McpServer).where(McpServer.name == ZAKUPKI_MCP_NAME)))
                .scalars()
                .all()
            )
        assert len(rows) == 1
        assert rows[0].url == "http://zakupki-mcp:9200/mcp"
        assert rows[0].enabled is True
    finally:
        # чистим за собой: иначе следующие тесты модуля полезут на несуществующий адрес
        async with runtime.db.session_factory() as db:
            for row in (
                (await db.execute(select(McpServer).where(McpServer.name == ZAKUPKI_MCP_NAME)))
                .scalars()
                .all()
            ):
                await db.delete(row)
            await db.commit()


async def test_admins_choice_is_not_overwritten(client, monkeypatch):
    """Администратор мог выключить сервер или подменить адрес — рестарт не вправе
    вернуть всё как было."""
    from app.config import get_settings
    from app.seed.seeder import ZAKUPKI_MCP_NAME, _register_zakupki_mcp

    runtime = client.app.state.rt
    monkeypatch.setattr(get_settings(), "zakupki_mcp_url", "http://zakupki-mcp:9200/mcp")
    async with runtime.db.session_factory() as db:
        db.add(McpServer(name=ZAKUPKI_MCP_NAME, url="http://свой-адрес:9200/mcp", enabled=False))
        await db.commit()
    try:
        await _register_zakupki_mcp(runtime)
        async with runtime.db.session_factory() as db:
            row = (await db.execute(select(McpServer).where(McpServer.name == ZAKUPKI_MCP_NAME))).scalar_one()
        assert row.enabled is False
        assert row.url == "http://свой-адрес:9200/mcp"
    finally:
        async with runtime.db.session_factory() as db:
            for stale in (
                (await db.execute(select(McpServer).where(McpServer.name == ZAKUPKI_MCP_NAME)))
                .scalars()
                .all()
            ):
                await db.delete(stale)
            await db.commit()


def test_registration_is_skipped_without_url(client):
    """Без адреса регистрировать нечего — и это не ошибка."""
    from app.seed.seeder import ZAKUPKI_MCP_NAME

    servers = client.get("/api/admin/mcp", headers=_admin(client)).json()
    assert not any(s["name"] == ZAKUPKI_MCP_NAME for s in servers)


def _admin(client) -> dict:
    # Общий хелпер снимает куку после логина: локальная копия оставляла бы
    # модульный клиент авторизованным для всех тестов ниже.
    from tests.conftest import login

    return login(client, "admin", "admin12345")


def test_message_model_keeps_meta_json():
    """Страховка от невнимательной правки модели: meta должна оставаться JSON-полем,
    иначе карточки не переживут перезагрузку страницы."""
    assert Message.meta.type.python_type is dict
