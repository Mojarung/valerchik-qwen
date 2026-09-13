"""Структурирование неформализованной БЗ: нарезка методички и запасные вопросы."""

import pytest

from app.models import IngestJob
from app.services.ingest import (
    Section,
    answerless_heading,
    extract_keywords,
    extract_text_from_file,
    fallback_question,
    generate_question,
    glossary_terms,
    parse_article_table,
    split_sections,
)

METHODICAL = """
# Регистрация на Портале поставщиков

Регистрация доступна юридическим лицам, индивидуальным предпринимателям и самозанятым.
Вход выполняется через ЕСИА. После заполнения профиля организации заявка проверяется
в течение одного рабочего дня.

2.1. Электронная подпись

Для подписания контрактов требуется усиленная квалифицированная электронная подпись.
Подпись выпускается удостоверяющим центром ФНС. Настройте плагин КриптоПро ЭЦП Browser
plug-in и убедитесь, что сертификат не истёк.

Котировочные сессии

Котировочная сессия — это короткая закупка малого объёма. Заказчик публикует
потребность, поставщики снижают цену в течение установленного времени.
"""


def test_splits_by_headings():
    sections = split_sections(METHODICAL)
    headings = [s.heading for s in sections]
    assert "Регистрация на Портале поставщиков" in headings
    assert "Электронная подпись" in headings  # нумерованный заголовок
    assert "Котировочные сессии" in headings  # короткая строка без точки
    assert all(s.body.strip() for s in sections)


def test_long_section_is_chunked():
    """Кусок длиннее лимита режется по абзацам: один вектор на 40 страниц бесполезен."""
    body = "\n\n".join(f"Абзац номер {i}. " + "Текст раздела о закупках. " * 12 for i in range(10))
    sections = split_sections(f"# Документ\n\n{body}", max_chars=600)
    assert len(sections) > 1
    assert all(len(s.body) <= 900 for s in sections)
    assert all(s.heading == "Документ" for s in sections)


@pytest.mark.parametrize(
    "title,answerless",
    [
        ("Рисунок 82 - Экспорт оферт", True),
        ("(Рисунок 5)", True),
        ("Figure 3 — Contract card", True),
        ("СОДЕРЖАНИЕ", True),
        ("Оглавление", True),
        # Глоссарий оформлен таблицей: в методичках это единственное место, где
        # расшифрованы УПД, МЧД и КС, — отсекать подписи таблиц огулом нельзя
        ("Таблица 1 - Перечень терминов и сокращений", False),
        ("Как выгрузить УПД?", False),
        ("Содержание договора и его существенные условия", False),
    ],
)
def test_captions_and_contents_are_not_questions(title, answerless):
    assert answerless_heading(title) is answerless


GLOSSARY = """| № | Термин | Расшифровка |
| --- | --- | --- |
| 1 | УПД | Универсальный передаточный документ |
| 2 | 44- ФЗ | Федеральный закон о контрактной системе |
| 3 | СТЕ, Стандартная товарная единица , Товар | Карточка товара в каталоге портала |
| 4 | 60 | 60 |
"""


def test_glossary_row_becomes_a_question_about_the_term():
    """Строки глоссария приезжают из PDF нумерованными, с пробелами вокруг дефиса и
    несколькими написаниями термина через запятую. Номер строки термином не считаем,
    в заголовок идёт первое написание — остальные всё равно в теле статьи."""
    terms = glossary_terms("Таблица 1 - Перечень терминов и сокращений", GLOSSARY)

    assert [(t.question, t.answer) for t in terms] == [
        ("Что означает УПД?", "УПД — Универсальный передаточный документ."),
        ("Что означает 44-ФЗ?", "44-ФЗ — Федеральный закон о контрактной системе."),
        (
            "Что означает СТЕ?",
            "СТЕ, Стандартная товарная единица , Товар — Карточка товара в каталоге портала.",
        ),
    ]


