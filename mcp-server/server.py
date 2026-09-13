"""MCP-сервер «Валерчик»: даёт любому ИИ-агенту (Claude Desktop/Code, кастомные боты)
инструменты службы поддержки Портала поставщиков.

Запуск:  uv run server.py          (stdio-транспорт)
Env:     VALERCHIK_URL      — адрес сервиса (по умолчанию http://localhost:8090)
         VALERCHIK_STAFF_LOGIN / VALERCHIK_STAFF_PASSWORD — учётка оператора для staff-тулов
"""

import os
from http import HTTPStatus
from typing import Any

import httpx
from mcp.server import MCPServer

BASE_URL = os.environ.get("VALERCHIK_URL", "http://localhost:8090").rstrip("/")
STAFF_LOGIN = os.environ.get("VALERCHIK_STAFF_LOGIN", "")
STAFF_PASSWORD = os.environ.get("VALERCHIK_STAFF_PASSWORD", "")

mcp = MCPServer("valerchik-support")

_client = httpx.AsyncClient(base_url=BASE_URL, timeout=180.0)
_staff_token: str | None = None


async def _staff_headers(*, refresh: bool = False) -> dict[str, str]:
    """Заголовок авторизации сотрудника. `refresh=True` — взять новый токен.

    Токен живёт двенадцать часов и кэшируется на процесс. Без обновления по 401
    сервер, проработавший дольше суток, отвечал отказом на все staff-инструменты
    до перезапуска — снаружи это выглядело как «интеграция сломалась».
    """
    global _staff_token
    if not STAFF_LOGIN:
        raise RuntimeError(
            "Staff-тулы требуют env VALERCHIK_STAFF_LOGIN / VALERCHIK_STAFF_PASSWORD (учётка оператора)"
        )
    if refresh:
        _staff_token = None
    if _staff_token is None:
        resp = await _client.post(
            "/api/auth/login", json={"username": STAFF_LOGIN, "password": STAFF_PASSWORD}
        )
        resp.raise_for_status()
        _staff_token = resp.json()["token"]
    return {"Authorization": f"Bearer {_staff_token}"}


async def _staff_get(path: str, **kwargs: Any) -> httpx.Response:
    """GET под учёткой сотрудника с одной повторной попыткой после 401."""
    resp = await _client.get(path, headers=await _staff_headers(), **kwargs)
    if resp.status_code == HTTPStatus.UNAUTHORIZED:
        resp = await _client.get(path, headers=await _staff_headers(refresh=True), **kwargs)
    resp.raise_for_status()
    return resp


@mcp.tool()
async def ask_support(question: str) -> dict[str, Any]:
    """Задать вопрос ИИ-агенту поддержки Портала поставщиков (zakupki.mos.ru).

    Создаёт новый диалог и возвращает ответ с источниками из базы знаний,
    уверенностью (0..1) и session_id для продолжения через continue_dialog.
    """
    session = (await _client.post("/api/chat/sessions")).json()
    resp = await _client.post(f"/api/chat/sessions/{session['session_id']}/messages", json={"text": question})
    resp.raise_for_status()
    data = resp.json()
    return {
        "session_id": session["session_id"],
        "status": data["status"],
        "answer": data["answer"],
        "confidence": data.get("confidence"),
        "sources": [
            {"article_id": c["article_id"], "question": c["question"], "score": c["score"]}
            for c in data.get("citations", [])
        ],
        "escalated_to_operator": data.get("escalated", False),
    }


@mcp.tool()
async def continue_dialog(session_id: str, message: str) -> dict[str, Any]:
    """Продолжить существующий диалог поддержки (session_id из ask_support)."""
    resp = await _client.post(f"/api/chat/sessions/{session_id}/messages", json={"text": message})
    resp.raise_for_status()
    return resp.json()


@mcp.tool()
async def get_source_article(article_id: str) -> dict[str, Any]:
    """Получить полный текст статьи базы знаний по id (из sources в ask_support)."""
    resp = await _client.get(f"/api/chat/articles/{article_id}")
    resp.raise_for_status()
    return resp.json()


@mcp.tool()
async def suggest_questions(prefix: str) -> list[str]:
    """Автодополнение: популярные вопросы базы знаний по началу фразы."""
    resp = await _client.get("/api/chat/suggest", params={"q": prefix})
    resp.raise_for_status()
    return resp.json()


@mcp.tool()
async def rate_dialog(session_id: str, rating: int, comment: str = "") -> dict[str, Any]:
    """Оценить диалог поддержки от 1 до 5 (с необязательным комментарием)."""
    resp = await _client.post(
        f"/api/chat/sessions/{session_id}/feedback", json={"rating": rating, "comment": comment}
    )
    resp.raise_for_status()
    return resp.json()


@mcp.tool()
async def search_knowledge_base(query: str) -> list[dict[str, Any]]:
    """[staff] Прямой гибридный поиск по базе знаний (нужна учётка оператора в env)."""
    resp = await _staff_get("/api/operator/kb-search", params={"q": query})
    return resp.json()


@mcp.tool()
async def support_health() -> dict[str, Any]:
    """Статус сервиса поддержки: БД, кэш, размер векторного индекса, режим."""
    resp = await _client.get("/api/health")
    resp.raise_for_status()
    return resp.json()


if __name__ == "__main__":
    mcp.run()  # stdio
