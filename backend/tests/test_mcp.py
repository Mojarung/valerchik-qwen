"""Реестр MCP-серверов: CRUD из админки, тумблер на лету, изоляция сбоев."""

import pytest


@pytest.fixture(autouse=True)
def allow_internal(client, admin_headers):
    """Тесты заводят серверы на 127.0.0.1, а такие адреса по умолчанию запрещены:
    сервер ходит по ним сам, и указать соседний контейнер — это SSRF. В тестах
    тумблер включаем, отдельным тестом ниже проверяем сам запрет."""
    client.put(
        "/api/admin/settings", headers=admin_headers, json={"updates": {"security.allow_internal_urls": True}}
    )
    yield
    client.put(
        "/api/admin/settings",
        headers=admin_headers,
        json={"updates": {"security.allow_internal_urls": False}},
    )


def test_internal_url_rejected_by_default(client, admin_headers):
    """SSRF: адрес внутрь сети админ может задать только осознанно."""
    client.put(
        "/api/admin/settings",
        headers=admin_headers,
        json={"updates": {"security.allow_internal_urls": False}},
    )
    resp = client.post(
        "/api/admin/mcp",
        headers=admin_headers,
        json={"name": "ssrf-probe", "url": "http://169.254.169.254/latest/meta-data"},
    )
    assert resp.status_code == 422
    assert "внутренн" in resp.json()["detail"]


def _create(client, admin_headers, **overrides) -> dict:
    payload = {
        "name": "test-mcp",
        # адрес заведомо мёртвый: проверяем, что недоступность фиксируется, а не роняет
        "url": "http://127.0.0.1:59999/mcp",
        "description": "тестовый сервер",
        "enabled": False,
        "timeout_s": 2.0,
    }
    payload.update(overrides)
    return client.post("/api/admin/mcp", json=payload, headers=admin_headers).json()


def test_crud_and_toggle(client, admin_headers):
    created = _create(client, admin_headers, name="crud-mcp")
    assert created["name"] == "crud-mcp"
    assert created["enabled"] is False

    listed = client.get("/api/admin/mcp", headers=admin_headers).json()
    assert any(s["name"] == "crud-mcp" for s in listed)

    # тумблер шлёт только enabled — частичное обновление не должно требовать всего объекта
    toggled = client.patch(
        f"/api/admin/mcp/{created['id']}", json={"enabled": True}, headers=admin_headers
    ).json()
    assert toggled["enabled"] is True
    assert toggled["url"] == created["url"]

    resp = client.delete(f"/api/admin/mcp/{created['id']}", headers=admin_headers)
    assert resp.status_code == 200
    listed = client.get("/api/admin/mcp", headers=admin_headers).json()
    assert not any(s["name"] == "crud-mcp" for s in listed)


def test_duplicate_name_rejected(client, admin_headers):
    _create(client, admin_headers, name="dup-mcp")
    resp = client.post(
        "/api/admin/mcp", json={"name": "dup-mcp", "url": "http://127.0.0.1:59999/mcp"}, headers=admin_headers
    )
    assert resp.status_code == 409


def test_unreachable_server_is_reported_not_raised(client, admin_headers):
    """Мёртвый сервер фиксируется статусом error — создание при этом проходит."""
    created = _create(client, admin_headers, name="dead-mcp")
    assert created["last_status"] == "error"
    assert created["last_error"]
    assert created["tools"] == []

    checked = client.post(f"/api/admin/mcp/{created['id']}/check", headers=admin_headers).json()
    assert checked["status"] == "error"


def test_enabled_broken_server_does_not_break_chat(client, admin_headers):
    """Главная гарантия: включённый, но нерабочий MCP не мешает отвечать по базе знаний."""
    _create(client, admin_headers, name="broken-but-enabled", enabled=True)

    session = client.post("/api/chat/sessions").json()
    # Роль задана: без неё бот сначала честно спросил бы «поставщик или заказчик?»
    resp = client.post(
        f"/api/chat/sessions/{session['session_id']}/messages",
        json={"text": "Как зарегистрироваться на портале?", "audience": "supplier"},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] in ("answered", "escalated", "no_answer")


def test_requires_admin(client):
    assert client.get("/api/admin/mcp").status_code in (401, 403)
    assert client.post("/api/admin/mcp", json={"name": "x", "url": "http://127.0.0.1:1/mcp"}).status_code in (
        401,
        403,
    )


def test_external_tools_only_when_kb_cannot_answer(client, admin_headers):
    """MCP поднимается не на каждый вопрос: подключение стоит секунды."""
    import asyncio

    rt = client.app.state.rt

    # вопрос из базы знаний — внешние инструменты не нужны
    assert asyncio.run(rt.chat._need_external_tools("Как зарегистрироваться на портале?")) is False
    # вопрос мимо базы знаний — нужны
    assert asyncio.run(rt.chat._need_external_tools("расскажи про библиотеку pytorch")) is True
    # явная просьба перевешивает даже удачный поиск по базе знаний
    assert (
        asyncio.run(rt.chat._need_external_tools("Как зарегистрироваться на портале, посмотри в интернете"))
        is True
    )


def test_mcp_mode_switches_behaviour(client, admin_headers):
    import asyncio

    rt = client.app.state.rt
    client.put("/api/admin/settings", json={"updates": {"mcp.mode": "off"}}, headers=admin_headers)
    assert asyncio.run(rt.chat._need_external_tools("расскажи про pytorch")) is False

    client.put("/api/admin/settings", json={"updates": {"mcp.mode": "always"}}, headers=admin_headers)
    assert asyncio.run(rt.chat._need_external_tools("Как зарегистрироваться на портале?")) is True

    client.put("/api/admin/settings", json={"updates": {"mcp.mode": "auto"}}, headers=admin_headers)


def test_product_request_reaches_the_catalog_even_when_kb_answers(client, admin_headers):
    """Товарное намерение опознаётся по вопросу, а не по молчанию базы знаний.

    Допущение «про товары база молчит, и режим auto поднимет MCP сам» верно
    только на маленькой базе. После импорта методичек поиск возвращает
    что-нибудь около 0.5 на любой вопрос — «найди машины до 1.5м» ложится на
    статью про закупки малого объёма, — порог не пробивается никогда, и каталог
    не подключается ни разу.
    """
    import asyncio

    rt = client.app.state.rt
    for text in (
        "найди машины до 1.5м",
        "покажи ноутбуки",
        "хочу купить стулья",
        "сколько стоит картридж hp",
    ):
        assert asyncio.run(rt.chat._need_external_tools(text)) is True, text
    # А вопрос о правилах работы портала внешних инструментов по-прежнему не требует
    assert asyncio.run(rt.chat._need_external_tools("Как зарегистрироваться на портале?")) is False


def test_broken_product_intent_regex_does_not_break_the_answer(client, admin_headers):
    """Выражение правится из админки — битое не должно ронять ответ пользователю."""
    import asyncio

    rt = client.app.state.rt
    client.put(
        "/api/admin/settings",
        headers=admin_headers,
        json={"updates": {"mcp.product_intent": "(незакрытая скобка"}},
    )
    try:
        # Признак просто не срабатывает, решение принимается по остальным правилам:
        # вопрос, на который база знаний отвечает, внешних инструментов не требует.
        assert asyncio.run(rt.chat._need_external_tools("Как зарегистрироваться на портале?")) is False
    finally:
        client.put(
            "/api/admin/settings",
            headers=admin_headers,
            json={"updates": {"mcp.product_intent": r"(найд|подбер|покаж|куп(и|л|я))"}},
        )
