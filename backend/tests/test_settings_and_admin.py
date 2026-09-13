"""Динамические настройки (hot-reload без рестарта), статистика, отчёты, аудит, rate limit."""


def test_settings_listed_with_categories(client, admin_headers):
    settings = client.get("/api/admin/settings", headers=admin_headers).json()
    keys = {s["key"] for s in settings}
    assert {"chat.greeting", "agent.confidence_threshold", "guardrails.profanity_action"} <= keys
    api_key = next(s for s in settings if s["key"] == "llm.api_key")
    assert "***" in str(api_key["value"]) or api_key["value"] == ""  # секрет маскируется


def test_settings_hot_reload_no_restart(client, admin_headers):
    new_greeting = "Тестовое приветствие после горячего обновления!"
    resp = client.put(
        "/api/admin/settings", json={"updates": {"chat.greeting": new_greeting}}, headers=admin_headers
    )
    assert resp.status_code == 200
    assert "chat.greeting" in resp.json()["changed"]

    # без перезапуска: новый диалог сразу получает новое приветствие
    session = client.post("/api/chat/sessions").json()
    assert session["greeting"] == new_greeting


def test_multiple_long_settings_can_be_updated_together(client, admin_headers):
    resp = client.put(
        "/api/admin/settings",
        json={
            "updates": {
                "source_conflicts.auto_enabled": False,
                "source_conflicts.auto_daily_call_limit": 18,
            }
        },
        headers=admin_headers,
    )
    assert resp.status_code == 200
    assert set(resp.json()["changed"]) == {
        "source_conflicts.auto_enabled",
        "source_conflicts.auto_daily_call_limit",
    }


def test_settings_unknown_key_rejected(client, admin_headers):
    resp = client.put("/api/admin/settings", json={"updates": {"evil.key": 1}}, headers=admin_headers)
    assert resp.status_code == 422


def test_threshold_change_applies_immediately(client, admin_headers):
    resp = client.put(
        "/api/admin/settings", json={"updates": {"agent.confidence_threshold": 0.99}}, headers=admin_headers
    )
    assert resp.status_code == 200
    sid = client.post("/api/chat/sessions").json()["session_id"]
    answer = client.post(
        f"/api/chat/sessions/{sid}/messages", json={"text": "Расскажите про котировочные сессии подробнее"}
    ).json()
    assert answer["status"] == "no_answer"  # порог 0.99 недостижим -> честный отказ
    client.put(
        "/api/admin/settings", json={"updates": {"agent.confidence_threshold": 0.4}}, headers=admin_headers
    )


def test_rate_limit_configurable(client, admin_headers):
    sid = client.post("/api/chat/sessions").json()["session_id"]
    client.put(
        "/api/admin/settings", json={"updates": {"rate_limit.chat_per_minute": 1}}, headers=admin_headers
    )
    # лимит 1/мин: из трёх подряд сообщений минимум одно упрётся в 429
    # даже если между ними случится смена минутного окна
    statuses = [
        client.post(f"/api/chat/sessions/{sid}/messages", json={"text": f"СТЕ {i}"}).status_code
        for i in range(3)
    ]
    assert 429 in statuses
    client.put(
        "/api/admin/settings", json={"updates": {"rate_limit.chat_per_minute": 1000}}, headers=admin_headers
    )


def test_stats_overview(client, admin_headers):
    stats = client.get("/api/admin/stats", headers=admin_headers).json()
    assert stats["sessions"]["total"] > 0
    assert stats["kb"]["active_articles"] >= 50
    assert len(stats["series"]) == 14
    assert stats["quality"]["avg_rating"] is not None


def test_feedback_report_generated(client, admin_headers):
    resp = client.post("/api/admin/feedback/report", headers=admin_headers)
    assert resp.status_code == 200
    report = client.get("/api/admin/feedback/report", headers=admin_headers).json()
    assert report["exists"] is True
    assert report["summary"]["count"] > 0
    assert report["specialists"], "должен быть разрез по специалистам"
    assert report["conclusion"], "должно быть содержательное заключение"
    weakest = report["specialists"][0]
    assert weakest["avg_rating"] <= report["specialists"][-1]["avg_rating"]


def test_reindex_and_retrain_endpoints(client, admin_headers):
    assert client.post("/api/admin/reindex", headers=admin_headers).status_code == 200
    assert client.post("/api/admin/retrain", headers=admin_headers).status_code == 200
    info = client.get("/api/admin/classifier", headers=admin_headers).json()
    assert info.get("accuracy") is not None
    assert info["samples"] > 50  # дообучение подхватило статьи и диалоги


def test_audit_log(client, admin_headers):
    audit = client.get("/api/admin/audit", headers=admin_headers).json()
    actions = {a["action"] for a in audit}
    assert "settings.update" in actions


def test_exports(client, admin_headers):
    dialogs = client.get("/api/admin/export/dialogs.csv", headers=admin_headers)
    assert dialogs.status_code == 200
    assert "session_id" in dialogs.text[:200]
    feedback = client.get("/api/admin/export/feedback.csv", headers=admin_headers)
    assert feedback.status_code == 200


