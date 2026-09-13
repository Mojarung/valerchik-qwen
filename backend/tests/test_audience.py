"""Роль собеседника — поставщик или заказчик.

У портала две стороны сделки, и на «как зарегистрироваться» им отвечают разные
статьи. Здесь проверяется вся цепочка: разбор роли из слов и переключателя,
фильтр поиска, уточняющий вопрос при неизвестной роли и её память у диалога.
"""

import pytest

from app.services import audience
from app.services.retrieval import RetrievedDoc

# --- разбор роли -----------------------------------------------------------


def test_normalize_accepts_keys_and_russian_aliases():
    assert audience.normalize("supplier") == "supplier"
    assert audience.normalize("Заказчик") == "customer"
    assert audience.normalize("поставщики") == "supplier"
    assert audience.normalize(None) == "all"
    assert audience.normalize("что-то ещё") == "all"
    assert audience.normalize("") == "all"


def test_allowed_and_fits():
    assert audience.allowed("supplier") == {"all", "supplier"}
    assert audience.allowed("all") == {"all", "supplier", "customer"}
    # пустая роль у старых строк базы — «для всех»
    assert audience.fits(None, "customer")
    assert audience.fits("customer", "customer")
    assert not audience.fits("supplier", "customer")
    assert audience.fits("supplier", "all")


@pytest.mark.parametrize(
    "text, expected",
    [
        ("Я заказчик", "customer"),
        ("я — поставщик, как разместить оферту?", "supplier"),
        ("Мы заказчики, как создать сессию?", "customer"),
        ("Являюсь поставщиком, не вижу кнопку", "supplier"),
        ("Хочу зарегистрироваться в качестве заказчика", "customer"),
        ("Как заказчику создать котировочную сессию?", "customer"),
        # вопрос поставщика ПРО заказчика — роль менять нельзя
        ("Заказчик отклонил УПД — что делать?", None),
        ("Как выставить счёт для заказчика?", None),
        ("Как заказчик отклонил УПД, что мне делать", None),
        # обе стороны сразу — неоднозначно
        ("Я поставщик, но иногда я заказчик", None),
        ("Как зарегистрироваться на Портале поставщиков?", None),
        # Косвенный падеж после «я/мы» — это про другую сторону сделки, а не про
        # себя. Раньше такие обороты переворачивали роль на весь диалог: заказчик,
        # написавший «мы поставщиков выбираем по цене», получал базу для поставщика.
        ("Мы поставщиков выбираем по цене", None),
        ("Я заказчику отправил УПД, а он не подписывает", None),
        ("Мы заказчику выставили счёт", None),
        ("Я поставщика не нашёл в реестре", None),
    ],
)
def test_detect_role_from_words(text, expected):
    assert audience.detect(text) == expected


def test_clarify_options_are_recognised_as_answers():
    """Кнопки уточнения — обычные реплики, и detect обязан их понимать."""
    for option in audience.clarify_options():
        assert audience.detect(option["text"]) == option["audience"]


@pytest.mark.parametrize(
    "text, expected",
    [
        ("Я поставщик", True),
        ("Я заказчик.", True),
        ("поставщик", True),
        ("Да, я заказчик", True),
        ("Я всё-таки поставщик", True),
        ("Я заказчик, и ещё вопрос: как создать сессию?", False),
        ("Как заказчику создать сессию?", False),
    ],
)
def test_role_choice_is_a_standalone_reply(text, expected):
    assert audience.is_choice(text) is expected


# --- поиск -----------------------------------------------------------------


async def test_search_filters_articles_of_the_other_side(client):
    retriever = client.app.state.rt.retriever
    for role in ("supplier", "customer"):
        docs = await retriever.search("как зарегистрироваться на портале", audience=role)
        assert docs, role
        assert all(d.audience in ("all", role) for d in docs), role

    # без роли участвуют обе стороны
    docs = await retriever.search("как зарегистрироваться на портале")
    assert {d.audience for d in docs} >= {"supplier", "customer"}


async def test_customer_gets_customer_registration(client):
    docs = await client.app.state.rt.retriever.search(
        "как зарегистрироваться на портале поставщиков", audience="customer"
    )
    assert "уполномоченный орган" in docs[0].answer.lower()


# --- диалог ----------------------------------------------------------------


def _session(client, audience_key: str | None = None) -> dict:
    body = {"audience": audience_key} if audience_key else None
    resp = client.post("/api/chat/sessions", json=body)
    assert resp.status_code == 200
    return resp.json()