def test_term_carries_the_phrasings_people_actually_type():
    """Агент ищет коротко — «упд», а не «что такое упд». Без отдельной точки
    векторного индекса на сам термин определение проигрывает разделу методички
    «11.5.8 Формирование УПД», где то же слово стоит в заголовке."""
    upd, _fz, ste = glossary_terms("Перечень терминов и сокращений", GLOSSARY)

    assert upd.phrasings == ["УПД", "что такое УПД", "расшифровка УПД"]
    # Все написания термина — тоже формулировки: спрашивают и «СТЕ», и «товар»
    assert ste.phrasings == [
        "СТЕ",
        "Стандартная товарная единица",
        "Товар",
        "что такое СТЕ",
        "расшифровка СТЕ",
    ]


def test_glossary_is_parsed_only_under_its_own_heading():
    """Таблиц в методичках хватает: разбирать на определения нужно только глоссарий."""
    assert glossary_terms("Таблица 4 - Статусы контракта", GLOSSARY) == []


def test_list_items_are_not_headings():
    text = "# Шаги\n\n- Открыть личный кабинет\n- Перейти в раздел «Контракты»\n\nГотово."
    sections = split_sections(text)
    assert [s.heading for s in sections] == ["Шаги"]


@pytest.mark.parametrize(
    "section,expected",
    [
        (Section(heading="Как выпустить ЭП?", body="Текст"), "Как выпустить ЭП?"),
        # без хвоста «что нужно знать?»: одинаковый хвост у каждой статьи — шум в поиске
        (Section(heading="Электронная подпись", body="Текст"), "Электронная подпись"),
        (Section(heading="Подписание контракта.", body="Текст"), "Подписание контракта"),
    ],
)
def test_fallback_question_uses_heading(section, expected):
    assert fallback_question(section) == expected


def test_fallback_question_without_heading():
    section = Section(heading=None, body="Котировочная сессия длится три часа. Далее текст.")
    assert fallback_question(section) == "Котировочная сессия длится три часа."


def test_keywords_extracted():
    section = Section(
        heading="Контракты",
        body="Контракт подписывается электронной подписью. Электронная подпись обязательна.",
    )
    keywords = extract_keywords(section)
    assert "контра" in keywords or "подпис" in keywords


def test_html_extraction_keeps_block_breaks():
    html = b"<html><body><h1>Title</h1><p>First</p><p>Second</p></body></html>"
    text = extract_text_from_file("doc.html", html)
    assert "Title" in text and "First" in text
    assert text.count("\n") >= 2  # блоки не схлопнулись в одну строку


def test_unsupported_format_rejected():
    with pytest.raises(ValueError, match="не поддерживается"):
        extract_text_from_file("scan.tiff", b"\x00\x01")


#: Текст из формы задача импорта разбирает как markdown-документ (app/rag/ingest): разделы —
#: по заголовкам «#». Нумерованные и короткие строки без разметки заголовками там не считаются.
METHODICAL_MD = """
# Регистрация на Портале поставщиков

Регистрация доступна юридическим лицам, индивидуальным предпринимателям и самозанятым.
Вход выполняется через ЕСИА.

# Электронная подпись

Для подписания контрактов требуется усиленная квалифицированная электронная подпись.

# Котировочные сессии

Котировочная сессия — это короткая закупка малого объёма.
"""


