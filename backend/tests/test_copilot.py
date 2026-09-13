"""Копайлот оператора: черновик ответа, деградация без LLM, границы доступа."""

from pydantic_ai import ModelResponse, ToolCallPart
from pydantic_ai.models.function import FunctionModel

from app.chat.copilot_flow import _last_client_question
from app.services import copilot
from app.services.retrieval import RetrievedDoc


class _Retriever:
    def __init__(self, answer: str) -> None:
        self.answer = answer

    async def search(self, query: str, top_k: int = 3, **kwargs) -> list[RetrievedDoc]:
        return [RetrievedDoc("a1", "Как отозвать оферту?", self.answer, "Оферты", 1, score=0.9)]


def test_combined_operator_request_keeps_the_client_question():
    history = [
        {"role": "user", "content": "Позовите оператора, как зарегистрироваться на портале?"}
    ]
    assert _last_client_question(history) == "как зарегистрироваться на портале"


def test_bare_operator_request_is_not_a_client_question():
    assert _last_client_question([{"role": "user", "content": "Позовите оператора"}]) == ""


async def test_generated_draft_is_sanitized(client, monkeypatch):
    """Черновик уходит в поле оператора, а оттуда клиенту — служебных пометок в нём быть не должно."""
    monkeypatch.setattr(copilot, "llm_configured", lambda dyn: True)
    monkeypatch.setattr(
        copilot,
        "build_model",
        lambda dyn: FunctionModel(
            lambda messages, info: ModelResponse(
                parts=[
                    ToolCallPart(
                        info.output_tools[0].name,
                        {
                            "draft": (
                                "Отзовите оферту в кабинете "
                                "【3007420c9b1e4f0aa1b2c3d4e5f60718】 (score = 0.72)."
                            ),
                            "source_ids": ["a1"],
                            "mode": "grounded",
                        },
                    )
                ]
            )
        ),
    )
    draft = await copilot.draft_reply(
        client.app.state.rt.dyn, _Retriever("Оферту можно отозвать."), [], "как отозвать оферту"
    )
    assert draft.generated is True
    assert draft.text == "Отзовите оферту в кабинете."


async def test_fallback_draft_is_sanitized(client):
    """Без модели черновик — статья целиком; разметка тикет-системы из старой статьи не доезжает."""
    draft = await copilot.draft_reply(
        client.app.state.rt.dyn,
        _Retriever("<m #ffff00><c #1a1a1a>Оферту можно отозвать</c></m> в кабинете."),
        [],
        "как отозвать оферту",
    )
    assert draft.generated is False
    assert draft.text == ""
    assert draft.status == "error"
    assert draft.sources[0]["answer"] == "Оферту можно отозвать в кабинете."


def _session_with_operator(client, operator_headers) -> str:
    """Диалог, доведённый до оператора, с вопросом из базы знаний последней репликой."""
    sid = client.post("/api/chat/sessions").json()["session_id"]
    client.post(
        f"/api/chat/sessions/{sid}/messages",
        json={"text": "Позовите оператора, как зарегистрироваться?"},
    )
    client.post(f"/api/operator/sessions/{sid}/take", headers=operator_headers)
    # Реплика уже при операторе: агент молчит, а в панель уходят подсказки
    forwarded = client.post(
        f"/api/chat/sessions/{sid}/messages", json={"text": "Как зарегистрироваться на портале?"}
    )
    assert forwarded.json()["status"] == "forwarded_to_operator"
    return sid


def test_draft_requires_staff(client):
    sid = client.post("/api/chat/sessions").json()["session_id"]
    assert client.post(f"/api/operator/sessions/{sid}/draft").status_code in (401, 403)


def test_draft_uses_question_embedded_in_handoff(client, operator_headers):
    """The initial hand-off message may contain both the request and the actual question."""
    sid = client.post("/api/chat/sessions").json()["session_id"]
    client.post(
        f"/api/chat/sessions/{sid}/messages",
        json={"text": "Позовите оператора, как зарегистрироваться на портале?"},
    )
    assert client.post(f"/api/operator/sessions/{sid}/take", headers=operator_headers).status_code == 200

    draft = client.post(f"/api/operator/sessions/{sid}/draft", headers=operator_headers)

    assert draft.status_code == 200, draft.text


def test_draft_falls_back_to_kb_article_without_llm(client, operator_headers):
    """Офлайн-режим: модели нет, но подсказка оператору обязана остаться.

    Тем же путём идут таймаут и ошибка генерации, поэтому проверка стоит именно
    здесь: в худшем случае оператор получает то, что панель показывала и до
    появления копайлота, а не пустоту и не ошибку.
    """
    sid = _session_with_operator(client, operator_headers)
    data = client.post(f"/api/operator/sessions/{sid}/draft", headers=operator_headers).json()

    assert data["generated"] is False, "скриптованная офлайн-модель черновики писать не умеет"
    assert not data["draft"], "статья не должна маскироваться под готовый ответ"
    assert data["sources"], "источники приезжают вместе с черновиком"
    assert data["status"] == "error"


def test_draft_rejected_when_dialog_not_with_operator(client, admin_headers):
    """Диалог ведёт бот — черновик оператору не нужен и стоил бы вызова модели."""
    sid = client.post("/api/chat/sessions").json()["session_id"]
    resp = client.post(f"/api/operator/sessions/{sid}/draft", headers=admin_headers)
    assert resp.status_code == 409


def test_draft_respects_kill_switch(client, admin_headers, operator_headers):
    sid = _session_with_operator(client, operator_headers)
    client.put("/api/admin/settings", headers=admin_headers, json={"updates": {"copilot.enabled": False}})
    try:
        resp = client.post(f"/api/operator/sessions/{sid}/draft", headers=operator_headers)
        assert resp.status_code == 409
    finally:
        client.put("/api/admin/settings", headers=admin_headers, json={"updates": {"copilot.enabled": True}})
