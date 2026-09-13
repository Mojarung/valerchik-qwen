"""Разведка выданных данных: что нам дали и чем мы это прочитаем.

    uv run python -m app.tools.recon <папка|файл|zip> [--json] [--limit N]

Первое, что запускается, когда организаторы выкладывают архив. Ничего не пишет в
базу и не требует поднятого стенда: только смотрит, кто из источников возьмётся
за файл и что из него выйдет. Отдельным блоком показывает то, что не разобрал
никто, — это и есть список форматов, под которые нужен новый источник.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import zipfile
from collections import Counter
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from itertools import islice
from pathlib import Path
from typing import TYPE_CHECKING, Any

from app.ingest import sources  # noqa: F401 — импорт регистрирует источники
from app.ingest.blob import SHEET_SUFFIXES, FileBlob
from app.ingest.records import ArticleRecord, AssetRecord, DialogRecord, FeedbackRecord
from app.ingest.registry import IngestContext, parse_blob, registry
from app.ingest.sources.qa_table import CSV_SUFFIXES

if TYPE_CHECKING:
    from app.runtime import AppRuntime

MAX_SAMPLES = 3
SAMPLE_CHARS = 70
# Сколько имён показывать в блоке «не разобрано» на каждый формат. Пятьсот файлов
# дают полторы сотни неразобранных, и простыня имён в одну строку — это ровно то,
# что нельзя прочитать в первые минуты после выдачи данных.
MAX_UNREAD_NAMES = 5
NO_SUFFIX = "без расширения"
SKIP_DIRS = {".git", "__pycache__", ".venv", "node_modules", ".idea", ".vscode"}
KIND_LABELS = {
    ArticleRecord.__name__: "статьи",
    FeedbackRecord.__name__: "отзывы",
    DialogRecord.__name__: "диалоги",
    AssetRecord.__name__: "картинки",
}
# Строки логов разбора, каждая из которых означает потерю: строку выкинули, лист
# не опознали, источник отказался. Разведка обязана показать их в отчёте, иначе
# «статьи × 12» из книги на 24 пары читается как успех.
LOSS_MARKERS = ("пропущено", "не дал записей", "отказался", "упал")
MAX_HINTS = 4


@dataclass
class FileReport:
    name: str
    size: int
    suffix: str
    source: str = ""
    confidence: float = 0.0
    counts: dict[str, int] = field(default_factory=dict)
    samples: list[str] = field(default_factory=list)
    note: str = ""
    # Что было на входе: [(лист, строк)] для книги, [("строк с данными", N)] для
    # csv. Без этой строки «статьи × 6» из файла на 12 пар читается как успех:
    # строки, у которых пуста половина пары, источники выкидывают без единого лога.
    layout: list[tuple[str, int]] = field(default_factory=list)
    # Что сказали источники по дороге: отказы и пропущенные строки
    hints: list[str] = field(default_factory=list)

    @property
    def parsed(self) -> bool:
        return bool(self.counts)

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "size": self.size,
            "suffix": self.suffix,
            "source": self.source,
            "confidence": round(self.confidence, 2),
            "counts": self.counts,
            "samples": self.samples,
            "note": self.note,
            "layout": [list(part) for part in self.layout],
            "hints": self.hints,
        }


class _LogCapture(logging.Handler):
    """Логи разбора не в консоль, а в отчёт по файлу."""

    def __init__(self) -> None:
        super().__init__(level=logging.INFO)
        self.lines: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.lines.append(record.getMessage())
        except Exception:  # noqa: BLE001 — разведка не имеет права падать на логе
            pass


@contextmanager
def _captured_logs() -> Iterator[list[str]]:
    """Перехват логов разбора вместо их глушения.

    Раньше разведка гасила логи целиком, а вместе с ними — единственную строку,
    сообщавшую, что из выгрузки выкинули половину строк или что лист книги не
    опознан. Молчание в этом месте выглядит как успешный разбор.
    """
    handler = _LogCapture()
    logger = logging.getLogger("app.ingest")
    level, propagate = logger.level, logger.propagate
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    try:
        yield handler.lines
    finally:
        logger.removeHandler(handler)
        logger.setLevel(level)
        logger.propagate = propagate


def _layout_of(blob: FileBlob) -> list[tuple[str, int]]:
    """Что лежало на входе, посчитанное независимо от источников.

    Смысл в сверке: источник отбрасывает строку без ответа и лист, вид которого не
    опознал, — и оба раза молча. Сравнить «строк на входе» с «записей на выходе»
    человек может только если первое число вообще показано.
    """
    if blob.suffix in SHEET_SUFFIXES:
        try:
            return [(name, len(frame)) for name, frame in blob.tables]
        except Exception:  # noqa: BLE001 — битую книгу разберёт (или нет) сам источник
            return []
    if blob.suffix in CSV_SUFFIXES:
        try:
            lines = [line for line in blob.decoded().splitlines() if line.strip()]
        except Exception:  # noqa: BLE001 — нечитаемый файл это забота источника
            return []
        return [("строк с данными", max(0, len(lines) - 1))]
    return []


def _oneline(text: str) -> str:
    """Пример в одну строку.

    Разбор таблицы кладёт в вопрос markdown с переводами строк, и такой пример
    разваливает выравнивание отчёта на шесть строк — а отчёт читают глазами и
    быстро, сравнивая файлы между собой.
    """
    return " ".join(str(text).split())[:SAMPLE_CHARS]


async def inspect_blob(blob: FileBlob, rt: AppRuntime | None = None) -> FileReport:
    """Один файл: кто возьмётся, сколько записей выйдет, три примера."""
    report = FileReport(name=blob.name, size=len(blob.data), suffix=blob.suffix)
    with _captured_logs() as logged:
        ranked = await registry.ranked(blob)
        if not ranked:
            report.note = "ни один источник не взялся"
            return report
        report.confidence, source = ranked[0]
        report.source = source.name
        confidence_of = {found.name: value for value, found in ranked}
        report.layout = _layout_of(blob)

        try:
            records = await parse_blob(blob, IngestContext(rt=rt))
        except Exception as exc:  # noqa: BLE001 — разведка не имеет права падать
            report.note = f"разбор упал: {type(exc).__name__}: {exc}"
            return report
        finally:
            report.hints = [
                line for line in dict.fromkeys(logged) if any(marker in line for marker in LOSS_MARKERS)
            ][:MAX_HINTS]

    # Кто действительно разобрал, а не кто был первым в очереди: pdf_figures
    # уверен сильнее всех, но при неподнятом сервисе разбора уступает файл
    # document — и записи приезжают с authored=False, чего в шапке отчёта было не
    # видно. Провенанс записи знает это точно, ранжирование — только предполагает.
    if records and records[0].origin.source_name:
        report.source = records[0].origin.source_name
        report.confidence = confidence_of.get(report.source, report.confidence)
    report.counts = dict(Counter(type(record).__name__ for record in records))
    for record in records:
        if len(report.samples) >= MAX_SAMPLES:
            break
        if isinstance(record, ArticleRecord):
            answer = record.answer.strip() or "«ответ картинкой»"
            report.samples.append(f"{_oneline(record.question)} → {_oneline(answer)}")
        elif isinstance(record, FeedbackRecord):
            report.samples.append(f"оценка {record.rating}: {_oneline(record.comment)}")
    if not records:
        report.note = "источник не дал ни одной записи"
    return report


def _blobs_from(path: Path, limit: int | None = None) -> Iterator[FileBlob]:
    """Папка, файл или zip → файлы для разведки, по одному.

    Генератор, а не список: выданный архив весит гигабайты, и «сначала прочитать
    всю папку, потом смотреть» означало бы держать в памяти всю выдачу целиком —
    на машине с 16 ГБ это падение вместо отчёта. Архив тоже читаем прямо с диска,
    а не через копию всех байтов в памяти.
    """
    if path.is_file() and path.suffix.lower() == ".zip":
        with zipfile.ZipFile(path) as archive:
            names = [n for n in archive.namelist() if not n.endswith("/")]
            for name in names[:limit]:
                yield FileBlob(name=Path(name).name, data=archive.read(name))
        return
    if path.is_file():
        yield FileBlob(name=path.name, data=path.read_bytes())
        return
    files = (
        item
        for item in sorted(path.rglob("*"))
        if item.is_file() and not any(part in SKIP_DIRS for part in item.parts)
    )
    for item in islice(files, limit):
        yield FileBlob(name=item.name, data=item.read_bytes())


async def inspect_path(
    path: Path | str, rt: AppRuntime | None = None, limit: int | None = None
) -> list[FileReport]:
    reports = []
    for blob in _blobs_from(Path(path), limit):
        reports.append(await inspect_blob(blob, rt))
    return reports


def _file_lines(report: FileReport) -> list[str]:
    """Блок одного файла: что это, кто его разобрал и что из этого вышло."""
    head = f"  {report.name}  ({report.size / 1024:.0f} КБ)"
    if report.source:
        head += f"  → {report.source} [{report.confidence:.2f}]"
    lines = [head]
    if report.counts:
        lines.append(
            "      "
            + ", ".join(f"{KIND_LABELS.get(kind, kind)} × {count}" for kind, count in report.counts.items())
        )
    if report.layout:
        lines.append("      на входе: " + ", ".join(f"{name} — {rows}" for name, rows in report.layout))
    lines += [f"      · {sample}" for sample in report.samples]
    lines += [f"      ~ {hint}" for hint in report.hints]
    if report.note:
        lines.append(f"      ! {report.note}")
    return lines


def _unread_lines(reports: list[FileReport]) -> list[str]:
    """Неразобранное, сгруппированное по формату. Группируем, а не перечисляем
    имена: блок отвечает на вопрос «под что писать источник», и ответ — это
    расширение, а не сто имён файлов."""
    unread = [report for report in reports if not report.parsed]
    if not unread:
        return []
    by_suffix: dict[str, list[str]] = {}
    for report in unread:
        by_suffix.setdefault(report.suffix or NO_SUFFIX, []).append(report.name)
    lines = ["", f"НЕ РАЗОБРАНО (нужен новый источник) — файлов {len(unread)}:"]
    for suffix, names in sorted(by_suffix.items(), key=lambda kv: (-len(kv[1]), kv[0])):
        shown = ", ".join(names[:MAX_UNREAD_NAMES])
        tail = f" и ещё {len(names) - MAX_UNREAD_NAMES}" if len(names) > MAX_UNREAD_NAMES else ""
        lines.append(f"  {suffix} × {len(names)}: {shown}{tail}")
    return lines


def render(reports: list[FileReport]) -> str:
    """Человекочитаемый отчёт: его читают в первые минуты после выдачи данных."""
    totals: Counter[str] = Counter()
    for report in reports:
        totals.update(report.counts)

    lines = [f"Файлов: {len(reports)}"]
    if totals:
        lines.append(
            "Всего записей: "
            + ", ".join(f"{KIND_LABELS.get(kind, kind)} — {count}" for kind, count in totals.items())
        )
    lines.append("")
    for report in reports:
        lines += _file_lines(report)
    lines += _unread_lines(reports)
    return "\n".join(lines)


def _utf8_output() -> None:
    """Вывод только в utf-8, чем бы он ни оказался.

    На Windows перенаправленный stdout берёт кодировку локали (cp1251), а в отчёте
    есть стрелка «→» — её же и вставляет сам рендер. Без этой строки самая ходовая
    команда дня выдачи данных, `recon data > recon.txt`, падала UnicodeEncodeError
    и не оставляла ни строки отчёта.
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
        except AttributeError, ValueError, OSError:  # pragma: no cover — не файловый поток
            pass


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="recon",
        description="Разведка выданных данных: чем мы это прочитаем",
    )
    parser.add_argument("path", type=Path, help="папка, файл или zip-архив")
    parser.add_argument("--json", action="store_true", help="машиночитаемый вывод")
    parser.add_argument("--limit", type=int, default=None, help="сколько файлов смотреть")
    args = parser.parse_args(argv)

    _utf8_output()
    if not args.path.exists():
        print(f"Нет такого пути: {args.path}", file=sys.stderr)
        return 2

    # Логи источников в консоли только мешают: нас интересует итог, а не каждый
    # отказ. Но глушить их нельзя — из них берутся подсказки о потерянных строках,
    # поэтому записи продолжают создаваться, а перехватывает их inspect_blob.
    logging.basicConfig(level=logging.WARNING, handlers=[logging.NullHandler()], force=True)
    reports = asyncio.run(inspect_path(args.path, limit=args.limit))
    if args.json:
        print(json.dumps([report.as_dict() for report in reports], ensure_ascii=False, indent=2))
    else:
        print(render(reports))
    return 0


if __name__ == "__main__":
    sys.exit(main())
