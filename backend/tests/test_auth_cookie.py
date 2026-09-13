"""Аутентификация сотрудников: httpOnly-кука вместо токена в JS, CSRF-заголовок,
лимит попыток входа, fail-fast секретов и операторский сокет без токена в URL."""

import time

import pytest
from starlette.websockets import WebSocketDisconnect

from app.security import origin_allowed

CSRF = {"X-Requested-With": "valerchik"}
# Изменяющий запрос для проверки CSRF: пустой словарь обновлений роутер отвергает
# как 422, поэтому берём известный ключ и пишем в него текущее значение.
SETTINGS_BODY = {"updates": {"rate_limit.enabled": True}}


def _login(client, user: str = "admin", pw: str = "admin12345"):
    return client.post("/api/auth/login", json={"username": user, "password": pw})


def _logout(client) -> None:
    client.post("/api/auth/logout", headers=CSRF)


def _clear_login_limit(client) -> None:
    """Снимает счётчик лимита входа, чтобы соседние тесты могли логиниться.

    Ключ строится так же, как в middleware.rate_limit_key: у TestClient адрес
    клиента всегда `testclient`. Чистим соседние окна тоже — прогон может
    перешагнуть границу минуты между запросом и уборкой.
    """
    rt = client.app.state.rt
    minute = int(time.time() // 60)
    for window in (minute - 1, minute, minute + 1):
        client.portal.call(rt.cache.delete, f"rl:login:testclient:{window}")


def test_login_sets_httponly_cookie(client):
    resp = _login(client)
    assert resp.status_code == 200
    # токен по-прежнему в теле: им живут mcp-серверы, curl и сами тесты
    assert resp.json()["token"]
    cookie = resp.headers["set-cookie"].lower()
    assert "valerchik_auth=" in cookie
    assert "httponly" in cookie
    assert "samesite=lax" in cookie
    # http в тестах: Secure выставляется только на https, иначе кука не доедет
    assert "secure" not in cookie
    _logout(client)


def test_me_via_cookie_and_logout(client):
    _login(client)
    me = client.get("/api/auth/me")
    assert me.status_code == 200
    body = me.json()
    assert body["role"] == "admin"
    assert body["display_name"]
    assert body["username"] == "admin"
    assert client.post("/api/auth/logout", headers=CSRF).status_code == 204
    # Аноним получает 200 с пустой ролью: «сессии нет» — это ответ о состоянии,
    # а не ошибка. На публичной странице чата 401 сорил бы в консоль браузера.
    after = client.get("/api/auth/me")
    assert after.status_code == 200
    assert after.json()["role"] is None


def test_cookie_mutation_requires_csrf_header(client):
    _login(client)
    denied = client.put("/api/admin/settings", json=SETTINGS_BODY)
    assert denied.status_code == 403
    assert denied.json()["detail"] == "Запрос без подтверждения происхождения"
    assert client.put("/api/admin/settings", json=SETTINGS_BODY, headers=CSRF).status_code == 200
    _logout(client)


def test_bearer_does_not_need_csrf_header(client, admin_headers):
    assert client.put("/api/admin/settings", json=SETTINGS_BODY, headers=admin_headers).status_code == 200


def test_token_in_query_is_rejected(client, admin_headers):
    token = admin_headers["Authorization"].split()[1]
    assert client.get(f"/api/admin/stats?token={token}").status_code == 401


def test_login_rate_limited(client, admin_headers):
    """Перебор пароля упирается в лимит на IP, а не в бесконечность попыток."""
    key = "rate_limit.login_per_minute"
    client.put("/api/admin/settings", headers=admin_headers, json={"updates": {key: 3}})
    _clear_login_limit(client)
    try:
        codes = [_login(client, pw="wrong").status_code for _ in range(4)]
        assert codes[0] == 401, codes
        assert codes[-1] == 429, codes
    finally:
        client.put("/api/admin/settings", headers=admin_headers, json={"updates": {key: 10}})
        _clear_login_limit(client)


def test_operator_ws_uses_cookie(client):
    _login(client, "op_maria", "operator12345")
    with client.websocket_connect("/ws/operator", headers={"origin": "http://testserver"}):
        pass
    _logout(client)


def test_operator_ws_rejects_foreign_origin(client):
    _login(client, "op_maria", "operator12345")
    try:
        with pytest.raises(WebSocketDisconnect) as exc:
            with client.websocket_connect("/ws/operator", headers={"origin": "https://evil.tld"}):
                pass
        assert exc.value.code == 4403
    finally:
        _logout(client)


def test_operator_ws_accepts_own_origin_on_nonstandard_port(client):
    """Стенд на http://localhost:8090: nginx передаёт Host с портом, и свой Origin
    проходит. Раньше Host приходил без порта, сокет закрывался с 4403 и лента
    оператора не получала ни одного живого события."""
    _login(client, "op_maria", "operator12345")
    try:
        own = {"origin": "http://localhost:8090", "host": "localhost:8090"}
        with client.websocket_connect("/ws/operator", headers=own):
            pass
        for foreign in ("http://localhost:9999", "http://localhost"):
            with pytest.raises(WebSocketDisconnect) as exc:
                with client.websocket_connect("/ws/operator", headers={**own, "origin": foreign}):
                    pass
            assert exc.value.code == 4403, foreign
    finally:
        _logout(client)


@pytest.mark.parametrize(
    ("origin", "host", "allowed"),
    [
        (None, "localhost:8090", True),
        ("http://localhost:8090", "localhost:8090", True),
        ("http://localhost:8090/", "localhost:8090", True),
        ("https://valerchik.example", "valerchik.example", True),
        ("http://localhost:8090", "localhost", False),
        ("http://localhost:9999", "localhost:8090", False),
        ("https://evil.tld", "localhost:8090", False),
        ("http://localhost:8090", "", False),
    ],
)
def test_origin_allowed_compares_host_with_port(origin, host, allowed):
    assert origin_allowed(origin, host, []) is allowed


def test_full_mode_refuses_default_secret(tmp_path):
    from app.config import Settings

    for placeholder in ("", "change-me-in-env", "dev-secret-not-for-prod", "коротко"):
        with pytest.raises(RuntimeError):
            Settings(app_mode="full", app_secret=placeholder, data_dir=tmp_path).resolve_secret()


def test_full_mode_accepts_long_secret(tmp_path):
    from app.config import Settings

    secret = "x" * 32
    assert Settings(app_mode="full", app_secret=secret, data_dir=tmp_path).resolve_secret() == secret


def test_lite_mode_persists_generated_secret(tmp_path):
    from app.config import Settings

    first = Settings(app_mode="lite", app_secret="", data_dir=tmp_path).resolve_secret()
    assert len(first) == 64
    # секрет переживает перезапуск: иначе все куки протухали бы при каждом старте
    assert Settings(app_mode="lite", app_secret="", data_dir=tmp_path).resolve_secret() == first
    assert (tmp_path / ".app-secret").read_text(encoding="utf-8").strip() == first


def test_seeder_refuses_empty_password_in_full(tmp_path):
    """В full пустой ADMIN_PASSWORD — это остановка старта, а не демо-пароль."""
    import pytest as _pytest

    from app.config import Settings
    from app.seed.seeder import staff_passwords

    lite = staff_passwords(Settings(app_mode="lite", admin_password="", operator_password=""))
    assert lite == ("admin12345", "operator12345")

    with _pytest.raises(RuntimeError):
        staff_passwords(Settings(app_mode="full", admin_password="", operator_password="x" * 12))
