"""Колонтитулы PDF: убрать шапку и номера страниц, не тронув текст документа.

Тексты синтетические, но повторяют то, что markitdown отдаёт на реальных файлах:
выгрузку КонсультантПлюс с шапкой приказа на каждой странице и инструкцию, где
от оформления остаётся только номер страницы отдельной строкой.
"""

import pytest

from app.adapters.docparse import ParsedDocument
from app.ingest.blob import FileBlob
from app.ingest.boilerplate import boilerplate_report, estimate_pages, removed_summary, strip_boilerplate
from app.ingest.records import ArticleRecord
from app.ingest.registry import IngestContext
from app.ingest.sources.pdf_figures import PdfFiguresSource

HEADER = [
    "Приказ Тендерного комитета г. Москвы от 14.02.2024 N 70-01-17/24",
    "Документ предоставлен КонсультантПлюс",
    "(ред. от 10.06.2025)",
    '"Об утверждении Регламента информа...',
    "Дата сохранения: 15.07.2025",
]


def consultant_document(bodies: list[list[str]]) -> str:
    """Страницы выгрузки КонсультантПлюс: шапка, тело, подвал с номером страницы."""
    total = len(bodies)
    pages = []
    for number, body in enumerate(bodies, start=1):
        footer = [
            "КонсультантПлюс",
            f"www.consultant.ru Страница {number} из {total}",
            "надежная правовая поддержка",
        ]
        pages.append("\n".join([*HEADER, *body, *footer]))
    return "\n\n".join(pages)


def clause_pages(count: int) -> list[list[str]]:
    return [
        [f"{n}.1. Пункт номер {n} регламента описывает порядок действий участника."]
        for n in range(1, count + 1)
    ]


def test_running_header_and_footer_are_removed():
    text = consultant_document(clause_pages(20))

    clean = strip_boilerplate(text)

    for line in [*HEADER, "КонсультантПлюс", "надежная правовая поддержка"]:
        assert line not in clean.split("\n")
    assert "Страница" not in clean
    # Тело документа на месте целиком
    for n in range(1, 21):
        assert f"{n}.1. Пункт номер {n} регламента" in clean


def test_sentence_cut_by_page_break_is_stitched_back():
    """Фраза, разорванная колонтитулом, должна склеиться без пустой строки посередине.

    Иначе нарезка по абзацам разрежет пункт надвое ровно по шву страницы.
    """
    bodies = clause_pages(12)
    bodies[4] = ['1.1.3. СКК - служба контроля качества работы АИС "Портал']
    bodies[5] = ['поставщиков".', "1.1.4. СТЕ - стандартная товарная единица."]

    clean = strip_boilerplate(consultant_document(bodies))

    assert '1.1.3. СКК - служба контроля качества работы АИС "Портал\nпоставщиков".' in clean


def test_finished_sentence_keeps_paragraph_break():
    bodies = clause_pages(12)
    clean = strip_boilerplate(consultant_document(bodies))
    # Пункт кончился точкой, следующий начинается с заглавной — шов остаётся абзацем
    assert "описывает порядок действий участника.\n\n2.1. Пункт номер 2" in clean


def test_bare_page_numbers_go_but_year_on_cover_stays():
    pages = [f"Шаг {n}. Откройте раздел и нажмите кнопку «Сохранить» на странице.\n{n}" for n in range(1, 40)]
    text = "Инструкция по работе с Порталом\nМосква\n2026\n\n" + "\n\n".join(pages)

    clean, removed = boilerplate_report(text)

    assert "2026" in clean.split("\n")
    assert all(line.isdigit() for line in removed)
    assert len(removed) == 39
    assert "Шаг 39. Откройте раздел" in clean


def test_figure_references_with_different_numbers_are_content():
    """«(Рисунок 26).» и «(Рисунок 27).» — разные строки, хоть их и десятки по документу."""
    pages = [f"Нажмите кнопку «Подписать»\n(Рисунок {n}).\n{n}" for n in range(1, 60)]

    clean = strip_boilerplate("\n\n".join(pages))

    assert all(f"(Рисунок {n})." in clean for n in range(1, 60))


def test_repeated_list_items_and_tables_are_kept():
    """Повтор разметки — структура: пункты фильтра, строки таблиц, заглушки картинок."""
    page = "\n".join(
        [
            "Фильтр по статусу:",
            "− Все;",
            "| --- | --- |",
            "| Поле | Значение |",
            "<!-- image -->",
            "Выберите нужное значение из списка.",
        ]
    )
    text = "\n\n".join(f"{page}\n{n}" for n in range(1, 30))

    clean = strip_boilerplate(text)

    assert clean.count("− Все;") == 29
    assert clean.count("| --- | --- |") == 29
    assert clean.count("<!-- image -->") == 29


