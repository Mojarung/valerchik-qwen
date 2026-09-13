"""Фикстуры: приложение в lite-режиме (SQLite + fakeredis + hash-эмбеддер + mock-LLM),
полный жизненный цикл с сидированием — как на реальном демо, но без внешних сервисов."""

import os
import re

import pytest
from starlette.testclient import TestClient

_SESSION_IN_URL = re.compile(r"/(?:api/chat|ws/chat)/(?:sessions/)?([a-f0-9]{32})")


class OwnerAwareClient(TestClient):
    """Клиент, который ведёт себя как настоящий фронт.

    Запись в диалог требует токена владельца: без него посторонний, узнавший
    id, писал бы реплики от имени заявителя. Фронт хранит токен в куке и шлёт
    заголовком — здесь делаем то же самое, чтобы тесты проверяли сценарий, а не
    возились с подписью. Отказ без токена проверяется отдельными тестами.
    """

    def _owner_of(self, url) -> str | None:
        from app.security import session_owner_token

        found = _SESSION_IN_URL.search(str(url))
        return session_owner_token(found.group(1)) if found else None

    def request(self, method, url, **kwargs):
        owner = self._owner_of(url)
        if owner:
            headers = dict(kwargs.get("headers") or {})
            headers.setdefault("X-Session-Owner", owner)
            kwargs["headers"] = headers
        return super().request(method, url, **kwargs)

    def websocket_connect(self, url, *args, **kwargs):
        # Владение сокетом доказывает подпротокол `owner.<токен>`, а не адрес:
        # токен в адресе оседал в логах прокси. Явно переданные subprotocols не
        # трогаем — так тесты проверяют отказ без токена.
        from app.security import CHAT_WS_PROTOCOL

        owner = self._owner_of(url)
        if owner and "subprotocols" not in kwargs:
            kwargs["subprotocols"] = [CHAT_WS_PROTOCOL, f"owner.{owner}"]
        return super().websocket_connect(url, *args, **kwargs)


_TEST_ENV = {
    "APP_MODE": "lite",
    "DATABASE_URL": "",  # выставляется per-module на tmp-файл
    "REDIS_URL": "",
    "QDRANT_URL": "",
    "TEI_URL": "",
    "STT_URL": "",
    # Гасим LLM явно, а не «по умолчанию пусто»: markitdown при импорте вызывает
    # load_dotenv(), тот поднимается вверх по дереву до .env проекта и вносит боевые
    # LLM_* прямо в окружение процесса. Без этих трёх строк тесты после первого
    # разбора документа начинают ходить в настоящий платный API и становятся
    # недетерминированными.
    "LLM_BASE_URL": "",
    "LLM_API_KEY": "",
    "LLM_MODEL": "",
    # По той же причине гасим и адреса соседних сервисов. Достаточно поднять стек
    # с профилем s3 — и в .env появится S3_ENDPOINT=http://minio:9000, который
    # markitdown внесёт в окружение тестов. Хранилище картинок молча переключится
    # с тома данных на боку S3, и половина тестов про вложения посыплется
    # NameResolutionError: имя minio с хоста не разрешается.
    "S3_ENDPOINT": "",
    "S3_ACCESS_KEY": "",
    "S3_SECRET_KEY": "",
    "DOCPARSE_URL": "",
    "TTS_URL": "",
    "TEI_RERANK_URL": "",
    # Внешние MCP тесты поднимают сами; адрес из .env увёл бы агента в сеть.
    "ZAKUPKI_MCP_URL": "",
    "SEED_ON_STARTUP": "true",
    "ADMIN_PASSWORD": "admin12345",
    "OPERATOR_PASSWORD": "operator12345",
    # Секрет длиной от 32 символов: в full короткий останавливает старт, а jwt
    # ругается предупреждением на любой ключ короче ключа HMAC-SHA256.
    "APP_SECRET": "test-secret-not-for-production-0123456789abcdef",
    # Боевые 600 000 итераций PBKDF2 стоят ~0.4 с на хеш, а сид хеширует пароли
    # каждого модуля тестов: итерации — настройка, и здесь она маленькая.
    # Формат хеша и перехеширование от этого не меняются.
    "PASSWORD_HASH_ITERATIONS": "1000",
}


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("app")
    env = dict(_TEST_ENV)
    env["DATA_DIR"] = str(tmp / "artifacts")
    env["DATABASE_URL"] = f"sqlite+aiosqlite:///{tmp / 'test.db'}"
    previous = {k: os.environ.get(k) for k in env}
    os.environ.update(env)

    from app.config import get_settings

    get_settings.cache_clear()
    from app.main import create_app

    app = create_app()
    with OwnerAwareClient(app) as test_client:
        yield test_client

    for key, value in previous.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value
    get_settings.cache_clear()


def login(client: TestClient, username: str, password: str) -> dict:
    """Заголовки Bearer. Куку логин теперь ставит тоже — и её здесь снимаем.

    Клиент общий на модуль, так что кука пережила бы этот вызов и молча
    авторизовала бы запросы, которыми соседние тесты проверяют отказ анониму.
    Сценарий с кукой проверяется отдельно, в test_auth_cookie.
    """
    resp = client.post("/api/auth/login", json={"username": username, "password": password})
    assert resp.status_code == 200, resp.text
    data = resp.json()
    from app.security import AUTH_COOKIE

    client.cookies.delete(AUTH_COOKIE)
    return {"Authorization": f"Bearer {data['token']}"}


@pytest.fixture(scope="module")
def admin_headers(client) -> dict:
    return login(client, "admin", "admin12345")


@pytest.fixture(scope="module")
def operator_headers(client) -> dict:
    return login(client, "op_maria", "operator12345")


@pytest.fixture(autouse=True)
def _forget_auth_cookie(request):
    """После каждого теста снимаем куку сотрудника с общего клиента.

    Клиент один на модуль: тест, упавший между логином кукой и выходом, иначе
    оставил бы соседей авторизованными, и проверки «аноним получает 401» молча
    перестали бы что-либо доказывать.
    """
    yield
    client = (getattr(request.node, "funcargs", None) or {}).get("client")
    if client is not None:
        from app.security import AUTH_COOKIE

        client.cookies.delete(AUTH_COOKIE)
