"""Пошаговые сценарии по порталу: каталог, подбор к вопросу, доставка в ответ."""

from app.services import guides

STEP_KEYS = {"title", "text", "target"}


def test_catalog_is_well_formed():
    """Каждый шаг — заголовок, пояснение и хотя бы один локатор; адреса — пути портала."""
    data = guides.catalog()
    assert data["origin"] == "https://zakupki.mos.ru"
    items = data["guides"]
    assert len(items) >= 10
    assert len({g["id"] for g in items}) == len(items), "дубли идентификаторов"
    for guide in items:
        assert guide["audience"] in ("all", "supplier", "customer"), guide["id"]
        assert guide["questions"] and guide["steps"], guide["id"]
        assert guide["start"].startswith("/"), guide["id"]
        for step in guide["steps"]:
            assert STEP_KEYS <= set(step), (guide["id"], step.get("title"))
            assert step["target"], (guide["id"], step["title"])
            for locator in step["target"]:
                assert locator.get("css") or locator.get("text") or locator.get("href"), locator
            if step.get("url"):
                assert step["url"].startswith("/"), (guide["id"], step["url"])
            if step.get("go"):
                assert step["go"].startswith("/") and not step["go"].startswith("/auth/"), (
                    guide["id"],
                    step["go"],
                )
            if step.get("action"):
                assert step["action"] in ("click", "input"), (guide["id"], step["action"])
    # Адреса Keycloak сами по себе не открываются — у таких шагов задан переход
    # или явное «только с предыдущего шага»
    for guide in items:
        for step in guide["steps"]:
            if str(step.get("url", "")).startswith("/auth/"):
                assert "go" in step, (guide["id"], step["title"])


def test_match_needs_most_of_the_pattern():
    assert guides.match("как войти в личный кабинет")["id"] == "login"
    assert (
        guides.match("подскажите, как мне найти котировочные сессии по моей продукции", "supplier")["id"]
        == "find-auctions"
    )
    # одно общее слово — не совпадение
    assert guides.match("сколько стоит участие") is None
    assert guides.match("") is None


def test_role_decides_between_supplier_and_customer_guides():
    question = "как зарегистрироваться на портале"
    assert guides.match(question, "supplier")["id"] == "register-supplier"
    assert guides.match(question, "customer")["id"] == "register-customer"
    # роль не выбрана, а сценарии спорят — не гадаем: чат в этот момент спрашивает роль
    assert guides.match(question) is None
    # а общий сценарий при неизвестной роли — пожалуйста
    assert guides.match("где центр поддержки")["id"] == "knowledge-base"


def test_supplier_wording_is_not_a_tie():
    """«Портал поставщиков» — имя всего портала, но человек, написавший его,
    ждёт сценарий поставщика. Раньше эта формулировка стояла у обоих сценариев,
    охват совпадал, роли спорили — и человек не получал ни одного."""
    assert guides.match("как зарегистрироваться на портале поставщиков")["id"] == "register-supplier"
    assert (
        guides.match("как зарегистрироваться на портале поставщиков", "customer")["id"] == "register-customer"
    )


def test_registration_step_has_no_catch_all_locator():
    """Локатор «form» находил форму входа: адрес шага сравнивается по префиксу,
    и движок перескакивал на «выберите способ регистрации» поверх логина."""
    for guide_id in ("register-supplier", "register-customer"):
        for step in guides.get(guide_id)["steps"]:
            assert {"css": "form"} not in step["target"], (guide_id, step["title"])


def test_guides_of_the_other_side_are_hidden():
    assert guides.match("как создать котировочную сессию", "supplier") is None
    assert guides.match("как создать котировочную сессию", "customer")["id"] == "create-auction"
    assert all(g["audience"] != "customer" for g in guides.all_guides("supplier"))


def test_public_shape_drops_matching_patterns():
    shown = guides.public(guides.get("login"))
    assert set(shown) == {"id", "title", "audience", "origin", "start", "steps"}
    assert shown["start"] == "/"


def test_guides_api(client):
    listing = client.get("/api/guides", params={"audience": "customer"}).json()
    ids = {g["id"] for g in listing["guides"]}
    assert "create-auction" in ids and "create-offer" not in ids
    assert listing["origin"].startswith("https://")

    assert client.get("/api/guides/login").json()["id"] == "login"
    assert client.get("/api/guides/nope").status_code == 404

    matched = client.get("/api/guides/match", params={"q": "как войти на портал"}).json()
    assert matched["guide"]["id"] == "login"


def test_guide_travels_with_the_answer(client, admin_headers):
    sid = client.post("/api/chat/sessions", json={"audience": "supplier"}).json()["session_id"]
    client.post(f"/api/chat/sessions/{sid}/messages", json={"text": "Как зарегистрироваться на портале?"})
    answer = client.get(f"/api/chat/sessions/{sid}/messages").json()[-1]
    assert answer["role"] == "assistant"
    assert answer["meta"]["guide"]["id"] == "register-supplier"
    assert answer["meta"]["guide"]["steps"][0]["target"][0]["css"].startswith("[data-testid")

    # выключатель: сценарии можно отключить из админки без перезапуска
    client.put("/api/admin/settings", headers=admin_headers, json={"updates": {"guides.enabled": False}})
    try:
        sid = client.post("/api/chat/sessions", json={"audience": "supplier"}).json()["session_id"]
        client.post(f"/api/chat/sessions/{sid}/messages", json={"text": "Как войти на портал?"})
        answer = client.get(f"/api/chat/sessions/{sid}/messages").json()[-1]
        assert "guide" not in answer["meta"]
    finally:
        client.put("/api/admin/settings", headers=admin_headers, json={"updates": {"guides.enabled": True}})