def test_unknown_role_is_asked_before_answering(client):
    """«Как зарегистрироваться» без роли: две статьи для разных сторон, и бот
    переспрашивает вместо того, чтобы отправить заказчика оформлять ЭП."""
    sid = _session(client)["session_id"]
    data = client.post(
        f"/api/chat/sessions/{sid}/messages", json={"text": "Как зарегистрироваться на Портале поставщиков?"}
    ).json()
    assert data["status"] == "clarify"
    assert [o["audience"] for o in data["options"]] == ["supplier", "customer"]

    saved = client.get(f"/api/chat/sessions/{sid}/messages").json()[-1]
    assert saved["meta"]["kind"] == "clarify_audience"
    assert saved["meta"]["question"].startswith("Как зарегистрироваться")


def test_answering_the_role_question_answers_the_original_question(client):
    sid = _session(client)["session_id"]
    first = client.post(
        f"/api/chat/sessions/{sid}/messages", json={"text": "Как зарегистрироваться на Портале поставщиков?"}
    ).json()
    assert first["status"] == "clarify"

    # «Я заказчик» — не новый вопрос, а недостающая половина прежнего
    answer = client.post(f"/api/chat/sessions/{sid}/messages", json={"text": "Я заказчик"}).json()
    assert answer["status"] == "answered"
    assert "уполномоченный орган" in answer["answer"].lower()
    # первый источник — статья для заказчика, а не общая инструкция поставщика
    assert "заказчик" in answer["citations"][0]["question"].lower()

    # роль запомнилась у диалога и видна при восстановлении
    resumed = client.get(f"/api/chat/sessions/{sid}").json()
    assert resumed["audience"] == "customer"
    # подсказки под роль — заказчику предлагают то, что делает заказчик
    assert any("сессию" in chip.lower() for chip in resumed["chips"])


def test_explicit_role_answers_without_questions(client):
    sid = _session(client, "customer")["session_id"]
    answer = client.post(
        f"/api/chat/sessions/{sid}/messages", json={"text": "Как зарегистрироваться на Портале поставщиков?"}
    ).json()
    assert answer["status"] == "answered"
    assert "уполномоченный орган" in answer["answer"].lower()

    sid = _session(client, "supplier")["session_id"]
    answer = client.post(
        f"/api/chat/sessions/{sid}/messages", json={"text": "Как зарегистрироваться на Портале поставщиков?"}
    ).json()
    assert answer["status"] == "answered"
    assert "ЕСИА" in answer["answer"]


def test_switch_in_message_overrides_session_role(client):
    """Переключатель едет с каждым сообщением: передумать можно посреди разговора."""
    sid = _session(client, "supplier")["session_id"]
    answer = client.post(
        f"/api/chat/sessions/{sid}/messages",
        json={"text": "Как зарегистрироваться на Портале поставщиков?", "audience": "customer"},
    ).json()
    assert answer["status"] == "answered"
    assert "уполномоченный орган" in answer["answer"].lower()
    assert client.get(f"/api/chat/sessions/{sid}").json()["audience"] == "customer"

    user_msg = next(m for m in client.get(f"/api/chat/sessions/{sid}/messages").json() if m["role"] == "user")
    assert user_msg["meta"]["audience"] == "customer"


def test_words_beat_the_switch(client):
    """«Я заказчик» написано только что, переключатель мог остаться от прошлого раза."""
    sid = _session(client, "supplier")["session_id"]
    client.post(
        f"/api/chat/sessions/{sid}/messages",
        json={"text": "Я заказчик, как создать котировочную сессию?", "audience": "supplier"},
    )
    assert client.get(f"/api/chat/sessions/{sid}").json()["audience"] == "customer"


def test_audience_endpoint_changes_role_and_chips(client):
    created = _session(client)
    sid = created["session_id"]
    default_chips = created["chips"]

    resp = client.post(f"/api/chat/sessions/{sid}/audience", json={"audience": "customer"})
    assert resp.status_code == 200
    assert resp.json()["audience"] == "customer"
    assert resp.json()["chips"] != default_chips
    assert client.get(f"/api/chat/sessions/{sid}").json()["audience"] == "customer"

    # обратно в «не выбрано»
    resp = client.post(f"/api/chat/sessions/{sid}/audience", json={"audience": "all"})
    assert resp.json()["chips"] == default_chips
    assert client.get(f"/api/chat/sessions/{sid}").json()["audience"] == "all"


