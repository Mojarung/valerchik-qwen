"""Края импорта, которые рефакторинг конвейера обязан сохранить.

Таблица «вопрос — ответ», присланная текстом, идёт прямым импортом, без LLM, и
берёт линию из параметров. Файл, который не разобрал ни один источник, — отказ
с объяснением в задаче, а не «Готово, статей: 0».
"""

from app.services.ingest import IngestRequest, ingest_document

PAIRS = [
    ("Как сменить реквизиты организации?", "В профиле организации, раздел «Банковские счета»."),
    ("Куда жаловаться на заказчика?", "В ФАС через форму на портале поставщиков."),
    ("Как отозвать поданную оферту?", "Откройте оферту и нажмите «Снять с публикации»."),
    ("Где посмотреть историю заявок?", "В личном кабинете, раздел «Мои заявки»."),
    ("Как продлить электронную подпись?", "Обратитесь в удостоверяющий центр до окончания срока."),
    ("Почему не приходит письмо с кодом?", "Проверьте папку «Спам» и правильность адреса почты."),
]
TABLE = "| Вопрос | Ответ |\n|---|---|\n" + "".join(f"| {q} | {a} |\n" for q, a in PAIRS)


async def test_table_sent_as_text_becomes_articles_as_written(client):
    result = await ingest_document(
        client.app.state.rt, IngestRequest(title="Выгрузка поддержки", text=TABLE, status="draft", line=2)
    )
    assert result["mode"] == "table"
    assert result["created"] == len(PAIRS)
    assert {article["line"] for article in result["articles"]} == {2}
    assert [article["question"] for article in result["articles"]] == [q for q, _ in PAIRS]


def test_file_nobody_can_parse_fails_the_job_with_an_explanation(client, admin_headers):
    resp = client.post(
        "/api/kb/ingest/file",
        headers=admin_headers,
        files={"file": ("dump.bin", b"\x00" * 64, "application/octet-stream")},
        data={"title": "Непонятный файл", "use_llm": "false"},
    )
    assert resp.status_code == 200
    job = client.get("/api/kb/ingest/jobs?limit=1", headers=admin_headers).json()[0]
    assert job["status"] == "error"
    # Объяснение словами, а не имя исключения разборщика (UnsupportedFormatException: …)
    assert job["error"].startswith("В файле «dump.bin» не найдено ни одного фрагмента знаний")
