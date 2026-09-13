"""Озвучка ответов: чистка разметки, обрезка по смыслу, мягкая деградация."""

from app.services import speech


def test_emphasis_markers_are_not_read_aloud():
    """«звёздочка звёздочка срок звёздочка звёздочка» — так справка звучать не должна."""
    assert speech.strip_markdown("Срок — **7 рабочих дней**") == "Срок — 7 рабочих дней"


def test_links_are_spoken_as_words_not_addresses():
    assert speech.strip_markdown("Откройте [личный кабинет](https://zakupki.mos.ru/lk)") == (
        "Откройте личный кабинет"
    )
    assert "http" not in speech.strip_markdown("Подробнее: https://zakupki.mos.ru/help")


def test_lists_and_headings_lose_only_their_markers():
    cleaned = speech.strip_markdown("## Порядок\n\n1. Войдите\n- Нажмите «Подписать»")
    assert "##" not in cleaned and "- " not in cleaned
    assert "Порядок" in cleaned and "Войдите" in cleaned and "Нажмите" in cleaned


def test_code_blocks_do_not_get_read_line_by_line():
    cleaned = speech.strip_markdown("Пример:\n```\nPOST /api/v1/offers\n```\nГотово")
    assert "POST" not in cleaned
    assert "Пример" in cleaned and "Готово" in cleaned


def test_trim_stops_at_a_sentence_boundary():
    text = "Первое предложение. Второе предложение. Третье предложение."
    trimmed = speech.trim_for_speech(text, 40)
    assert trimmed.endswith("."), "обрыв на полуслове звучит как сбой"
    assert len(trimmed) <= 40


def test_trim_keeps_short_text_untouched():
    assert speech.trim_for_speech("Коротко.", 600) == "Коротко."


def test_speak_is_unavailable_without_the_voice_profile(client):
    """Профиль voice не поднят — озвучка обязана вежливо отсутствовать, а не падать."""
    resp = client.post("/api/voice/speak", json={"text": "Проверка"})
    assert resp.status_code == 503
    assert "профиль voice" in resp.json()["detail"]


def test_meta_tells_the_frontend_that_speech_is_off(client):
    meta = client.get("/api/meta").json()
    assert meta["tts_available"] is False, "кнопки быть не должно, раз ручка ответит 503"


def test_kill_switch_is_reported_before_the_call(client, admin_headers):
    client.put("/api/admin/settings", headers=admin_headers, json={"updates": {"voice.tts_enabled": False}})
    try:
        assert client.get("/api/meta").json()["tts_available"] is False
        assert client.post("/api/voice/speak", json={"text": "Проверка"}).status_code == 503
    finally:
        client.put(
            "/api/admin/settings", headers=admin_headers, json={"updates": {"voice.tts_enabled": True}}
        )