def test_audience_endpoint_requires_owner(client):
    sid = _session(client)["session_id"]
    resp = client.post(
        f"/api/chat/sessions/{sid}/audience",
        json={"audience": "customer"},
        headers={"X-Session-Owner": "0" * 32},
    )
    assert resp.status_code == 403


def test_role_question_is_not_asked_for_distinct_questions(client):
    """Роль уточняется только когда две статьи — один вопрос для разных сторон."""
    chat = client.app.state.rt.chat
    assert (
        chat._same_question(
            "Как зарегистрироваться на портале?", "Как зарегистрироваться на портале в качестве заказчика?"
        )
        >= 0.5
    )
    assert (
        chat._same_question("Что такое котировочная сессия?", "Как заказчику создать котировочную сессию?")
        < 0.5
    )


async def test_role_question_needs_two_sides_close_together(client):
    rt = client.app.state.rt
    sid = _session(client)["session_id"]

    def doc(aid: str, role: str, score: float, question: str = "Как зарегистрироваться?") -> RetrievedDoc:
        return RetrievedDoc(
            article_id=aid,
            question=question,
            answer="текст",
            category="Регистрация",
            line=1,
            score=score,
            audience=role,
        )

    close = [doc("s", "supplier", 0.9), doc("c", "customer", 0.88)]
    assert await rt.chat._maybe_clarify_audience(sid, "как зарегистрироваться", close, "all", 0.0)

    # Общая статья может вклиниться перед двумя ролевыми версиями. Роль всё
    # равно надо спросить: позиция пары в top-k не меняет бизнес-решение.
    behind_common = [doc("all", "all", 0.93, "Регистрация бесплатна?"), *close]
    assert await rt.chat._maybe_clarify_audience(
        _session(client)["session_id"], "как зарегистрироваться", behind_common, "all", 0.0
    )

    # В production заголовки документов — существительные, а люди спрашивают
    # глаголом: «зарегистрироваться» должно совпасть с «Регистрация».
    production_titles = [
        doc("s-prod", "supplier", 0.8037, "3.3 Регистрация поставщика"),
        doc("c-prod", "customer", 0.7906, "3.3 Регистрация заказчика"),
    ]
    assert await rt.chat._maybe_clarify_audience(
        _session(client)["session_id"],
        "Как зарегистрироваться на Портале поставщиков?",
        production_titles,
        "all",
        0.0,
    )

    # Точный общий ответ не зависит от роли — его отдаём сразу.
    exact_common = [doc("all", "all", 0.93, "Как зарегистрироваться?"), *close]
    assert (
        await rt.chat._maybe_clarify_audience(
            _session(client)["session_id"], "как зарегистрироваться", exact_common, "all", 0.0
        )
        is None
    )

    # роль известна — вопросов нет
    assert (
        await rt.chat._maybe_clarify_audience(
            _session(client)["session_id"], "как зарегистрироваться", close, "supplier", 0.0
        )
        is None
    )
    # одна из статей «для всех» — спора нет
    common = [doc("s", "supplier", 0.9), doc("c", "all", 0.88)]
    assert (
        await rt.chat._maybe_clarify_audience(
            _session(client)["session_id"], "как зарегистрироваться", common, "all", 0.0
        )
        is None
    )
    # явный лидер — отвечаем ему
    lead = [doc("s", "supplier", 0.9), doc("c", "customer", 0.5)]
    assert (
        await rt.chat._maybe_clarify_audience(
            _session(client)["session_id"], "как зарегистрироваться", lead, "all", 0.0
        )
        is None
    )
    # Решение принимается по фактической RRF-шкале, а не по близости embeddings.
    fused_lead = [
        RetrievedDoc(**{**close[0].__dict__, "kw_score": 0.03}),
        RetrievedDoc(**{**close[1].__dict__, "kw_score": 0.01}),
    ]
    assert (
        await rt.chat._maybe_clarify_audience(
            _session(client)["session_id"], "как зарегистрироваться", fused_lead, "all", 0.0
        )
        is None
    )
    # просьба позвать человека не переспрашивается никогда
    assert (
        await rt.chat._maybe_clarify_audience(
            _session(client)["session_id"], "позовите оператора", close, "all", 0.0
        )
        is None
    )


