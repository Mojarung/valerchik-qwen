"""Источник qa_table: готовая выгрузка «вопрос — ответ».

Проверяем не только «таблица разобралась», но и три вещи, на которых источник
ломает конвейер тихо: проза не должна перехватываться (иначе документ не дойдёт
до нарезки), книга Excel должна остаться источнику spreadsheet, а провенанс
обязан помнить, что текст писал человек — от этого зависит, отдадим ли мы ответ
пользователю дословно.
"""

import pytest

from app.ingest.blob import FileBlob
from app.ingest.registry import MIN_CONFIDENCE, IngestContext
from app.ingest.sources.qa_table import QaTableSource

# Выгрузка с привычными заголовками — самый частый случай
TABLE_CLASSIC = """| Вопрос | Ответ | Линия |
|---|---|---|
| Как зарегистрироваться на портале? | Нужна электронная подпись и заявка в личном кабинете. | 1 |
| Как подписать контракт? | Откройте карточку закупки и нажмите кнопку подписания. | 2 |
| Почему не приходит УПД? | Проверьте статус документа в разделе исполнения. | 2 |
| Как сменить банковские реквизиты? | Реквизиты меняются в профиле организации. | 1 |
| Куда жаловаться на заказчика? | Жалоба подаётся в ФАС через форму на портале. | 3 |
| Как выгрузить реестр сессий? | Выгрузка лежит в реестре котировочных сессий. | 2 |
"""

# Те же данные, но заголовков нет ни в одном списке синонимов: колонки должны
# определиться по содержимому — так приезжают выгрузки из чужих тикет-систем
TABLE_UNKNOWN_HEADERS = TABLE_CLASSIC.replace("| Вопрос | Ответ |", "| Обращение | Что делать |")

# Заголовки из реальной выгрузки поддержки
TABLE_SUPPORT_EXPORT = TABLE_CLASSIC.replace("| Вопрос | Ответ |", "| Текст обращения | Решение |")

# Так CSV сохраняет Excel с русской локалью: разделитель «;», ответ в кавычках
CSV_SEMICOLON = (
    "Вопрос;Ответ;Линия\n"
    "Как зарегистрироваться на портале?;Нужна электронная подпись и заявка в ЛК.;1\n"
    'Как подписать контракт?;"Откройте карточку закупки,\nнажмите «Подписать».";2\n'
    "Почему не приходит УПД?;Проверьте статус документа в разделе исполнения.;2\n"
    "Как сменить банковские реквизиты?;Реквизиты меняются в профиле организации.;1\n"
    "Куда жаловаться на заказчика?;Жалоба подаётся в ФАС через форму на портале.;3\n"
    "Как выгрузить реестр сессий?;Выгрузка лежит в реестре котировочных сессий.;2\n"
)

PROSE = """# Регламент работы с обращениями

Оператор первой линии принимает обращение и уточняет реквизиты организации.
Если вопрос требует проверки в реестре, обращение передаётся на вторую линию.
"""


def _blob(text: str, name: str = "qa.md") -> FileBlob:
    return FileBlob(name=name, data=text.encode("utf-8"))


def _table(rows: int) -> str:
    body = "".join(
        f"| Как выполнить шаг {i} на портале поставщиков? "
        f"| Инструкция по шагу {i} лежит в разделе помощи портала. |\n"
        for i in range(1, rows + 1)
    )
    return "| Вопрос | Ответ |\n|---|---|\n" + body


@pytest.mark.asyncio
async def test_prose_is_refused():
    """Проза без таблицы — не наш формат: порог реестра её отсекает."""
    confidence = await QaTableSource().sniff(_blob(PROSE, name="reglament.md"))
    assert confidence < MIN_CONFIDENCE


@pytest.mark.asyncio
async def test_workbooks_and_binaries_are_left_to_others():
    """Книгу Excel читает spreadsheet полистно, картинку — image_folder."""
    source = QaTableSource()
    assert await source.sniff(FileBlob("qa.xlsx", b"PK\x03\x04fake")) == 0.0
    assert await source.sniff(FileBlob("screen.png", b"\x89PNG\r\n\x1a\n")) == 0.0


@pytest.mark.asyncio
async def test_confidence_grows_with_rows_and_stops_at_ceiling():
    """Шесть строк — возможно табличка внутри методички, две сотни — точно выгрузка."""
    source = QaTableSource()
    small = await source.sniff(_blob(_table(6)))
    large = await source.sniff(_blob(_table(60)))
    huge = await source.sniff(_blob(_table(400)))
    assert MIN_CONFIDENCE < small < large
    assert huge == pytest.approx(0.95)


