"""Оператор читает переписку и получает подсказки по обращению, которое открыл.

Историю оператор брал из гостевого эндпоинта. Когда чтение диалога по id
закрыли токеном владельца, колонка чата у оператора опустела: токена у
сотрудника нет и быть не должно. Здесь проверяется отдельный вход для
сотрудника — и то, что он не открывает переписку посторонним.
"""


def _dialog(client, *texts: str) -> str:
    sid = client.post("/api/chat/sessions").json()["session_id"]
    for text in texts:
        resp = client.post(f"/api/chat/sessions/{sid}/messages", json={"text": text})
        assert resp.status_code == 200, resp.text
    return sid


def test_staff_reads_history_without_owner_token(client, admin_headers):
    operator_headers = admin_headers
    sid = _dialog(client, "Как зарегистрироваться на портале?")

    resp = client.get(f"/api/operator/sessions/{sid}/messages", headers=operator_headers)

    assert resp.status_code == 200, resp.text
    history = resp.json()
    assert any(m["role"] == "user" and m["content"] == "Как зарегистрироваться на портале?" for m in history)
    assert any(m["role"] == "assistant" for m in history)


def test_history_is_closed_to_guests(client):
    sid = _dialog(client)

    assert client.get(f"/api/operator/sessions/{sid}/messages").status_code == 401


def test_history_of_unknown_dialog_is_404(client, operator_headers):
    resp = client.get(f"/api/operator/sessions/{'0' * 32}/messages", headers=operator_headers)

    assert resp.status_code == 404


def test_suggestions_follow_last_client_question(client, admin_headers):
    operator_headers = admin_headers
    sid = _dialog(client, "Как зарегистрироваться на портале?")

    resp = client.get(f"/api/operator/sessions/{sid}/suggestions", headers=operator_headers)

    assert resp.status_code == 200, resp.text
    cards = resp.json()
    assert cards, "по вопросу из базы знаний подсказки должны найтись"
    assert {"article_id", "question", "answer", "score", "category"} <= set(cards[0])


def test_no_suggestions_before_client_asked(client, admin_headers):
    operator_headers = admin_headers
    sid = _dialog(client)  # в диалоге только приветствие бота

    resp = client.get(f"/api/operator/sessions/{sid}/suggestions", headers=operator_headers)

    assert resp.status_code == 200
    assert resp.json() == []


def test_suggestions_are_closed_to_guests(client):
    sid = _dialog(client)

    assert client.get(f"/api/operator/sessions/{sid}/suggestions").status_code == 401


def test_suggestions_answer_the_question_not_the_request_for_human(client, admin_headers):
    operator_headers = admin_headers
    """Последняя реплика перед эскалацией — обычно «позовите оператора»: искать по
    ней в базе знаний нечего, подсказки обязаны относиться к вопросу до неё."""
    question = "Как подписать контракт электронной подписью?"
    escalated = _dialog(client, question, "Хочу поговорить с живым оператором")
    asked_only = _dialog(client, question)

    def cards(sid: str) -> list[str]:
        resp = client.get(f"/api/operator/sessions/{sid}/suggestions", headers=operator_headers)
        assert resp.status_code == 200, resp.text
        return [card["article_id"] for card in resp.json()]

    assert cards(escalated) == cards(asked_only)