async def test_role_with_a_new_question_does_not_restore_the_old_one(client, monkeypatch):
    """«Я заказчик, и ещё вопрос…» выбирает роль, но не теряет новый вопрос."""
    rt = client.app.state.rt
    sid = _session(client)["session_id"]
    docs = [
        RetrievedDoc(
            "s",
            "Как зарегистрироваться?",
            "Для поставщика",
            "Регистрация",
            1,
            0.9,
            audience="supplier",
        ),
        RetrievedDoc(
            "c",
            "Как зарегистрироваться заказчику?",
            "Для заказчика",
            "Регистрация",
            1,
            0.88,
            audience="customer",
        ),
    ]
    assert await rt.chat._maybe_clarify_audience(sid, "Как зарегистрироваться?", docs, "all", 0.0)

    seen: dict[str, str] = {}

    async def capture(turn):
        seen["text"] = turn.text
        return {"status": "captured"}

    monkeypatch.setattr(rt.chat, "_agent_answer", capture)
    question = "Я заказчик, и ещё вопрос: как создать котировочную сессию?"
    result = await rt.chat.handle_user_message(sid, question)

    assert result["status"] == "captured"
    assert seen["text"] == question
    assert client.get(f"/api/chat/sessions/{sid}").json()["audience"] == "customer"


def test_kill_switch_for_role_question(client, admin_headers):
    client.put(
        "/api/admin/settings", headers=admin_headers, json={"updates": {"clarify.audience_enabled": False}}
    )
    try:
        sid = _session(client)["session_id"]
        data = client.post(
            f"/api/chat/sessions/{sid}/messages",
            json={"text": "Как зарегистрироваться на Портале поставщиков?"},
        ).json()
        assert data["status"] != "clarify"
    finally:
        client.put(
            "/api/admin/settings", headers=admin_headers, json={"updates": {"clarify.audience_enabled": True}}
        )


# --- база знаний и мета ----------------------------------------------------


def test_kb_list_filters_by_audience(client, admin_headers):
    listing = client.get(
        "/api/kb", params={"audience": "customer", "page_size": 100}, headers=admin_headers
    ).json()
    assert listing["total"] >= 5
    assert all(a["audience"] == "customer" for a in listing["items"])

    everyone = client.get(
        "/api/kb", params={"audience": "all", "page_size": 200}, headers=admin_headers
    ).json()
    assert everyone["total"] >= 1
    assert all(a["audience"] == "all" for a in everyone["items"])


def test_import_and_export_carry_audience(client, admin_headers, operator_headers):
    items = [
        {
            "question": "Как заказчику проверить лимиты закупок в тесте?",
            "answer": "Откройте раздел лимитов в личном кабинете заказчика.",
            "category": "Импортированные",
            "keywords": ["лимиты"],
            "line": 1,
            "status": "active",
            "audience": "customer",
            "images": [{"url": "https://example.test/1.png", "caption": "Рисунок 1"}],
        }
    ]
    assert client.post("/api/kb/import", json={"items": items}, headers=admin_headers).status_code == 200

    exported = client.get("/api/kb/export.json", headers=admin_headers).json()
    row = next(a for a in exported if "лимиты закупок в тесте" in a["question"])
    assert row["audience"] == "customer"
    assert row["images"][0]["url"] == "https://example.test/1.png"

    # роль без указания — «для всех», как и раньше
    found = client.get("/api/kb", params={"query": "лимиты закупок в тесте"}, headers=admin_headers).json()[
        "items"
    ][0]
    assert found["audience"] == "customer" and found["images"]


def test_article_create_defaults_to_everyone(client, admin_headers):
    created = client.post(
        "/api/kb",
        headers=admin_headers,
        json={
            "question": "Проверочная статья без роли: что это?",
            "answer": "Достаточно длинный ответ, чтобы пройти валидацию схемы.",
            "category": "Проверка",
            "keywords": [],
            "line": 1,
            "status": "active",
        },
    ).json()
    found = client.get(
        "/api/kb", params={"query": "Проверочная статья без роли"}, headers=admin_headers
    ).json()["items"][0]
    assert found["id"] == created["id"] and found["audience"] == "all"


def test_meta_exposes_audience_options(client):
    meta = client.get("/api/meta").json()
    assert [o["key"] for o in meta["audiences"]] == ["supplier", "customer"]
    assert all(o["label"] and o["hint"] for o in meta["audiences"])
