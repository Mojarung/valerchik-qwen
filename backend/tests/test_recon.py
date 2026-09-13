"""Разведчик: первое, что запускается, когда выложили архив с данными."""

import io
import json
import sys
import zipfile

import pytest

from app.ingest.records import ArticleRecord, Provenance
from app.ingest.registry import SourceMismatch, registry
from app.tools.recon import FileReport, inspect_path, main, render

TABLE = (
    "| Вопрос | Ответ |\n|---|---|\n"
    "| Как зарегистрироваться на портале? | Нужна электронная подпись и заявка. |\n"
    "| Как подписать контракт? | Откройте карточку и нажмите «Подписать». |\n"
    "| Где найти УПД? | В разделе «Исполнение контракта». |\n"
    "| Как сменить банковские реквизиты? | В профиле организации. |\n"
    "| Куда жаловаться на заказчика? | В ФАС через форму на портале. |\n"
    "| Как выгрузить реестр сессий? | Кнопкой «Экспорт» в реестре. |\n"
)
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


@pytest.mark.asyncio
async def test_report_names_source_counts_and_samples(tmp_path):
    (tmp_path / "qa.md").write_text(TABLE, encoding="utf-8")
    (tmp_path / "kak-vygruzit-upd.png").write_bytes(PNG)

    reports = {report.name: report for report in await inspect_path(tmp_path)}

    qa = reports["qa.md"]
    assert qa.source == "qa_table"
    assert qa.counts["ArticleRecord"] == 6
    assert qa.samples and "→" in qa.samples[0]
    assert reports["kak-vygruzit-upd.png"].source == "image_folder"

    text = render(list(reports.values()))
    assert "qa.md" in text
    assert "qa_table" in text


@pytest.mark.asyncio
async def test_cp1251_survives(tmp_path):
    """Выгрузки из старых систем приезжают не в utf-8, и это не повод их терять."""
    (tmp_path / "old.md").write_bytes(TABLE.encode("cp1251"))
    report = (await inspect_path(tmp_path))[0]
    assert report.counts.get("ArticleRecord") == 6


@pytest.mark.asyncio
async def test_unreadable_file_is_reported_not_raised(tmp_path):
    """Разведка не имеет права падать: непонятный файл — это её результат."""
    (tmp_path / "junk.bin").write_bytes(b"\x00\x01\x02\x03" * 10)
    report = (await inspect_path(tmp_path))[0]
    assert report.parsed is False
    assert report.note
    assert "НЕ РАЗОБРАНО" in render([report])