def test_export_masks_personal_data(client, admin_headers):
    """Выгрузка уходит наружу — телефон и почта из переписки не должны в неё
    попасть, пока включено маскирование. До этого теста маскирование ПДн в
    экспорте не было покрыто ничем."""
    import csv
    import io

    sid = client.post("/api/chat/sessions").json()["session_id"]
    secret = "Мой телефон +7 916 123-45-67, почта ivanov.test@example.com"
    client.post(f"/api/chat/sessions/{sid}/messages", json={"text": secret})

    csv_text = client.get("/api/admin/export/dialogs.csv", headers=admin_headers).text
    assert "+7 916 123-45-67" not in csv_text
    assert "ivanov.test@example.com" not in csv_text
    assert "[телефон]" in csv_text and "[email]" in csv_text

    # Записи считаем разбором CSV, а не по строкам: в сообщениях бывают переводы
    # строк, и физических строк всегда больше, чем записей.
    # BOM в начале файла нужен Excel — для разбора его снимаем
    rows = list(csv.reader(io.StringIO(csv_text.lstrip("﻿")), delimiter=";"))
    header, data_rows = rows[0], [r for r in rows[1:] if r]
    assert header[0] == "session_id" and "сообщение" in header
    assert any(r[0] == sid for r in data_rows), "новый диалог обязан попасть в выгрузку"


def test_export_without_masking_keeps_text(client, admin_headers):
    """Выключенное маскирование действительно отдаёт исходный текст — иначе
    настройка была бы декоративной."""
    client.put(
        "/api/admin/settings", headers=admin_headers, json={"updates": {"guardrails.pii_mask_enabled": False}}
    )
    try:
        sid = client.post("/api/chat/sessions").json()["session_id"]
        client.post(
            f"/api/chat/sessions/{sid}/messages", json={"text": "Напишите на pochta.bez.maski@example.com"}
        )
        csv_text = client.get("/api/admin/export/dialogs.csv", headers=admin_headers).text
        assert "pochta.bez.maski@example.com" in csv_text
    finally:
        client.put(
            "/api/admin/settings",
            headers=admin_headers,
            json={"updates": {"guardrails.pii_mask_enabled": True}},
        )


def test_every_setting_has_human_label(client, admin_headers):
    """У каждой настройки есть человеческое название, а не только ключ.

    Страницу настроек открывает руководитель службы поддержки: подпись
    «chat.layout_fix_delay_ms» ему ничего не говорит, и поле без подписи
    вдобавок не читается скринридером.
    """
    entries = client.get("/api/admin/settings", headers=admin_headers).json()
    assert entries
    without_label = [e["key"] for e in entries if not e.get("label", "").strip()]
    same_as_key = [e["key"] for e in entries if e.get("label") == e["key"]]
    assert without_label == [], f"настройки без названия: {without_label}"
    assert same_as_key == [], f"название совпадает с ключом: {same_as_key}"


def test_secret_setting_is_masked_completely(client, admin_headers):
    """Ключ LLM не показывается даже началом: по префиксу опознаётся провайдер."""
    client.put(
        "/api/admin/settings",
        headers=admin_headers,
        json={"updates": {"llm.api_key": "sk-secret-value-123456"}},
    )
    entries = client.get("/api/admin/settings", headers=admin_headers).json()
    key_entry = next(e for e in entries if e["key"] == "llm.api_key")
    assert key_entry["secret"] is True
    assert key_entry["value"] == "***"
    client.put("/api/admin/settings", headers=admin_headers, json={"updates": {"llm.api_key": ""}})


def test_invalid_setting_value_is_refused_with_explanation(client, admin_headers):
    """Строка в числовой настройке — ошибка формы, а не 500-я в чате через час.

    До проверки значение просто записывалось: падало оно позже, внутри обработки
    сообщения пользователя, и связать одно с другим было нечем.
    """
    resp = client.put(
        "/api/admin/settings",
        headers=admin_headers,
        json={"updates": {"agent.timeout_s": "минуточку"}},
    )
    assert resp.status_code == 422
    assert "agent.timeout_s" in resp.json()["detail"]

    # Значение осталось прежним — отказ не должен быть частичным.
    entries = client.get("/api/admin/settings", headers=admin_headers).json()
    timeout = next(e for e in entries if e["key"] == "agent.timeout_s")
    assert isinstance(timeout["value"], (int, float))


def test_negative_number_is_refused(client, admin_headers):
    """Отрицательный лимит выглядит как «отключить», а работает как отказ всем."""
    resp = client.put(
        "/api/admin/settings",
        headers=admin_headers,
        json={"updates": {"rate_limit.chat_per_minute": -5}},
    )
    assert resp.status_code == 422


def test_valid_update_still_applies(client, admin_headers):
    """Проверка не должна мешать обычной правке."""
    resp = client.put(
        "/api/admin/settings",
        headers=admin_headers,
        json={"updates": {"retrieval.top_k": 7}},
    )
    assert resp.status_code == 200
    entries = client.get("/api/admin/settings", headers=admin_headers).json()
    assert next(e for e in entries if e["key"] == "retrieval.top_k")["value"] == 7
