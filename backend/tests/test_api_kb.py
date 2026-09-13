"""База знаний: CRUD, мгновенная индексация («динамически докидываем информацию»),
импорт/экспорт, конвертация пробелов в статьи."""

ARTICLE = {
    "question": "Как подключить экзотическую интеграцию с марсоходом?",
    "answer": "Для подключения марсохода откройте раздел «Интеграции» и создайте ключ API "
    "с типом «Межпланетный обмен». Далее следуйте инструкции миссии.",
    "category": "Интеграции и API",
    "keywords": ["марсоход", "интеграция"],
    "line": 2,
    "status": "active",
}


def test_kb_create_updates_search_instantly(client, admin_headers, operator_headers):
    created = client.post("/api/kb", json=ARTICLE, headers=admin_headers)
    assert created.status_code == 200
    article_id = created.json()["id"]

    # новая статья сразу ищется — и оператором, и агентом
    found = client.get(
        "/api/operator/kb-search", params={"q": "интеграция с марсоходом"}, headers=operator_headers
    ).json()
    assert found and found[0]["article_id"] == article_id

    sid = client.post("/api/chat/sessions").json()["session_id"]
    answer = client.post(
        f"/api/chat/sessions/{sid}/messages", json={"text": "Как подключить интеграцию с марсоходом?"}
    ).json()
    assert answer["status"] == "answered"

    # архивация убирает из поиска
    assert client.delete(f"/api/kb/{article_id}", headers=admin_headers).status_code == 200
    found = client.get("/api/operator/kb-search", params={"q": "марсоход"}, headers=operator_headers).json()
    assert all(f["article_id"] != article_id for f in found)


def test_kb_list_filters(client, admin_headers):
    listing = client.get("/api/kb", params={"query": "котировочная"}, headers=admin_headers).json()
    assert listing["total"] >= 1
    categories = client.get("/api/kb/categories", headers=admin_headers).json()
    assert any(c["category"] == "Котировочные сессии" for c in categories)


def test_kb_update(client, admin_headers):
    listing = client.get("/api/kb", params={"query": "СТЕ"}, headers=admin_headers).json()
    item = listing["items"][0]
    payload = {k: item[k] for k in ("question", "answer", "category", "keywords", "line", "status")}
    payload["answer"] += " Дополнение из теста."
    resp = client.put(f"/api/kb/{item['id']}", json=payload, headers=admin_headers)
    assert resp.status_code == 200


def test_kb_import_and_export(client, admin_headers):
    items = [
        {
            "question": "Тестовый импортированный вопрос про выгрузку реестров?",
            "answer": "Тестовый ответ про выгрузку: раздел Отчёты, кнопка Экспорт.",
            "category": "Импортированные",
            "keywords": ["экспорт"],
            "line": 1,
            "status": "active",
        }
    ]
    resp = client.post("/api/kb/import", json={"items": items}, headers=admin_headers)
    assert resp.status_code == 200
    assert resp.json()["imported"] == 1

    csv_resp = client.get("/api/kb/export.csv", headers=admin_headers)
    assert csv_resp.status_code == 200
    assert "вопрос" in csv_resp.text.splitlines()[0] or "question" in csv_resp.text

    json_resp = client.get("/api/kb/export.json", headers=admin_headers)
    assert any("выгрузку реестров" in a["question"] for a in json_resp.json())


def test_gap_convert_to_article(client, admin_headers):
    sid = client.post("/api/chat/sessions").json()["session_id"]
    client.post(
        f"/api/chat/sessions/{sid}/messages",
        json={"text": "Выдаёте ли вы гранты на строительство космодрома?"},
    )
    gaps = client.get("/api/admin/gaps", headers=admin_headers).json()
    gap = next(g for g in gaps if "космодром" in g["query"])

    resp = client.post(
        f"/api/admin/gaps/{gap['id']}/convert",
        json={
            "answer": "Гранты на космодромы портал не выдаёт, обратитесь в профильное ведомство.",
            "category": "Новые темы",
            "line": 1,
            "publish": True,
        },
        headers=admin_headers,
    )
    assert resp.status_code == 200

    # самообучение: теперь агент отвечает на этот вопрос
    sid2 = client.post("/api/chat/sessions").json()["session_id"]
    answer = client.post(
        f"/api/chat/sessions/{sid2}/messages",
        json={"text": "Выдаёте ли вы гранты на строительство космодрома?"},
    ).json()
    assert answer["status"] == "answered"