def test_ingest_queues_job_and_completes(client, admin_headers):
    """Импорт ставится задачей: в lite-режиме брокер выполняет её на месте.

    Число разделов в ответе считает тот же разбор, что и задача: пока ответ считался
    прежней нарезкой, API обещал три раздела, а задача заводила одну статью."""
    resp = client.post(
        "/api/kb/ingest",
        headers=admin_headers,
        json={
            "title": "Методичка о котировочных сессиях",
            "text": METHODICAL_MD,
            "status": "draft",
            "use_llm": False,  # без LLM: тест не должен зависеть от внешнего API
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "queued"
    assert body["sections"] >= 3

    job = client.get(f"/api/kb/ingest/jobs/{body['job_id']}", headers=admin_headers).json()
    assert job["status"] == "done", job
    assert job["created_articles"] == body["sections"]
    assert job["total"] == job["processed"]

    jobs = client.get("/api/kb/ingest/jobs", headers=admin_headers).json()
    assert any(j["id"] == body["job_id"] for j in jobs)

    # статьи действительно появились и лежат черновиками
    articles = client.get("/api/kb?status=draft&page_size=100", headers=admin_headers).json()
    # Конвейер RAG помечает всё загруженное одним источником — «import»
    assert any(a["source"] == "import" and a["category"] for a in articles["items"])


def test_ingest_rejects_empty_document(client, admin_headers):
    resp = client.post(
        "/api/kb/ingest",
        headers=admin_headers,
        json={"title": "Пустышка", "text": " " * 40, "use_llm": False},
    )
    assert resp.status_code == 422


def test_ingest_file_unknown_extension_is_not_rejected_upfront(client, admin_headers):
    """Расширение больше не приговор: формат выбирает реестр источников.

    Раньше файл с непривычным именем отсекался списком расширений до всякого
    разбора. На хакатоне выгрузка приезжает с любым именем, и отказать ей за
    расширение значит не принять данные вовсе: пусть решает содержимое.
    """
    table = (
        "| Вопрос | Ответ |\n|---|---|\n"
        "| Как зарегистрироваться на портале? | Нужна электронная подпись. |\n"
        "| Как подписать контракт? | Откройте карточку контракта. |\n"
        "| Где найти УПД? | В разделе «Исполнение контракта». |\n"
        "| Как сменить реквизиты? | В профиле организации. |\n"
        "| Куда жаловаться? | В ФАС через форму на портале. |\n"
    )
    resp = client.post(
        "/api/kb/ingest/file",
        headers=admin_headers,
        files={"file": ("dump.dat", table.encode("utf-8"), "application/octet-stream")},
    )
    assert resp.status_code == 200, resp.text


def test_ingest_file_rejects_empty_file(client, admin_headers):
    resp = client.post(
        "/api/kb/ingest/file", headers=admin_headers, files={"file": ("empty.txt", b"   ", "text/plain")}
    )
    assert resp.status_code == 422


ARTICLE_TABLE = """## Выгрузка из БЗ

| Заголовок статьи | Описание |
| --- | --- |
| Как подписать контракт | Откройте карточку контракта и нажмите «Подписать» с помощью ЭП. |
| Что такое котировочная сессия | Электронная процедура закупки малого объёма сроком от трёх часов. |
| Как выгрузить УПД | Раздел «Документы» — кнопка «Выгрузить УПД» в карточке исполнения. |
| Где посмотреть статус заявки | Статус отображается в личном кабинете в разделе «Мои заявки». |
| Пустая строка | NaN |
| Как изменить реквизиты | Реквизиты меняются в профиле организации после подтверждения ЭП. |
"""


def test_ready_table_imported_as_articles():
    """Готовая выгрузка «вопрос — ответ» не режется и не переформулируется LLM."""
    rows = parse_article_table(ARTICLE_TABLE)
    assert rows is not None
    assert len(rows) == 5  # строка с NaN отброшена
    assert rows[0]["question"] == "Как подписать контракт"
    assert "Подписать" in rows[0]["answer"]
    assert all("nan" not in r["answer"].lower() for r in rows)


def test_table_rows_without_a_real_answer_are_dropped():
    """Номер страницы и подпись к рисунку в колонке ответа — не статья.

    Такая строка опаснее пустой: вопрос совпадает точно, уверенность высокая,
    и пользователь получает в ответ «60» вместо честного «не знаю».
    """
    table = ARTICLE_TABLE + (
        "| Как удалить токен | 14 |\n"
        "| Где найти историю изменений | Рисунок 68 – История изменений |\n"
        "| Какова сумма зачёта аванса по документу о приемке | документу о приемке |\n"
        "| Какой адрес официального сайта ЕИС | (www.zakupki.gov.ru) |\n"
    )
    rows = parse_article_table(table)
    assert rows is not None
    answers = [r["answer"] for r in rows]
    assert "14" not in answers  # номер страницы
    assert not any(a.startswith("Рисунок") for a in answers)  # подпись к иллюстрации
    assert "документу о приемке" not in answers  # кусок самого вопроса
    assert "(www.zakupki.gov.ru)" in answers  # короткий, но настоящий ответ


def test_table_inside_document_is_not_an_export():
    """В инструкциях таблицы встречаются постоянно — документ от этого не выгрузка."""
    prose = "\n\n".join(f"Раздел {i}. " + "Подробный текст инструкции о закупках. " * 25 for i in range(12))
    assert parse_article_table(f"# Инструкция\n\n{prose}\n\n{ARTICLE_TABLE}") is None


def test_junk_chunks_dropped():
    """Номера страниц и колонтитулы не должны становиться статьями."""
    text = "# Раздел\n\nСодержательный абзац про порядок подписания контракта на портале.\n\n7\n"
    sections = split_sections(text)
    assert all(len(s.body.strip()) >= 40 for s in sections), [s.body for s in sections]


def test_xls_supported_via_markitdown():
    """Старый .xls раньше не открывался вовсе — теперь его читает markitdown."""
    import io

    import xlsxwriter

    buf = io.BytesIO()
    book = xlsxwriter.Workbook(buf, {"in_memory": True})
    sheet = book.add_worksheet("БЗ")
    sheet.write_row(0, 0, ["Заголовок статьи", "Описание"])
    for i in range(1, 7):
        sheet.write_row(i, 0, [f"Вопрос номер {i}", f"Развёрнутый ответ на вопрос номер {i}."])
    book.close()
    text = extract_text_from_file("Статьи.xlsx", buf.getvalue())
    rows = parse_article_table(text)
    assert rows and len(rows) == 6


async def test_llm_question_path_actually_reaches_the_model_builder(monkeypatch):
    """Имя функции в agent.py и импорт в ingest.py обязаны сходиться.

    Импорт локальный — иначе круговая зависимость — и лежит внутри общего
    перехвата, поэтому рассинхрон имён наружу не падает: вопрос тихо берётся из
    заголовка, и по поведению это неотличимо от «LLM не сформулировала».
    Однажды так и вышло: функцию в agent.py сделали публичной, импорт остался
    старым, и разбор методички с подключённой моделью падал целиком. Проверяем
    не результат, а сам факт вызова сборщика модели.
    """
    called: list[bool] = []

    def fake_build_model(dyn):
        called.append(True)
        raise RuntimeError("до сети не идём: важно, что до сборщика модели дошли")

    monkeypatch.setattr("app.services.agent.build_model", fake_build_model)
    dyn = {"llm.base_url": "http://llm.invalid/v1", "llm.model": "test-model"}
    section = Section(heading="Регистрация на портале", body="Текст раздела.")

    assert await generate_question(dyn, section) == fallback_question(section)
    assert called, "ingest не добрался до build_model — разошлись имена при импорте"


async def test_question_without_llm_does_not_touch_the_model_at_all():
    """Без ключа путь LLM не должен даже пытаться собрать модель."""
    section = Section(heading="Электронная подпись", body="Текст раздела.")
    assert await generate_question({}, section) == fallback_question(section)


@pytest.mark.parametrize(
    ("form", "expected"),
    [
        (
            {
                "category": " Оферты ",
                "status": "active",
                "audience": "supplier",
                "use_llm": "false",
                "do_ocr": "true",
            },
            {
                "category": "Оферты",
                "status": "active",
                "audience": "supplier",
                "use_llm": False,
                "do_ocr": True,
            },
        ),
        (
            {"category": "   ", "status": "published", "audience": "кто угодно", "use_llm": "false"},
            {"category": None, "status": "draft", "audience": "all", "use_llm": False, "do_ocr": False},
        ),
    ],
)
async def test_ingest_file_form_reaches_the_job(client, admin_headers, form, expected):
    """Поля формы загрузки доходят до задачи разбора: рубрика без пробелов по
    краям, статус и роль — только из допустимых, остальное — безопасные значения."""
    text = f"Оферту отзывают в личном кабинете. Метка {form['status']}-{len(form)}."
    resp = client.post(
        "/api/kb/ingest/file",
        headers=admin_headers,
        files={"file": ("zametka.txt", text.encode("utf-8"), "text/plain")},
        data={"title": "Заметка", **form},
    )
    assert resp.status_code == 200, resp.text

    async with client.app.state.rt.db.session_factory() as db:
        job = await db.get(IngestJob, resp.json()["job_id"])
    assert job is not None
    assert {key: job.params.get(key) for key in expected} == expected
