"""E2E чата через API: ответ из БЗ, честный отказ + пробел БЗ, эскалация, мат, оценка."""


def _new_session(client) -> str:
    resp = client.post("/api/chat/sessions")
    assert resp.status_code == 200
    data = resp.json()
    assert data["chips"], "должны быть чипсы-подсказки"
    return data["session_id"]


def test_known_question_answered_with_citations(client):
    sid = _new_session(client)
    # Роль задана явно: без неё на «как зарегистрироваться» спорят статьи для
    # поставщика и для заказчика, и бот честно переспрашивает (см. test_audience)
    resp = client.post(
        f"/api/chat/sessions/{sid}/messages",
        json={"text": "Как зарегистрироваться на Портале поставщиков?", "audience": "supplier"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "answered"
    assert data["citations"], "ответ должен ссылаться на статьи БЗ"
    assert data["confidence"] >= 0.4
    assert "ЕСИА" in data["answer"] or "Регистрация" in data["answer"]


def test_unknown_question_refused_and_gap_recorded(client, admin_headers):
    sid = _new_session(client)
    question = "Как заказать пиццу с доставкой в переговорку через портал?"
    resp = client.post(f"/api/chat/sessions/{sid}/messages", json={"text": question})
    data = resp.json()
    assert data["status"] == "no_answer"
    assert "нет ответа" in data["answer"] or "оператора" in data["answer"]

    gaps = client.get("/api/admin/gaps", headers=admin_headers).json()
    assert any("пиццу" in g["query"] for g in gaps), "вопрос без ответа должен стать пробелом БЗ"


def test_operator_request_escalates_with_line(client, admin_headers):
    operator_headers = admin_headers
    sid = _new_session(client)
    resp = client.post(
        f"/api/chat/sessions/{sid}/messages",
        json={"text": "Позовите живого оператора, у меня не работает ЭЦП"},
    )
    data = resp.json()
    assert data["escalated"] is True

    queue = client.get("/api/operator/sessions?status=queue", headers=operator_headers).json()
    ticket = next(t for t in queue if t["session_id"] == sid)
    assert ticket["line"] in (1, 2, 3)
    # Превью последней реплики собирается одним запросом на всю выдачу
    # (оконная функция вместо SELECT на каждую сессию) — проверяем, что оно есть
    # и что это действительно последнее сообщение диалога, а не первое.
    history = client.get(f"/api/chat/sessions/{sid}/messages").json()
    assert ticket["last_message"] == history[-1]["content"][:140]
    assert ticket["last_role"] == history[-1]["role"]

    # оператор берёт диалог, отвечает, закрывает
    assert client.post(f"/api/operator/sessions/{sid}/take", headers=operator_headers).status_code == 200
    assert (
        client.post(
            f"/api/operator/sessions/{sid}/message",
            json={"text": "Проверьте плагин КриптоПро."},
            headers=operator_headers,
        ).status_code
        == 200
    )
    assert client.post(f"/api/operator/sessions/{sid}/close", headers=operator_headers).status_code == 200
    assert client.get(f"/api/chat/sessions/{sid}").json()["status"] == "waiting_user"
    # Только пользователь подтверждает решение.
    assert client.post(f"/api/chat/sessions/{sid}/solution", json={"solved": True}).status_code == 200
    assert client.post(f"/api/chat/sessions/{sid}/solution", json={"solved": True}).status_code == 409

    messages = client.get(f"/api/chat/sessions/{sid}/messages").json()
    roles = [m["role"] for m in messages]
    assert "operator" in roles and "system" in roles
    asks = [m for m in messages if (m.get("meta") or {}).get("kind") == "feedback_request"]
    assert len(asks) == 1


def test_profanity_closes_session(client, monkeypatch):
    """Требование кейса: при мате — автоматическое уведомление о завершении обращения
    и информирование пользователя о нарушении правил общения."""
    ws = client.app.state.rt.ws
    events: list[dict] = []
    publish = ws.publish

    async def recording(room: str, event: dict) -> None:
        events.append(event)
        await publish(room, event)

    monkeypatch.setattr(ws, "publish", recording)

    sid = _new_session(client)
    resp = client.post(
        f"/api/chat/sessions/{sid}/messages", json={"text": "да пошло оно всё, хуйня ваш портал"}
    )
    assert resp.json()["status"] == "closed_violation"

    # пользователь проинформирован о нарушении правил общения — сообщением в диалоге
    history = client.get(f"/api/chat/sessions/{sid}/messages").json()
    notice = [m for m in history if m["role"] == "system" and m["meta"].get("kind") == "violation"]
    assert notice and "нарушает правила общения" in notice[0]["content"]
    # и автоматически уведомлён о завершении: интерфейс закрывает диалог по событию
    assert {"type": "session_closed", "reason": "violation"} in events
    # операторы тоже видят нарушение в очереди
    assert {"type": "session_violation", "session_id": sid} in events
    assert client.get(f"/api/chat/sessions/{sid}").json()["status"] == "closed_violation"

    # диалог завершён — новые сообщения не принимаются
    resp = client.post(f"/api/chat/sessions/{sid}/messages", json={"text": "алло"})
    assert resp.status_code == 409


def test_feedback_saved(client):
    sid = _new_session(client)
    client.post(f"/api/chat/sessions/{sid}/messages", json={"text": "Что такое СТЕ?"})
    resp = client.post(
        f"/api/chat/sessions/{sid}/feedback", json={"rating": 5, "comment": "Спасибо, быстро и понятно!"}
    )
    assert resp.status_code == 200


def test_source_article_viewable(client):
    """Цитата из ответа открывается: публичная ручка отдаёт активную статью-источник."""
    sid = _new_session(client)
    answer = client.post(
        f"/api/chat/sessions/{sid}/messages",
        json={"text": "Какая электронная подпись нужна для работы на портале?"},
    ).json()
    assert answer["citations"]
    article_id = answer["citations"][0]["article_id"]

    resp = client.get(f"/api/chat/articles/{article_id}")
    assert resp.status_code == 200
    article = resp.json()
    assert article["question"] and article["answer"] and article["category"]

    assert client.get("/api/chat/articles/nonexistent000000000000000000000").status_code == 404


def test_suggest_autocomplete(client):
    resp = client.get("/api/chat/suggest", params={"q": "как зарег"})
    assert resp.status_code == 200
    items = resp.json()
    assert items and any("зарегистрироваться" in s.lower() for s in items)


def test_dialog_history_available(client):
    sid = _new_session(client)
    client.post(f"/api/chat/sessions/{sid}/messages", json={"text": "Что такое котировочная сессия?"})
    messages = client.get(f"/api/chat/sessions/{sid}/messages").json()
    assert [m["role"] for m in messages][:3] == ["assistant", "user", "assistant"]
    tool_trace = messages[-1]["meta"].get("tool_trace")
    assert tool_trace and tool_trace[0]["tool"] == "search_kb", "ReAct-цикл должен фиксироваться"


def test_forgotten_layout_is_fixed_before_search(client):
    """«Rfr pfhtubcnhbhjdfnmcz» доходит до базы знаний уже как «Как зарегистрироваться»."""
    sid = _new_session(client)
    resp = client.post(
        f"/api/chat/sessions/{sid}/messages",
        json={"text": "Rfr pfhtubcnhbhjdfnmcz yf Gjhnfkt gjcnfdobrjd?", "audience": "supplier"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "answered", "исправленный вопрос должен найтись в БЗ"

    messages = client.get(f"/api/chat/sessions/{sid}/messages").json()
    user_msg = next(m for m in messages if m["role"] == "user")
    assert user_msg["meta"].get("layout_fixed") is True
    assert user_msg["content"].startswith("Как зарегистрироваться")
    # оригинал сохраняем: пользователь должен видеть, что именно исправили
    assert user_msg["meta"]["original"].startswith("Rfr")


def test_correct_russian_message_is_not_touched(client):
    sid = _new_session(client)
    client.post(f"/api/chat/sessions/{sid}/messages", json={"text": "Как зарегистрироваться на портале?"})
    messages = client.get(f"/api/chat/sessions/{sid}/messages").json()
    user_msg = next(m for m in messages if m["role"] == "user")
    assert "layout_fixed" not in user_msg["meta"]
    assert "typo_fixed" not in user_msg["meta"]
    assert user_msg["content"] == "Как зарегистрироваться на портале?"


def test_typos_are_fixed_in_the_message_itself(client):
    """Опечатку бот и так правил в поисковом запросе — теперь её правка видна и в чате,
    с тем же оригиналом в meta, что у починки раскладки."""
    sid = _new_session(client)
    typo = "Как зарегестрироваться на портале?"
    client.post(f"/api/chat/sessions/{sid}/messages", json={"text": typo, "audience": "supplier"})

    messages = client.get(f"/api/chat/sessions/{sid}/messages").json()
    user_msg = next(m for m in messages if m["role"] == "user")
    assert user_msg["meta"].get("typo_fixed") is True
    assert "layout_fixed" not in user_msg["meta"]
    assert user_msg["meta"]["original"] == typo
    assert "зарегестр" not in user_msg["content"].lower()
    assert user_msg["content"].startswith("Как "), "регистр и нетронутые слова остаются как были"


def test_typo_fix_can_be_switched_off(client):
    rt = client.app.state.rt
    rt.dyn.override("guardrails.typo_fix_enabled", False)
    try:
        sid = _new_session(client)
        client.post(f"/api/chat/sessions/{sid}/messages", json={"text": "Как зарегестрироваться на портале?"})
        messages = client.get(f"/api/chat/sessions/{sid}/messages").json()
    finally:
        rt.dyn.override("guardrails.typo_fix_enabled", True)
    user_msg = next(m for m in messages if m["role"] == "user")
    assert "typo_fixed" not in user_msg["meta"]
    assert user_msg["content"] == "Как зарегестрироваться на портале?"


def test_answer_can_be_rated_and_dislike_enters_review_queue(client, admin_headers):
    """Дизлайк требует разбора: причина может быть в изложении, а не в базе знаний."""
    sid = _new_session(client)
    question = "Как подписать контракт с ЭП?"
    client.post(f"/api/chat/sessions/{sid}/messages", json={"text": question})
    messages = client.get(f"/api/chat/sessions/{sid}/messages").json()
    answer = [m for m in messages if m["role"] == "assistant" and not m["meta"].get("kind")][-1]

    resp = client.post(f"/api/chat/sessions/{sid}/messages/{answer['id']}/rating", json={"value": -1})
    assert resp.status_code == 200

    stored = client.get(f"/api/chat/sessions/{sid}/messages").json()
    assert next(m for m in stored if m["id"] == answer["id"])["meta"]["rating"] == -1

    reviews = client.get("/api/admin/answer-feedback?status=all", headers=admin_headers).json()["items"]
    assert any(r["message_id"] == answer["id"] for r in reviews)


def test_user_message_cannot_be_rated(client):
    sid = _new_session(client)
    client.post(f"/api/chat/sessions/{sid}/messages", json={"text": "Как зарегистрироваться?"})
    messages = client.get(f"/api/chat/sessions/{sid}/messages").json()
    user_msg = next(m for m in messages if m["role"] == "user")
    resp = client.post(f"/api/chat/sessions/{sid}/messages/{user_msg['id']}/rating", json={"value": 1})
    assert resp.status_code == 422


# --- продолжение диалога после перезагрузки страницы ---


def test_session_resumes_after_reload(client):
    """Перезагрузка не должна терять разговор: id лежит в куке у браузера."""
    sid = client.post("/api/chat/sessions").json()["session_id"]
    client.post(f"/api/chat/sessions/{sid}/messages", json={"text": "Как зарегистрироваться?"})

    resumed = client.get(f"/api/chat/sessions/{sid}")
    assert resumed.status_code == 200
    data = resumed.json()
    assert data["session_id"] == sid
    assert data["status"] == "active"
    assert data["escalated"] is False
    assert data["line_label"] == "" and data["addressee_label"] == "", "не передан — адресата нет"
    assert data["chips"], "подсказки нужны приветственному экрану"

    # переписка на месте
    history = client.get(f"/api/chat/sessions/{sid}/messages").json()
    assert any(m["role"] == "user" for m in history)


def test_resume_returns_escalation_state(client):
    """Человек ждал оператора и закрыл вкладку — вернувшись, он должен увидеть
    то же ожидание, а не пустой чат."""
    sid = client.post("/api/chat/sessions").json()["session_id"]
    client.post(
        f"/api/chat/sessions/{sid}/messages", json={"text": "Позовите оператора, у меня не работает ЭЦП"}
    )

    data = client.get(f"/api/chat/sessions/{sid}").json()
    assert data["status"] == "waiting_operator"
    assert data["escalated"] is True
    assert data["line"] in (1, 2, 3)
    # шапка чата называет линию словами, а не голым номером
    assert data["line_label"] and data["line_label"] != "поддержка"


def test_resume_of_unknown_session_is_404(client):
    """Кука пережила пересоздание базы — фронт должен получить понятный отказ
    и завести новый диалог, а не сломаться."""
    assert client.get("/api/chat/sessions/deadbeef" + "0" * 24).status_code == 404


# --- «Мои обращения»: история диалогов из личного кабинета ---


def test_session_history_returns_only_valid_pairs(client):
    """Список прошлых обращений: две реальные пары + одна поддельная.

    Поддельная (чужой/неверный токен владельца) должна тихо выпасть из ответа,
    а не обрушить весь запрос 403-м — иначе одна протухшая запись в
    localStorage прятала бы все настоящие обращения посетителя.
    """
    from app.security import session_owner_token

    sid1 = _new_session(client)
    client.post(f"/api/chat/sessions/{sid1}/messages", json={"text": "Как зарегистрироваться на портале?"})
    sid2 = _new_session(client)
    client.post(f"/api/chat/sessions/{sid2}/messages", json={"text": "Что такое СТЕ?"})

    fake_sid = "0" * 32
    resp = client.post(
        "/api/chat/sessions/history",
        json={
            "sessions": [
                {"session_id": sid1, "owner": session_owner_token(sid1)},
                {"session_id": sid2, "owner": session_owner_token(sid2)},
                {"session_id": fake_sid, "owner": "definitely-not-the-real-token"},
            ]
        },
    )
    assert resp.status_code == 200
    cards = resp.json()
    assert len(cards) == 2, "поддельная пара не должна попасть в ответ"
    assert {c["session_id"] for c in cards} == {sid1, sid2}

    card1 = next(c for c in cards if c["session_id"] == sid1)
    assert card1["status"] == "active"
    assert card1["line"] is None
    assert card1["closed_at"] is None
    # Превью — это последняя реплика диалога (ответ бота), а не первая (приветствие)
    history1 = client.get(f"/api/chat/sessions/{sid1}/messages").json()
    assert card1["last_message_preview"] == history1[-1]["content"][:100]


def test_session_history_with_no_valid_pairs_is_empty_list(client):
    """Все пары поддельные — пустой список, а не отказ."""
    resp = client.post(
        "/api/chat/sessions/history",
        json={"sessions": [{"session_id": "1" * 32, "owner": "nope"}]},
    )
    assert resp.status_code == 200
    assert resp.json() == []