def test_operator_dialog_to_kb_draft(client, operator_headers, admin_headers):
    sid = client.post("/api/chat/sessions").json()["session_id"]
    client.post(
        f"/api/chat/sessions/{sid}/messages", json={"text": "Нужен оператор, как зарегистрироваться?"}
    )
    assert client.post(f"/api/operator/sessions/{sid}/take", headers=operator_headers).status_code == 200
    resp = client.post(
        f"/api/operator/sessions/{sid}/to-kb",
        json={
            "question": "Вопрос от оператора про новую тему?",
            "answer": "Развёрнутый ответ оператора для базы знаний.",
            "category": "Из диалогов",
            "line": 1,
        },
        headers=operator_headers,
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "draft"

    drafts = client.get("/api/kb", params={"status": "draft"}, headers=admin_headers).json()
    assert drafts["total"] >= 1


def _make_articles(client, admin_headers, count: int, status: str = "draft") -> list[str]:
    return [
        client.post(
            "/api/kb",
            headers=admin_headers,
            json={
                "question": f"Массовая проверка номер {i}: что делает кнопка?",
                "answer": "Достаточно длинный ответ, чтобы пройти валидацию схемы.",
                "category": "Проверка массовых действий",
                "keywords": [],
                "line": 1,
                "status": status,
            },
        ).json()["id"]
        for i in range(count)
    ]


def test_bulk_publish_puts_drafts_into_search(client, admin_headers, operator_headers):
    ids = _make_articles(client, admin_headers, 2, status="draft")

    resp = client.post("/api/kb/bulk", json={"ids": ids, "action": "publish"}, headers=admin_headers)
    assert resp.status_code == 200
    assert resp.json() == {"action": "publish", "affected": 2}

    listing = client.get("/api/kb?status=active&page_size=100", headers=admin_headers).json()
    assert {a["id"] for a in listing["items"]} >= set(ids)
    # опубликованное сразу ищется — значит пачка доехала до индекса
    found = client.get(
        "/api/operator/kb-search", params={"q": "Массовая проверка номер"}, headers=operator_headers
    ).json()
    assert any(f["article_id"] in ids for f in found)


def test_bulk_archive_removes_from_search(client, admin_headers, operator_headers):
    ids = _make_articles(client, admin_headers, 2, status="active")

    resp = client.post("/api/kb/bulk", json={"ids": ids, "action": "archive"}, headers=admin_headers)
    assert resp.json() == {"action": "archive", "affected": 2}

    listing = client.get("/api/kb?status=archived&page_size=100", headers=admin_headers).json()
    assert {a["id"] for a in listing["items"]} >= set(ids)
    found = client.get(
        "/api/operator/kb-search", params={"q": "Массовая проверка номер"}, headers=operator_headers
    ).json()
    assert all(f["article_id"] not in ids for f in found)


def test_bulk_purge_deletes_rows(client, admin_headers):
    ids = _make_articles(client, admin_headers, 2, status="active")

    assert client.post(
        "/api/kb/bulk", json={"ids": ids, "action": "purge"}, headers=admin_headers
    ).json() == {"action": "purge", "affected": 2}

    listing = client.get("/api/kb?page_size=200", headers=admin_headers).json()
    assert all(a["id"] not in ids for a in listing["items"])
    # повтор по тем же id — уже нечего трогать
    assert (
        client.post("/api/kb/bulk", json={"ids": ids, "action": "purge"}, headers=admin_headers).status_code
        == 404
    )


def test_bulk_publish_all_drafts_by_filter(client, admin_headers):
    """Черновиков после разбора методички больше, чем влезает на страницу, —
    поэтому цель можно задать фильтром, а не перечнем id."""
    ids = _make_articles(client, admin_headers, 3, status="draft")
    before_active = client.get("/api/kb?status=active&page_size=200", headers=admin_headers).json()["total"]

    resp = client.post(
        "/api/kb/bulk", json={"action": "publish", "filter": {"status": "draft"}}, headers=admin_headers
    )
    assert resp.status_code == 200
    assert resp.json()["affected"] >= len(ids)

    assert client.get("/api/kb?status=draft&page_size=200", headers=admin_headers).json()["total"] == 0
    after = client.get("/api/kb?status=active&page_size=200", headers=admin_headers).json()
    assert after["total"] >= before_active + len(ids)
    assert {a["id"] for a in after["items"]} >= set(ids)


def test_bulk_filter_respects_search_query(client, admin_headers):
    """Фильтр в действии — тот же, что в списке: трогаем ровно видимое."""
    ids = _make_articles(client, admin_headers, 2, status="draft")
    untouched = client.post(
        "/api/kb",
        headers=admin_headers,
        json={
            "question": "Совсем другая тема: как продлить контракт?",
            "answer": "Достаточно длинный ответ, чтобы пройти валидацию схемы.",
            "category": "Проверка массовых действий",
            "keywords": [],
            "line": 1,
            "status": "draft",
        },
    ).json()["id"]

    resp = client.post(
        "/api/kb/bulk",
        headers=admin_headers,
        json={"action": "archive", "filter": {"query": "Массовая проверка", "status": "draft"}},
    )
    assert resp.status_code == 200

    archived = {
        a["id"]
        for a in client.get("/api/kb?status=archived&page_size=200", headers=admin_headers).json()["items"]
    }
    assert archived >= set(ids)
    assert untouched not in archived


def test_bulk_filter_rejects_blind_purge(client, admin_headers):
    """Удаление насовсем по пустому фильтру снесло бы всю базу одной кнопкой."""
    _make_articles(client, admin_headers, 1)
    resp = client.post("/api/kb/bulk", json={"action": "purge", "filter": {}}, headers=admin_headers)
    assert resp.status_code == 400
    # с уточнением фильтра — уже можно
    assert (
        client.post(
            "/api/kb/bulk",
            headers=admin_headers,
            json={"action": "purge", "filter": {"query": "Массовая проверка"}},
        ).status_code
        == 200
    )


def test_bulk_needs_exactly_one_target(client, admin_headers):
    ids = _make_articles(client, admin_headers, 1)
    # ни ids, ни фильтра
    assert client.post("/api/kb/bulk", json={"action": "publish"}, headers=admin_headers).status_code == 422
    # и то и другое разом — непонятно, что имелось в виду
    assert (
        client.post(
            "/api/kb/bulk",
            headers=admin_headers,
            json={"action": "publish", "ids": ids, "filter": {"status": "draft"}},
        ).status_code
        == 422
    )


def test_bulk_rejects_unknown_action(client, admin_headers):
    ids = _make_articles(client, admin_headers, 1)
    assert (
        client.post("/api/kb/bulk", json={"ids": ids, "action": "burn"}, headers=admin_headers).status_code
        == 422
    )
    assert (
        client.post("/api/kb/bulk", json={"ids": [], "action": "publish"}, headers=admin_headers).status_code
        == 422
    )


def test_bulk_needs_admin(client, operator_headers, admin_headers):
    ids = _make_articles(client, admin_headers, 1)
    assert (
        client.post(
            "/api/kb/bulk", json={"ids": ids, "action": "publish"}, headers=operator_headers
        ).status_code
        == 403
    )


def test_purge_removes_article_for_good(client, admin_headers):
    """Архив обратим, удаление — нет: мусор из неудачного импорта незачем хранить."""
    created = client.post(
        "/api/kb",
        headers=admin_headers,
        json={
            "question": "Временная статья для проверки удаления",
            "answer": "Достаточно длинный ответ, чтобы пройти валидацию схемы.",
            "category": "Проверка",
            "keywords": [],
            "line": 1,
            "status": "active",
        },
    ).json()
    article_id = created["id"]

    # по умолчанию — архив: статья остаётся в базе
    assert client.delete(f"/api/kb/{article_id}", headers=admin_headers).json() == {"status": "archived"}
    listing = client.get("/api/kb?status=archived&page_size=100", headers=admin_headers).json()
    assert any(a["id"] == article_id for a in listing["items"])

    # purge — строки больше нет
    assert client.delete(f"/api/kb/{article_id}?purge=true", headers=admin_headers).json() == {
        "status": "deleted"
    }
    for status in ("", "archived", "active", "draft"):
        listing = client.get(f"/api/kb?status={status}&page_size=100", headers=admin_headers).json()
        assert not any(a["id"] == article_id for a in listing["items"]), status

    assert client.delete(f"/api/kb/{article_id}?purge=true", headers=admin_headers).status_code == 404


def test_listing_pages_are_validated_at_the_door(client, admin_headers):
    """Номер страницы — с единицы, размер — от 1 до 200. Раньше page=0 давал
    отрицательный OFFSET (на Postgres это 500), а page_size=-1 в SQLite снимал
    LIMIT и выгружал всю базу одним ответом."""
    for query in ("page=0", "page=-3", "page_size=0", "page_size=-1", "page_size=201"):
        assert client.get(f"/api/kb?{query}", headers=admin_headers).status_code == 422, query
    assert client.get("/api/kb?page=2&page_size=200", headers=admin_headers).status_code == 200