@pytest.mark.asyncio
async def test_zip_is_inspected_without_unpacking(tmp_path):
    """Организаторы выкладывают архив — распаковывать руками не должно быть нужно."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("data/qa.md", TABLE)
        archive.writestr(
            "data/faq.json",
            json.dumps([{"question": "Как войти?", "answer": "Через ЭП."}], ensure_ascii=False),
        )
    archive_path = tmp_path / "hackathon.zip"
    archive_path.write_bytes(buffer.getvalue())

    names = {report.name for report in await inspect_path(archive_path)}
    assert names == {"qa.md", "faq.json"}


def test_report_survives_non_utf8_stdout(tmp_path, monkeypatch):
    """`recon данные > отчёт.txt`: у перенаправленного stdout кодировка локали.

    Отчёт сам вставляет «→» между вопросом и ответом, а в cp1251 этого символа нет.
    Без своей кодировки самая ходовая команда дня выдачи данных падала
    UnicodeEncodeError и не оставляла ни строки — а тесты рендера были зелёные,
    потому что до записи в поток дело не доходило.
    """
    (tmp_path / "qa.md").write_text(TABLE, encoding="utf-8")
    raw = io.BytesIO()
    redirected = io.TextIOWrapper(raw, encoding="cp1251", newline="")
    monkeypatch.setattr(sys, "stdout", redirected)

    code = main([str(tmp_path)])
    redirected.flush()

    assert code == 0
    assert "→" in raw.getvalue().decode("utf-8")


@pytest.mark.asyncio
async def test_layout_reports_rows_on_input(tmp_path):
    """Строки, отброшенные без ответа, видны только рядом с числом строк на входе.

    Половину пар источник выкидывает по условию «есть и вопрос, и ответ» — молча,
    без счётчика. «Статьи × 6» из файла на 12 строк читается как успех, пока в
    отчёте нет второго числа.
    """
    rows = ["Вопрос;Ответ"]
    for i in range(6):
        rows.append(f"Как подписать документ номер {i}?;Откройте карточку и нажмите «Подписать».")
        rows.append(f"Что делать с обращением номер {i}?;")
    (tmp_path / "half.csv").write_bytes(("\r\n".join(rows) + "\r\n").encode("utf-8"))

    report = (await inspect_path(tmp_path))[0]

    assert report.counts["ArticleRecord"] == 6
    assert ("строк с данными", 12) in report.layout
    assert "на входе: строк с данными — 12" in render([report])


@pytest.fixture
def only_stub_sources():
    """Реестр глобальный — тест обязан вернуть его в исходное состояние."""
    saved = list(registry.sources)
    was_loaded = registry._loaded
    registry.sources.clear()
    registry._loaded = True
    yield registry
    registry.sources[:] = saved
    registry._loaded = was_loaded


def _source(name: str, confidence: float, *, refuses: bool):
    class Stub:
        pass

    Stub.name = name
    Stub.priority = 0

    async def sniff(self, blob):
        return confidence

    async def parse(self, blob, ctx):
        if refuses:
            raise SourceMismatch("сервис разбора не поднят")
        yield ArticleRecord(
            question="Как войти?",
            answer="Через электронную подпись.",
            origin=Provenance(file=blob.name, source_name=name, authored=False),
        )

    Stub.sniff = sniff
    Stub.parse = parse
    return Stub()


@pytest.mark.asyncio
async def test_report_names_the_source_that_actually_parsed(tmp_path, only_stub_sources):
    """Самый уверенный источник может уступить файл: в шапке должен стоять разобравший.

    Так ведёт себя pdf_figures при неподнятом сервисе разбора: уверенность 0.85,
    отказ в parse, и методичку забирает document — с authored=False вместо True.
    Показать в отчёте первого в очереди значит соврать про происхождение записей.
    """
    only_stub_sources.add(_source("greedy", 0.9, refuses=True))
    only_stub_sources.add(_source("real", 0.4, refuses=False))
    (tmp_path / "manual.pdf").write_bytes(b"%PDF-1.4 fake")

    report = (await inspect_path(tmp_path))[0]

    assert report.source == "real"
    assert report.confidence == pytest.approx(0.4)
    assert any("greedy" in hint and "отказался" in hint for hint in report.hints)


def test_render_lays_out_every_part_of_a_report():
    """Формат отчёта целиком. Его читают глазами в первые минуты после выдачи
    данных, поэтому у каждой строки своё место: итог, файл за файлом, а в конце —
    неразобранное, сгруппированное по формату."""
    parsed = FileReport(
        name="faq.xlsx",
        size=2048,
        suffix=".xlsx",
        source="spreadsheet",
        confidence=0.9,
        counts={"ArticleRecord": 12, "FeedbackRecord": 3},
        samples=["Как войти? → через ЕСИА"],
        note="две строки без ответа",
        layout=[("Вопросы", 12), ("Отзывы", 3)],
        hints=["пропущено строк: 2"],
    )
    # Самая большая группа по алфавиту стоит не первой: блок упорядочен по числу
    # файлов, а при равенстве — по расширению
    scans = [FileReport(name=f"scan{i}.tiff", size=10, suffix=".tiff") for i in range(7)]
    book = FileReport(name="book.djvu", size=10, suffix=".djvu")
    readme = FileReport(name="README", size=1, suffix="")

    assert render([parsed, *scans, book, readme]).splitlines() == [
        "Файлов: 10",
        "Всего записей: статьи — 12, отзывы — 3",
        "",
        "  faq.xlsx  (2 КБ)  → spreadsheet [0.90]",
        "      статьи × 12, отзывы × 3",
        "      на входе: Вопросы — 12, Отзывы — 3",
        "      · Как войти? → через ЕСИА",
        "      ~ пропущено строк: 2",
        "      ! две строки без ответа",
        *[f"  scan{i}.tiff  (0 КБ)" for i in range(7)],
        "  book.djvu  (0 КБ)",
        "  README  (0 КБ)",
        "",
        "НЕ РАЗОБРАНО (нужен новый источник) — файлов 9:",
        "  .tiff × 7: scan0.tiff, scan1.tiff, scan2.tiff, scan3.tiff, scan4.tiff и ещё 2",
        "  .djvu × 1: book.djvu",
        "  без расширения × 1: README",
    ]
