"""«Мои обращения»: карточки прошлых диалогов посетителя.

Свой модуль, а не хвост test_api_chat: клиент общий на модуль, и лимит на
создание диалогов (20 в минуту с адреса) там исчерпывается раньше, чем эти
тесты успевают завести свои обращения.
"""

from app.security import session_owner_token


def _new_session(client) -> str:
    resp = client.post("/api/chat/sessions")
    assert resp.status_code == 200, resp.text
    return resp.json()["session_id"]


def _history(client, *sids: str) -> list[dict]:
    pairs = [{"session_id": sid, "owner": session_owner_token(sid)} for sid in sids]
    resp = client.post("/api/chat/sessions/history", json={"sessions": pairs})
    assert resp.status_code == 200
    return resp.json()


def test_session_history_skips_dialogs_without_a_question(client):
    """Диалог заводится при каждом заходе на страницу и лежит с одним приветствием.
    Обращением он становится с первой реплики человека — до неё в списке ему не место."""
    empty = _new_session(client)
    asked = _new_session(client)
    client.post(f"/api/chat/sessions/{asked}/messages", json={"text": "Что такое СТЕ?"})

    assert [c["session_id"] for c in _history(client, empty, asked)] == [asked]


def test_session_history_card_is_titled_by_the_first_question(client):
    """Обращение узнают по своему вопросу, а не по последней реплике бота."""
    sid = _new_session(client)
    client.post(f"/api/chat/sessions/{sid}/messages", json={"text": "Как зарегистрироваться на портале?"})
    client.post(f"/api/chat/sessions/{sid}/messages", json={"text": "А если я заказчик?"})

    card = _history(client, sid)[0]
    assert card["title"] == "Как зарегистрироваться на портале?"
    assert card["last_message_role"] == "assistant"
    assert card["updated_at"] >= card["created_at"]


def test_session_history_is_ordered_by_last_activity(client):
    """Свежая переписка сверху, в каком бы порядке браузер ни прислал пары."""
    older = _new_session(client)
    client.post(f"/api/chat/sessions/{older}/messages", json={"text": "Что такое СТЕ?"})
    newer = _new_session(client)
    client.post(f"/api/chat/sessions/{newer}/messages", json={"text": "Как подписать УПД?"})
    assert [c["session_id"] for c in _history(client, older, newer)] == [newer, older]

    client.post(f"/api/chat/sessions/{older}/messages", json={"text": "А как создать СТЕ?"})
    assert [c["session_id"] for c in _history(client, newer, older)] == [older, newer]


def test_session_history_names_the_addressee_like_the_chat_header(client):
    """«Кому» в списке — те же слова, что в шапке восстановленного диалога."""
    routed = _new_session(client)
    client.post(
        f"/api/chat/sessions/{routed}/messages", json={"text": "Позовите оператора, у меня не работает ЭЦП"}
    )
    plain = _new_session(client)
    client.post(f"/api/chat/sessions/{plain}/messages", json={"text": "Что такое СТЕ?"})

    cards = {c["session_id"]: c for c in _history(client, routed, plain)}
    header = client.get(f"/api/chat/sessions/{routed}").json()
    assert cards[routed]["status"] == "waiting_operator"
    assert cards[routed]["line_label"] == header["line_label"] != ""
    assert cards[routed]["addressee_label"] == header["addressee_label"]
    assert cards[plain]["line_label"] == cards[plain]["addressee_label"] == ""


def test_history_refreshes_do_not_spend_the_quota_for_new_dialogs(client):
    """Список обращений перечитывается на каждое переключение и по таймеру. Если
    он тратит ту же минутную квоту, что и создание диалога, пара десятков
    обновлений запрещает человеку завести новое обращение — и сам список
    перестаёт обновляться на 429."""
    sid = _new_session(client)
    client.post(f"/api/chat/sessions/{sid}/messages", json={"text": "Что такое СТЕ?"})
    for _ in range(25):
        client.post(
            "/api/chat/sessions/history",
            json={"sessions": [{"session_id": sid, "owner": session_owner_token(sid)}]},
        )
    assert client.post("/api/chat/sessions").status_code == 200