@pytest.mark.asyncio
async def test_records_are_authored_with_row_locator():
    """Текст написан человеком — значит authored, и видно, из какой строки он взят."""
    source = QaTableSource()
    blob = _blob(TABLE_CLASSIC, name="export.md")
    assert await source.sniff(blob) > 0.6

    records = [record async for record in source.parse(blob, IngestContext())]

    assert len(records) == 6
    assert all(record.origin.authored is True for record in records)
    assert all(record.origin.source_name == "qa_table" for record in records)
    assert all(record.origin.file == "export.md" for record in records)
    assert [record.origin.locator for record in records[:2]] == ["строка 1", "строка 2"]
    assert records[0].question == "Как зарегистрироваться на портале?"
    assert records[0].answer == "Нужна электронная подпись и заявка в личном кабинете."


@pytest.mark.asyncio
async def test_headers_outside_synonym_list_are_split_by_content():
    """«Обращение» и «Что делать» — не синонимы из словаря, решает содержимое колонок."""
    records = [
        record async for record in QaTableSource().parse(_blob(TABLE_UNKNOWN_HEADERS), IngestContext())
    ]

    assert len(records) == 6
    assert all(record.question.endswith("?") for record in records)
    assert records[2].answer == "Проверьте статус документа в разделе исполнения."


@pytest.mark.asyncio
async def test_support_export_headers_are_parsed():
    """Выгрузка поддержки «Текст обращения» / «Решение» обязана разбираться.

    Заголовок «Текст обращения» — это вопрос, но слово «текст» стоит в синонимах
    колонки ответа и срабатывает первым. Схлопнись обе колонки в одну, вопрос
    совпал бы с ответом, строки отбросились бы как пустые, и вся выгрузка молча
    уехала бы в запасной разбор моделью.
    """
    blob = _blob(TABLE_SUPPORT_EXPORT, name="support.md")
    assert await QaTableSource().sniff(blob) > MIN_CONFIDENCE

    records = [record async for record in QaTableSource().parse(blob, IngestContext())]

    assert len(records) == 6
    assert records[0].question == "Как зарегистрироваться на портале?"
    assert records[0].answer == "Нужна электронная подпись и заявка в личном кабинете."
    assert all(record.origin.authored is True for record in records)


@pytest.mark.asyncio
async def test_semicolon_csv_from_russian_excel_is_parsed():
    """CSV заявлен в форматах источника, а русский Excel пишет «;», а не запятую.

    markitdown разбирает только запятую: строку «Вопрос;Ответ» он кладёт в одну
    колонку, вопрос слипается с ответом, и готовая выгрузка молча уезжает в
    запасной разбор моделью — вместе с потерей authored.
    """
    source = QaTableSource()
    blob = _blob(CSV_SEMICOLON, name="Выгрузка БЗ.csv")
    assert await source.sniff(blob) > MIN_CONFIDENCE

    records = [record async for record in source.parse(blob, IngestContext())]

    assert len(records) == 6
    assert records[0].question == "Как зарегистрироваться на портале?"
    assert records[0].answer == "Нужна электронная подпись и заявка в ЛК."
    assert all(record.origin.authored is True for record in records)
    # Перенос строки внутри закавыченной ячейки доезжает до ответа переносом
    assert records[1].answer == "Откройте карточку закупки,\nнажмите «Подписать»."


@pytest.mark.asyncio
async def test_comma_csv_tsv_and_cp1251_are_parsed_too():
    """Запятая и табуляция — те же выгрузки; кодировку выбирает перебор в blob."""
    source = QaTableSource()
    plain = CSV_SEMICOLON.replace(
        '"Откройте карточку закупки,\nнажмите «Подписать»."',
        "Откройте карточку закупки и нажмите подписание.",
    )
    cases = [
        ("qa.csv", plain.replace(";", ",").encode("utf-8")),
        ("qa.tsv", plain.replace(";", "\t").encode("utf-8")),
        ("qa1251.csv", plain.encode("cp1251")),
    ]
    for name, data in cases:
        blob = FileBlob(name=name, data=data)
        assert await source.sniff(blob) > MIN_CONFIDENCE, name
        records = [record async for record in source.parse(blob, IngestContext())]
        assert len(records) == 6, name
        assert records[0].question == "Как зарегистрироваться на портале?", name


