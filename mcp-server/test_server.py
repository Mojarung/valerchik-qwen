"""Инструменты MCP-сервера поддержки: то, что видит внешний агент.

Раньше этот файл не проверялся ничем: опечатка в имени поля или сломанный
импорт доезжали до пользователя. Отдельно закреплено поведение токена
оператора — он кэшируется на процесс, и без обновления по 401 все
staff-инструменты отказывали до перезапуска сервера.
"""

import httpx
import pytest
import server


@pytest.fixture(autouse=True)
def _isolate():
    """Свой транспорт и чистый кэш токена на каждый тест."""
    original = server._client
    server._staff_token = None
    yield
    server._client = original
    server._staff_token = None


def _mock(handler) -> None:
    server._client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://valerchik.test"
    )


@pytest.mark.asyncio
async def test_ask_support_returns_answer_and_sources():
    """Ответ агента приезжает вместе с источниками и уверенностью."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/chat/sessions":
            return httpx.Response(200, json={"session_id": "s1", "greeting": "", "chips": [], "owner": "o"})
        return httpx.Response(
            200,
            json={
                "status": "answered",
                "answer": "Нужна электронная подпись.",
                "confidence": 0.91,
                "citations": [{"article_id": "a1", "question": "Как зарегистрироваться?", "score": 0.9}],
                "escalated": False,
            },
        )

    _mock(handler)
    result = await server.ask_support("Как зарегистрироваться?")
    assert result["answer"] == "Нужна электронная подпись."
    assert result["sources"][0]["article_id"] == "a1"
    assert result["session_id"] == "s1"


@pytest.mark.asyncio
async def test_staff_token_is_refreshed_after_expiry(monkeypatch):
    """Протухший токен не должен выключать инструменты до перезапуска процесса."""
    monkeypatch.setattr(server, "STAFF_LOGIN", "op_maria")
    monkeypatch.setattr(server, "STAFF_PASSWORD", "operator12345")
    calls = {"login": 0, "search": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/auth/login":
            calls["login"] += 1
            return httpx.Response(200, json={"token": f"token-{calls['login']}"})
        calls["search"] += 1
        if request.headers.get("authorization") == "Bearer stale":
            return httpx.Response(401, json={"detail": "Недействительный токен"})
        return httpx.Response(200, json=[{"article_id": "a1", "question": "Как войти?"}])

    _mock(handler)
    server._staff_token = "stale"
    found = await server.search_knowledge_base("вход")
    assert found[0]["article_id"] == "a1"
    assert calls["login"] == 1, "после 401 сервер обязан взять новый токен"
    assert calls["search"] == 2, "запрос повторяется ровно один раз"


@pytest.mark.asyncio
async def test_staff_tools_refuse_without_credentials(monkeypatch):
    """Без учётки сотрудника — понятное объяснение, а не отказ сервера."""
    monkeypatch.setattr(server, "STAFF_LOGIN", "")

    _mock(lambda request: httpx.Response(200, json=[]))
    with pytest.raises(RuntimeError, match="VALERCHIK_STAFF_LOGIN"):
        await server.search_knowledge_base("вход")


@pytest.mark.asyncio
async def test_health_is_passed_through():
    _mock(lambda request: httpx.Response(200, json={"status": "ok", "mode": "lite"}))
    assert (await server.support_health())["status"] == "ok"
