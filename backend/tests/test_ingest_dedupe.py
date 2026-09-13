"""Повторный импорт документа и починка кодировки заголовка.

Жюри поймало обе вещи на живом стенде: одна и та же выгрузка, залитая дважды,
дала по две одинаковые статьи, а заголовок задачи показывался как «Âûãðóçêà
ïîääåðæêè». Проверяем поведение через публичный API, а не внутренности
конвейера: именно им пользуется администратор.
"""

from app.ingest.text import fix_mojibake

# Документ с разделами-вопросами: конвейер RAG (app/rag/ingest) делает статью на раздел,
# вопрос статьи — заголовок раздела. Версии одного документа узнаются по названию.
DOCUMENT = """# Кабинет поставщика

## Как войти в кабинет поставщика?

Через электронную подпись.

## Где найти реестр сессий?

В разделе «Котировочные сессии».

## Как сменить пароль в кабинете?

В профиле пользователя.
"""


def _upload(client, headers, *, title: str, body: str = DOCUMENT, filename: str = "") -> dict:
    resp = client.post(
        "/api/kb/ingest/file",
        headers=headers,
        files={"file": (filename or f"{title}.md", body.encode("utf-8"), "text/markdown")},
        data={"title": title, "use_llm": "false", "status": "active"},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def _articles(client, headers, question: str, status: str) -> list[dict]:
    listing = client.get("/api/kb", params={"query": question, "status": status}, headers=headers).json()
    return [item for item in listing["items"] if item["question"] == question]


def test_reimport_replaces_previous_version(client, admin_headers):
    """Тот же документ, залитый дважды, не удваивает базу знаний."""
    _upload(client, admin_headers, title="Инструкция по кабинету")
    after_first = _articles(client, admin_headers, "Как войти в кабинет поставщика?", "active")
    assert len(after_first) == 1, "первая загрузка должна дать ровно одну статью"

    _upload(client, admin_headers, title="Инструкция по кабинету")
    after_second = _articles(client, admin_headers, "Как войти в кабинет поставщика?", "active")
    assert len(after_second) == 1, "повторная загрузка того же документа завела вторую статью"
    # Статья именно новая: прошлую версию заменили, а не пропустили загрузку.
    assert after_second[0]["id"] != after_first[0]["id"]

    archived = _articles(client, admin_headers, "Как войти в кабинет поставщика?", "archived")
    assert any(item["id"] == after_first[0]["id"] for item in archived), (
        "прошлая версия должна уйти в архив, а не исчезнуть: на неё ссылаются старые диалоги"
    )


def test_other_document_is_not_touched(client, admin_headers):
    """Замена версии не трогает статьи другого документа."""
    _upload(client, admin_headers, title="Регламент по оплате")
    _upload(client, admin_headers, title="Регламент по доставке")
    _upload(client, admin_headers, title="Регламент по оплате")
    # Считаем по категориям своих двух документов: база в модуле общая, и
    # статьи из соседних тестов на тот же вопрос отвечают тоже.
    active = _articles(client, admin_headers, "Где найти реестр сессий?", "active")
    # Рубрика статьи — «документ › раздел › подраздел»
    by_document = [item["category"].split(" › ")[0] for item in active]
    assert by_document.count("Регламент по оплате") == 1, "повторная загрузка удвоила документ"
    assert by_document.count("Регламент по доставке") == 1, "задет соседний документ"


def test_mojibake_title_is_restored(client, admin_headers):
    """Заголовок из формы, приехавший в чужой кодировке, читается по-русски."""
    job = _upload(client, admin_headers, title="Âûãðóçêà ïîääåðæêè", filename="dump.csv")
    jobs = client.get("/api/kb/ingest/jobs", headers=admin_headers).json()
    created = next(item for item in jobs if item["id"] == job["job_id"])
    assert created["title"] == "Выгрузка поддержки"


def test_fix_mojibake_leaves_normal_text_alone():
    """Обычный текст, включая европейские диакритики, не портится."""
    for text in ("Отчёт 2026", "Report 2026", "Grüße aus Köln", "città", ""):
        assert fix_mojibake(text) == text