def test_repeat_confined_to_one_chapter_is_not_a_header():
    """Колонтитул идёт через весь документ; повтор в одной главе — это текст главы."""
    chapter = ["Статус «Черновик» означает, что оферта ещё не подписана."] * 15
    body = [f"Абзац номер {n} о другой функции портала." for n in range(200)]
    text = "\n".join(body[:100] + chapter + body[100:])

    clean = strip_boilerplate(text)

    assert clean.count("Статус «Черновик» означает") == 15


def test_without_page_marks_rare_repeat_stays():
    """Страниц не видно — «на каждой ли странице» строка, не оценить; порог выше."""
    lines = []
    for n in range(8):
        lines += ["Инструкция поставщика", f"Раздел {n}: текст раздела про закупки."]
    clean = strip_boilerplate("\n".join(lines))
    assert clean.count("Инструкция поставщика") == 8


def test_numbered_headings_are_structure_not_header():
    """«Приложение 1» … «Приложение 6» без номера по краю — одна строка, и шесть
    её повторов на коротком акте выглядели колонтитулом: регламент терял границы
    всех приложений разом."""
    bodies = clause_pages(12)
    for n in range(1, 7):
        bodies[n * 2 - 1] = [f"Приложение {n}", "к Регламенту информационного", *bodies[n * 2 - 1]]

    clean = strip_boilerplate(consultant_document(bodies))

    for n in range(1, 7):
        assert f"Приложение {n}" in clean.split("\n")


def test_markdown_repeat_is_content_not_header():
    """У .md и .docx колонтитулов в тексте нет, и повтор там — содержание: фраза
    «обратитесь в поддержку» в конце каждого раздела нужна в каждом ответе."""
    advice = "Если ошибка сохраняется, обратитесь в службу технической поддержки."
    sections = [
        f"## Ошибка {n} при подписании\n\nПроверьте сертификат электронной подписи номер {n}.\n{advice}"
        for n in range(1, 15)
    ]

    clean = strip_boilerplate("\n\n".join(sections))

    assert clean.count(advice) == 14


def test_table_row_number_out_of_page_sequence_stays():
    """«14» из таблицы сокращений, развёрнутой в строки, — не номер страницы:
    между страницами 6 и 7 он из последовательности выбивается."""
    pages = [f"Раздел {n}. Описание шага инструкции.\n{n}" for n in range(1, 11)]
    table = (
        "13 УКД Универсальный корректировочный документ\n14\nУКД, УКДи\n"
        "15 УПД Универсальный передаточный документ"
    )
    pages.insert(6, table)

    clean, removed = boilerplate_report("\n".join(pages))

    assert "14" in clean.split("\n")
    assert sorted(int(line) for line in removed) == list(range(1, 11))


def test_page_number_next_to_page_break_goes():
    """У разрыва страницы число — номер страницы, даже если нумерация сбилась."""
    text = "Первая страница текста.\n40\fВторая страница текста.\n41"
    clean, removed = boilerplate_report(text)
    assert removed == ["40", "41"]
    assert "Вторая страница текста." in clean


def test_text_without_boilerplate_is_returned_as_is():
    text = "# Регистрация\n\nДля регистрации нужна электронная подпись.\n"
    assert strip_boilerplate(text) == text


class _FakeDocParse:
    def __init__(self, markdown: str) -> None:
        self.markdown = markdown

    async def parse(self, filename: str, raw: bytes, *, do_ocr: bool = False):
        return ParsedDocument(markdown=self.markdown, pictures=[])


class _FakeRuntime:
    def __init__(self, markdown: str) -> None:
        self.docparse = _FakeDocParse(markdown)


@pytest.mark.asyncio
async def test_pdf_figures_strips_page_numbers_too():
    """Путь через docling чистится так же, как через markitdown: документ один."""
    step = "Откройте карточку контракта и нажмите кнопку «Подписать» на шаге"
    pages = [f"## Раздел {n}\n\n{step} {n}.\n\n{n}\n\n<!-- image -->" for n in range(1, 25)]
    ctx = IngestContext(rt=_FakeRuntime("\n\n".join(pages)))

    records = [r async for r in PdfFiguresSource().parse(FileBlob("instrukciya.pdf", b"%PDF-1.7"), ctx)]

    answers = [r.answer for r in records if isinstance(r, ArticleRecord)]
    assert answers
    for answer in answers:
        assert not any(line.strip().isdigit() for line in answer.split("\n"))
    # Заглушки картинок docling снимаются до чистки: картинка едет отдельной
    # записью, а пометка в тексте доходила до ответа человеку. Текст шагов — весь
    assert not any("<!--" in answer for answer in answers)
    assert sum(answer.count(step) for answer in answers) == 24


def test_page_estimate_and_summary():
    text = consultant_document(clause_pages(15))
    assert estimate_pages(text) == 15
    _, removed = boilerplate_report(text)
    summary = dict(removed_summary(removed, limit=50))
    # Номера страниц в отчёте свёрнуты в одну строку, а не в полсотни разных
    assert summary["www.consultant.ru Страница # из"] == 15
    assert summary["Документ предоставлен КонсультантПлюс"] == 15