@pytest.mark.asyncio
async def test_single_column_csv_is_not_a_table():
    """Список вопросов без ответов — не выгрузка: пусть его режет разбор документа."""
    text = "Вопрос\n" + "".join(f"Как выполнить шаг {i} на портале?\n" for i in range(1, 9))
    assert await QaTableSource().sniff(_blob(text, name="list.csv")) < MIN_CONFIDENCE


@pytest.mark.asyncio
async def test_category_and_audience_come_from_params_line_from_the_table():
    """Категорию и роль задаёт загрузка, а линию — сама выгрузка, если та её знает.

    Значение из формы — умолчание на весь документ, колонка «Линия» — правда про
    конкретную строку. Частное сильнее общего: иначе выгрузка, где линии уже
    расставлены людьми, вся уехала бы в одну очередь.
    """
    ctx = IngestContext(params={"category": "Регистрация", "line": 2, "audience": "supplier"})

    records = [record async for record in QaTableSource().parse(_blob(TABLE_CLASSIC), ctx)]

    assert {record.category for record in records} == {"Регистрация"}
    assert [record.line for record in records] == [1, 2, 2, 1, 3, 2]
    assert {record.audience for record in records} == {"supplier"}


@pytest.mark.asyncio
async def test_line_falls_back_to_params_and_then_to_classifier():
    """Нет колонки «Линия» — берём значение формы; нет и его — решит классификатор."""
    without_line = "\n".join(
        line.rsplit("|", 2)[0] + "|" if line.strip().startswith("|") else line
        for line in TABLE_CLASSIC.strip().split("\n")
    ).replace("|---|---|", "|---|---|")

    ctx = IngestContext(params={"line": 3})
    records = [record async for record in QaTableSource().parse(_blob(without_line), ctx)]
    assert records, "таблица без колонки линии обязана разбираться"
    assert {record.line for record in records} == {3}

    empty = [record async for record in QaTableSource().parse(_blob(without_line), IngestContext())]
    assert empty[0].line is None


@pytest.mark.asyncio
async def test_defaults_when_params_are_empty():
    """Без параметров категория пустая, аудитория — все."""
    records = [record async for record in QaTableSource().parse(_blob(TABLE_CLASSIC), IngestContext())]

    assert records[0].category == ""
    assert records[0].audience == "all"


@pytest.mark.asyncio
async def test_prose_yields_nothing():
    """Если источник всё-таки позвали на прозе, он молча не даёт записей.

    Реестр трактует пустую выдачу как отказ и идёт к следующему источнику —
    это дешевле исключения и не роняет разбор всей папки.
    """
    records = [record async for record in QaTableSource().parse(_blob(PROSE), IngestContext())]

    assert records == []


@pytest.mark.asyncio
@pytest.mark.parametrize("name,delimiter", [("qa.csv", ","), ("qa.csv", ";"), ("qa.tsv", "\t")])
@pytest.mark.parametrize("answers", [["Срок — 10 дней."], ["Срок — 10 дней.", "Срок — 20 дней."]])
async def test_small_explicit_csv_preserves_each_authored_answer(name, delimiter, answers):
    question = "Какой срок?"
    text = delimiter.join(["Вопрос", "Ответ"]) + "\n"
    text += "\n".join(delimiter.join([question, answer]) for answer in answers)
    source = QaTableSource()
    blob = _blob(text, name=name)

    assert await source.sniff(blob) > MIN_CONFIDENCE
    records = [record async for record in source.parse(blob, IngestContext())]

    assert [record.question for record in records] == [question] * len(answers)
    assert [record.answer for record in records] == answers
    assert all(record.origin.authored for record in records)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "name,text",
    [
        ("qa.md", "| Вопрос | Ответ |\n|---|---|\n| Какой срок? | Срок — 10 дней. |"),
        ("qa.csv", "Название,Описание\nКакой срок?,Срок — 10 дней."),
        ("qa.csv", "Вопрос,Ответ\nКакой срок?,"),
    ],
)
async def test_small_non_explicit_or_empty_tables_still_refused(name, text):
    source = QaTableSource()
    blob = _blob(text, name=name)
    assert await source.sniff(blob) < MIN_CONFIDENCE
    assert [record async for record in source.parse(blob, IngestContext())] == []
