"""Сценарии по порталу, заведённые из админки: наложение поверх заводского файла."""

import pytest

from app.services import guides

GUIDE = {
    "id": "test-upd",
    "title": "Выгрузка УПД",
    "audience": "supplier",
    "questions": ["как выгрузить упд из личного кабинета"],
    "steps": [
        {
            "url": "/profile/user",
            "title": "Откройте «Документы»",
            "text": "Раздел слева в личном кабинете.",
            "target": [{"css": '[data-testid="profile-menu__documents"]'}, {"text": "Документы", "in": "a"}],
            "action": "click",
            "go": "/profile/documents",
            "optional": False,
        },
    ],
}


@pytest.fixture(autouse=True)
def _clean(client, admin_headers):
    """Наложение живёт в модуле процесса — убираем за собой, иначе соседние
    тестовые модули увидят чужие сценарии."""
    yield
    client.delete(f"/api/admin/guides/{GUIDE['id']}", headers=admin_headers)
    client.delete("/api/admin/guides/login", headers=admin_headers)
    guides.set_overrides([])


def test_new_guide_is_matched_right_after_saving(client, admin_headers):
    assert client.get("/api/guides/test-upd").status_code == 404

    saved = client.put(f"/api/admin/guides/{GUIDE['id']}", headers=admin_headers, json=GUIDE)
    assert saved.status_code == 200, saved.text

    # подбор к вопросу работает сразу, без перезапуска сервиса
    matched = client.get(
        "/api/guides/match", params={"q": "как выгрузить упд из личного кабинета", "audience": "supplier"}
    ).json()
    assert matched["guide"]["id"] == "test-upd"
    assert matched["guide"]["start"] == "/profile/user"
    # чужой роли сценарий не показывается
    assert (
        client.get(
            "/api/guides/match", params={"q": "как выгрузить упд из личного кабинета", "audience": "customer"}
        ).json()["guide"]
        is None
    )

    step = client.get("/api/guides/test-upd").json()["steps"][0]
    assert step["target"][0]["css"].startswith("[data-testid")
    # пустые поля в каталог не попадают: его читают и глазами
    assert "optional" not in step and set(step) == {"url", "title", "text", "target", "action", "go"}


def test_factory_guide_can_be_rewritten_and_restored(client, admin_headers):
    original = client.get("/api/guides/login").json()["title"]
    mine = {**GUIDE, "id": "login", "title": "Вход по-нашему", "audience": "all"}
    assert client.put("/api/admin/guides/login", headers=admin_headers, json=mine).status_code == 200
    assert client.get("/api/guides/login").json()["title"] == "Вход по-нашему"

    listing = client.get("/api/admin/guides", headers=admin_headers).json()["items"]
    row = next(item for item in listing if item["id"] == "login")
    assert row["overridden"] is True and row["source"] == "admin" and row["author"] == "admin"

    removed = client.delete("/api/admin/guides/login", headers=admin_headers).json()
    assert removed["restored"] is True
    assert client.get("/api/guides/login").json()["title"] == original


def test_factory_guide_can_be_switched_off(client, admin_headers):
    assert (
        client.post(
            "/api/admin/guides/login/enabled", headers=admin_headers, params={"enabled": False}
        ).status_code
        == 200
    )
    assert client.get("/api/guides/login").status_code == 404
    assert client.get("/api/guides/match", params={"q": "как войти на портал"}).json()["guide"] is None
    # выключенный сценарий остаётся в списке админки — иначе его не включить обратно
    row = next(
        item
        for item in client.get("/api/admin/guides", headers=admin_headers).json()["items"]
        if item["id"] == "login"
    )
    assert row["enabled"] is False

    client.post("/api/admin/guides/login/enabled", headers=admin_headers, params={"enabled": True})
    assert client.get("/api/guides/login").status_code == 200


@pytest.mark.parametrize(
    "broken",
    [
        {"go": "https://evil.example/phish"},  # увод с портала по кнопке «Далее»
        {"go": "//evil.example/phish"},  # протокол-относительный адрес — туда же
        {"url": "https://evil.example"},
        {"target": [{"contains": True}]},  # локатор, которым ничего не найти
    ],
)
def test_step_cannot_send_the_person_off_the_portal(client, admin_headers, broken):
    step = {**GUIDE["steps"][0], **broken}
    body = {**GUIDE, "steps": [step]}
    resp = client.put(f"/api/admin/guides/{GUIDE['id']}", headers=admin_headers, json=body)
    assert resp.status_code == 422, resp.text


def test_identifier_and_rights_are_checked(client, admin_headers, operator_headers):
    # оператор сценарии не правит
    assert (
        client.put(f"/api/admin/guides/{GUIDE['id']}", headers=operator_headers, json=GUIDE).status_code
        == 403
    )
    # идентификатор в адресе и в теле обязан совпадать
    assert client.put("/api/admin/guides/other-id", headers=admin_headers, json=GUIDE).status_code == 400
    # удалять нечего — заводской сценарий из файла админка не трогает
    assert client.delete("/api/admin/guides/login", headers=admin_headers).status_code == 404


# --- черновик нейросетью ---


def test_draft_keeps_locators_and_survives_without_model(client, admin_headers):
    """Модель пишет слова, а не разметку: селекторы обязаны дойти нетронутыми,
    а в демо-режиме без LLM ручка отвечает честным отказом, а не ошибкой."""
    resp = client.post(
        "/api/admin/guides/draft",
        headers=admin_headers,
        json={
            "goal": "как выгрузить УПД",
            "audience": "supplier",
            "steps": GUIDE["steps"],
        },
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["llm"] is False and data["reason"]
    assert len(data["steps"]) == len(GUIDE["steps"])
    assert data["steps"][0]["target"] == [
        {"css": '[data-testid="profile-menu__documents"]'},
        {"text": "Документы", "in": "a"},
    ]
    assert data["steps"][0]["title"] == "Откройте «Документы»"
    assert data["steps"][0]["go"] == "/profile/documents"


def test_words_from_the_model_land_on_recorded_steps():
    from app.services import guide_draft

    recorded = [
        {
            "url": "/",
            "title": "Нажмите «Войти»",
            "text": "",
            "target": [{"css": '[data-testid="top-menu__login-btn"]'}],
            "action": "click",
        }
    ]
    answer = guide_draft._clean_json(
        '```json\n{"title": "Вход", "questions": ["КАК ВОЙТИ"], '
        '"steps": [{"title": "Нажмите «Войти»", "text": "Кнопка в правом верхнем углу."}]}\n```'
    )
    merged = guide_draft.merge(recorded, answer)
    assert merged["title"] == "Вход"
    assert merged["questions"] == ["как войти"]  # формулировки приводим к строчным
    assert merged["steps"][0]["text"].startswith("Кнопка")
    assert merged["steps"][0]["target"] == recorded[0]["target"]
    assert merged["steps"][0]["action"] == "click"
