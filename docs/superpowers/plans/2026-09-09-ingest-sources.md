# Приём произвольных данных: план реализации

> **Для агентов-исполнителей:** ОБЯЗАТЕЛЬНЫЙ САБ-СКИЛЛ: `superpowers:subagent-driven-development`
> (рекомендуется) либо `superpowers:executing-plans`. Шаги отмечаются чекбоксами `- [ ]`.

**Цель:** любой файл, выданный организаторами хакатона, разбирается подходящим источником
в записи и попадает в систему; ответ на авторскую пару «вопрос — ответ» отдаётся дословно,
вместе с картинками.

**Архитектура:** источник (`sniff`/`parse`) отдаёт записи, конвейер их сохраняет. Реестр
выбирает источник по уверенности, последним в очереди стоит LLM-нормализатор. Логика
нынешнего `services/ingest.py` переезжает в модули пакета `app/ingest/`, а сам файл
остаётся фасадом ради существующих импортов и тестов.

**Стек:** Python 3.12, FastAPI, SQLAlchemy 2 (async), pytest + pytest-asyncio, markitdown,
pandas/openpyxl (уже в зависимостях транзитивно), pydantic-ai.

**Спека:** `docs/superpowers/specs/2026-09-09-ingest-sources-design.md`

## Общие ограничения

- Пакетный менеджер только `uv`. Тесты: `uv run pytest`, линт: `uv run ruff check .`.
- Работаем из `backend/`. Все пути в плане — относительно `backend/`.
- Новых зависимостей не добавляем: `markitdown[pdf,docx,xlsx,xls,pptx]`, `pypdf`, `pillow`,
  `numpy`, `pandas` 3.0.5, `openpyxl` 3.1.5 уже доступны.
- Комментарии и докстринги — по-русски, в стиле репозитория: объясняют «почему», а не «что».
- Публичные имена `app.services.ingest` (`split_sections`, `ingest_document`,
  `extract_document`, `extract_text_from_file`, `SUPPORTED_EXTENSIONS`, `parse_article_table`,
  `Section`, `figure_numbers`, `attach_images`, `store_pictures`, `strip_dangling_figures`,
  `fallback_question`, `generate_question`, `extract_keywords`) сохраняются — их импортируют
  `routers/kb.py`, `tasks/jobs.py` и тесты.
- Колонки в БД досинхронизируются сами (`app/db.py::_add_missing_columns`) — миграций писать
  не нужно, но новое поле обязано быть nullable или иметь default.
- Каждая задача заканчивается зелёным прогоном **всего** набора: `uv run pytest -q`.
  296 тестов — критерий того, что перенос ничего не сломал.
- Коммит после каждой задачи, Conventional Commits, тело — по-русски.

---

### Task 1: Записи и провенанс

**Файлы:**
- Создать: `app/ingest/__init__.py`
- Создать: `app/ingest/records.py`
- Изменить: `app/models.py:33-58` (поле `origin` у `KBArticle`)
- Тест: `tests/test_ingest_records.py`

**Интерфейсы:**
- Отдаёт: `Provenance`, `ArticleRecord`, `FeedbackRecord`, `DialogRecord`, `AssetRecord`,
  `ImageRef`, тип-объединение `Record`. Все последующие задачи оперируют этими типами.

- [ ] **Шаг 1: Написать падающий тест**

```python
# tests/test_ingest_records.py
from app.ingest.records import ArticleRecord, AssetRecord, Provenance


def test_article_record_defaults():
    rec = ArticleRecord(question="Как войти?", answer="Через ЭП.")
    assert rec.audience == "all"
    assert rec.images == []
    assert rec.origin.authored is False


def test_provenance_travels_with_record():
    origin = Provenance(file="qa.xlsx", locator="лист 1, строка 12",
                        source_name="qa_table", authored=True)
    rec = ArticleRecord(question="q", answer="a", origin=origin)
    assert rec.origin.authored is True
    assert rec.origin.as_dict()["locator"] == "лист 1, строка 12"


def test_asset_record_keeps_anchor():
    asset = AssetRecord(data=b"\x89PNG", caption="Рисунок 26",
                        anchor={"figure": "26", "page": 7})
    assert asset.anchor["figure"] == "26"
```

- [ ] **Шаг 2: Убедиться, что тест падает**

Запуск: `uv run pytest tests/test_ingest_records.py -q`
Ожидание: FAIL, `ModuleNotFoundError: No module named 'app.ingest'`

- [ ] **Шаг 3: Написать минимальную реализацию**

```python
# app/ingest/records.py
"""Записи — промежуточный формат между источником и базой.

Источник разбирает файл и отдаёт записи; сохранением занимается конвейер.
Раньше эти две работы жили в одной функции, и добавить новый формат означало
править её же. Провенанс едет с каждой записью: на защите нужно уметь показать,
из какой строки какого файла взялся ответ.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass(slots=True)
class Provenance:
    """Откуда приехала запись. `authored` решает судьбу дословного ответа."""

    file: str = ""
    locator: str = ""          # «лист 2, строка 145», «стр. 7», «$.items[3]»
    source_name: str = ""
    # Текст написан человеком (готовая пара «вопрос — ответ», ответ оператора),
    # а не собран нами из кусков документа. Только такой можно отдавать дословно.
    authored: bool = False

    def as_dict(self) -> dict:
        return {"file": self.file, "locator": self.locator,
                "source": self.source_name, "authored": self.authored}


@dataclass(slots=True)
class ImageRef:
    """Картинка, уже сохранённая в хранилище: то, что ложится в KBArticle.images."""

    url: str
    caption: str = ""
    page: int | None = None
    figure: str = ""

    def as_dict(self) -> dict:
        return {"url": self.url, "caption": self.caption,
                "page": self.page, "figure": self.figure}


@dataclass(slots=True)
class ArticleRecord:
    question: str
    answer: str
    category: str = ""
    line: int | None = None
    audience: str = "all"
    keywords: list[str] = field(default_factory=list)
    images: list[ImageRef] = field(default_factory=list)
    origin: Provenance = field(default_factory=Provenance)


@dataclass(slots=True)
class FeedbackRecord:
    rating: int
    comment: str = ""
    operator: str = ""
    created_at: datetime | None = None
    origin: Provenance = field(default_factory=Provenance)


@dataclass(slots=True)
class DialogRecord:
    """Лог чужого обращения: топливо для классификатора линий и регресса."""

    messages: list[tuple[str, str]] = field(default_factory=list)
    line: int | None = None
    resolved: bool | None = None
    origin: Provenance = field(default_factory=Provenance)


@dataclass(slots=True)
class AssetRecord:
    data: bytes
    caption: str = ""
    # Как эту картинку привязать к записи: {figure, page, ref, order}
    anchor: dict = field(default_factory=dict)
    origin: Provenance = field(default_factory=Provenance)


Record = ArticleRecord | FeedbackRecord | DialogRecord | AssetRecord
```

```python
# app/ingest/__init__.py
"""Приём произвольных данных: источники, реестр, конвейер."""
```

Поле в модели (`app/models.py`, после `source`):

```python
    # Откуда приехала статья: файл, строка, разобравший источник и признак
    # авторского текста. Nullable ради досинхронизации схемы на живом стенде.
    origin: Mapped[dict | None] = mapped_column(JSON, nullable=True, default=None)
```

- [ ] **Шаг 4: Убедиться, что тесты проходят**

Запуск: `uv run pytest tests/test_ingest_records.py -q && uv run pytest -q`
Ожидание: новые тесты PASS, весь набор зелёный

- [ ] **Шаг 5: Коммит**

```bash
git add app/ingest app/models.py tests/test_ingest_records.py
git commit -m "feat(ingest): записи как промежуточный формат и провенанс статьи"
```

---

### Task 2: FileBlob — ленивое чтение файла

**Файлы:**
- Создать: `app/ingest/blob.py`
- Тест: `tests/test_ingest_blob.py`

**Интерфейсы:**
- Потребляет: ничего.
- Отдаёт: `FileBlob(name, data)` со свойствами `suffix`, `text` (ленивый markdown через
  markitdown), `tables` (ленивый список таблиц-датафреймов по листам), `is_binary`,
  методом `decoded()`. Используется всеми источниками в `sniff`/`parse`.

- [ ] **Шаг 1: Написать падающий тест**

```python
# tests/test_ingest_blob.py
from app.ingest.blob import FileBlob


def test_text_is_decoded_without_converter():
    blob = FileBlob(name="notes.txt", data="Привет, мир".encode("utf-8"))
    assert blob.text == "Привет, мир"
    assert blob.suffix == ".txt"


def test_cp1251_survives():
    blob = FileBlob(name="old.csv", data="Вопрос;Ответ".encode("cp1251"))
    assert "Вопрос" in blob.text


def test_text_is_computed_once():
    blob = FileBlob(name="notes.md", data=b"# H")
    first = blob.text
    assert first is blob.text          # тот же объект: конвертация не повторяется


def test_binary_is_detected():
    blob = FileBlob(name="shot.png", data=b"\x89PNG\r\n\x1a\n")
    assert blob.is_binary is True
```

- [ ] **Шаг 2: Убедиться, что тест падает**

Запуск: `uv run pytest tests/test_ingest_blob.py -q`
Ожидание: FAIL, `ModuleNotFoundError: No module named 'app.ingest.blob'`

- [ ] **Шаг 3: Написать минимальную реализацию**

```python
# app/ingest/blob.py
"""Файл на входе конвейера: имя, байты и ленивые представления.

Ленивые намеренно. `sniff` спрашивают у всех источников подряд, и если бы
каждый дёргал markitdown, разбор PDF шёл бы десять раз вместо одного.
"""

from __future__ import annotations

import io
from functools import cached_property
from pathlib import Path

TEXT_SUFFIXES = (".txt", ".md", ".markdown", ".csv", ".json", ".jsonl", ".xml", ".html", ".htm")
IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp")
# Кодировки по убыванию вероятности: выгрузки из старых систем приезжают в 1251
ENCODINGS = ("utf-8-sig", "utf-8", "cp1251", "koi8-r")


class FileBlob:
    def __init__(self, name: str, data: bytes) -> None:
        self.name = name
        self.data = data

    @property
    def suffix(self) -> str:
        return Path(self.name).suffix.lower()

    @property
    def is_binary(self) -> bool:
        return b"\x00" in self.data[:1024] or self.suffix in IMAGE_SUFFIXES

    def decoded(self) -> str:
        """Байты → строка перебором кодировок. Последняя попытка — с заменой."""
        for encoding in ENCODINGS:
            try:
                return self.data.decode(encoding)
            except UnicodeDecodeError:
                continue
        return self.data.decode("utf-8", errors="replace")

    @cached_property
    def text(self) -> str:
        """Markdown-представление. Простой текст не гоняем через конвертер."""
        if self.suffix in (".txt", ".md", ".markdown"):
            return self.decoded()
        from app.services.ingest import extract_text_from_file
        return extract_text_from_file(self.name, self.data)

    @cached_property
    def tables(self) -> list[tuple[str, object]]:
        """[(имя листа, DataFrame)] для xlsx/xls; для остального — пусто.

        Полистно, а не одной простынёй: в выгрузках лист «Вопросы» и лист
        «Операторы» — разные наборы данных, и склеивать их значит потерять оба.
        """
        if self.suffix not in (".xlsx", ".xls", ".xlsm"):
            return []
        import pandas as pd
        book = pd.read_excel(io.BytesIO(self.data), sheet_name=None, dtype=str)
        return [(name, frame.fillna("")) for name, frame in book.items()]
```

- [ ] **Шаг 4: Убедиться, что тесты проходят**

Запуск: `uv run pytest tests/test_ingest_blob.py -q && uv run pytest -q`
Ожидание: PASS

- [ ] **Шаг 5: Коммит**

```bash
git add app/ingest/blob.py tests/test_ingest_blob.py
git commit -m "feat(ingest): FileBlob с ленивым текстом, кодировками и листами Excel"
```

---

### Task 3: Реестр источников

**Файлы:**
- Создать: `app/ingest/sources/__init__.py`
- Создать: `app/ingest/sources/base.py`
- Создать: `app/ingest/registry.py`
- Тест: `tests/test_ingest_registry.py`

**Интерфейсы:**
- Потребляет: `FileBlob` (Task 2), `Record` (Task 1).
- Отдаёт: `Source` (протокол с `name`, `priority`, `sniff`, `parse`), исключение
  `SourceMismatch`, декоратор `register`, функции `pick_source(blob) -> Source | None`
  и `parse_blob(blob, ctx) -> list[Record]`, датакласс `IngestContext(rt, params)`.

- [ ] **Шаг 1: Написать падающий тест**

```python
# tests/test_ingest_registry.py
import pytest

from app.ingest.blob import FileBlob
from app.ingest.records import ArticleRecord
from app.ingest.registry import IngestContext, SourceMismatch, parse_blob, pick_source, registry


@pytest.fixture
def isolated_registry():
    """Реестр глобальный — тест обязан вернуть его в исходное состояние."""
    saved = list(registry.sources)
    registry.sources.clear()
    yield registry
    registry.sources[:] = saved


def test_most_confident_source_wins(isolated_registry):
    class Weak:
        name = "weak"
        priority = 0
        async def sniff(self, blob): return 0.2
        async def parse(self, blob, ctx): yield ArticleRecord(question="w", answer="w")

    class Strong:
        name = "strong"
        priority = 0
        async def sniff(self, blob): return 0.9
        async def parse(self, blob, ctx): yield ArticleRecord(question="s", answer="s")

    isolated_registry.add(Weak())
    isolated_registry.add(Strong())
    blob = FileBlob(name="x.txt", data=b"anything")
    assert pytest.run(pick_source(blob)).name == "strong"


@pytest.mark.asyncio
async def test_mismatch_falls_through_to_next(isolated_registry):
    class Refusing:
        name = "refusing"
        priority = 0
        async def sniff(self, blob): return 0.9
        async def parse(self, blob, ctx):
            raise SourceMismatch("передумал")
            yield  # pragma: no cover — делает функцию генератором

    class Backup:
        name = "backup"
        priority = 0
        async def sniff(self, blob): return 0.5
        async def parse(self, blob, ctx): yield ArticleRecord(question="b", answer="b")

    isolated_registry.add(Refusing())
    isolated_registry.add(Backup())
    records = await parse_blob(FileBlob(name="x.txt", data=b"y"), IngestContext())
    assert [r.question for r in records] == ["b"]


@pytest.mark.asyncio
async def test_fallback_source_goes_last(isolated_registry):
    class Fallback:
        name = "llm_fallback"
        priority = -100
        async def sniff(self, blob): return 1.0
        async def parse(self, blob, ctx): yield ArticleRecord(question="f", answer="f")

    class Normal:
        name = "normal"
        priority = 0
        async def sniff(self, blob): return 0.3
        async def parse(self, blob, ctx): yield ArticleRecord(question="n", answer="n")

    isolated_registry.add(Fallback())
    isolated_registry.add(Normal())
    records = await parse_blob(FileBlob(name="x.txt", data=b"y"), IngestContext())
    assert [r.question for r in records] == ["n"]
```

Первый тест написан неверно намеренно — `pytest.run` не существует. Заменить на:

```python
@pytest.mark.asyncio
async def test_most_confident_source_wins(isolated_registry):
    ...
    picked = await pick_source(blob)
    assert picked.name == "strong"
```

- [ ] **Шаг 2: Убедиться, что тест падает**

Запуск: `uv run pytest tests/test_ingest_registry.py -q`
Ожидание: FAIL, `ModuleNotFoundError: No module named 'app.ingest.registry'`

- [ ] **Шаг 3: Написать минимальную реализацию**

```python
# app/ingest/sources/base.py
"""Контракт источника: распознать файл и выдать записи.

Два метода вместо одного намеренно. `sniff` дешёвый и вызывается у всех
источников подряд — по нему реестр решает, кому отдать файл; `parse` дорогой и
работает один раз. Источник, который в процессе понял, что ошибся, бросает
SourceMismatch, и очередь идёт дальше — это лучше, чем выдать мусорные записи.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Protocol, runtime_checkable

from app.ingest.blob import FileBlob
from app.ingest.records import Record


class SourceMismatch(Exception):
    """Источник взялся за файл и понял, что это не его формат."""


@runtime_checkable
class Source(Protocol):
    name: str
    # Тай-брейк при равной уверенности; у запасного нормализатора он отрицательный,
    # чтобы тот никогда не перехватывал файл у настоящего источника.
    priority: int

    async def sniff(self, blob: FileBlob) -> float: ...

    def parse(self, blob: FileBlob, ctx: object) -> AsyncIterator[Record]: ...
```

```python
# app/ingest/registry.py
"""Реестр источников: кому отдать файл и что делать, если тот отказался."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from app.ingest.blob import FileBlob
from app.ingest.records import Record
from app.ingest.sources.base import Source, SourceMismatch

log = logging.getLogger(__name__)

MIN_CONFIDENCE = 0.15


@dataclass
class IngestContext:
    """Что источнику нужно кроме файла: рантайм для LLM и параметры импорта."""

    rt: object | None = None
    params: dict = field(default_factory=dict)
    # Соседние файлы из той же папки: картинки, на которые ссылается таблица
    siblings: dict[str, bytes] = field(default_factory=dict)


class Registry:
    def __init__(self) -> None:
        self.sources: list[Source] = []

    def add(self, source: Source) -> Source:
        self.sources.append(source)
        return source

    async def ranked(self, blob: FileBlob) -> list[tuple[float, Source]]:
        scored: list[tuple[float, Source]] = []
        for source in self.sources:
            try:
                confidence = await source.sniff(blob)
            except Exception:  # noqa: BLE001 — кривой источник не должен ронять импорт
                log.exception("Источник %s упал на sniff", source.name)
                continue
            if confidence >= MIN_CONFIDENCE:
                scored.append((confidence, source))
        scored.sort(key=lambda item: (-item[0], -item[1].priority))
        return scored


registry = Registry()


def register(cls):
    """Декоратор класса: источник регистрируется самим фактом импорта модуля."""
    registry.add(cls())
    return cls


async def pick_source(blob: FileBlob) -> Source | None:
    ranked = await registry.ranked(blob)
    return ranked[0][1] if ranked else None


async def parse_blob(blob: FileBlob, ctx: IngestContext) -> list[Record]:
    """Разобрать файл первым согласившимся источником.

    Отказ (SourceMismatch) и падение источника означают одно и то же для вызывающего:
    пробуем следующего. Пустая выдача — тоже отказ: файл, из которого не вышло ни
    одной записи, стоит показать следующему источнику, а не считать разобранным.
    """
    for confidence, source in await registry.ranked(blob):
        try:
            records = [record async for record in source.parse(blob, ctx)]
        except SourceMismatch as exc:
            log.info("Источник %s отказался от %s: %s", source.name, blob.name, exc)
            continue
        except Exception:  # noqa: BLE001
            log.exception("Источник %s упал на разборе %s", source.name, blob.name)
            continue
        if records:
            log.info("Файл %s разобран источником %s (уверенность %.2f), записей %d",
                     blob.name, source.name, confidence, len(records))
            return records
    return []
```

```python
# app/ingest/sources/__init__.py
"""Источники. Импорт модуля регистрирует источник в реестре."""

from app.ingest.sources import base  # noqa: F401

__all__ = ["base"]
```

- [ ] **Шаг 4: Убедиться, что тесты проходят**

Запуск: `uv run pytest tests/test_ingest_registry.py -q && uv run pytest -q`
Ожидание: PASS

- [ ] **Шаг 5: Коммит**

```bash
git add app/ingest/registry.py app/ingest/sources tests/test_ingest_registry.py
git commit -m "feat(ingest): реестр источников с выбором по уверенности и отказом в пользу следующего"
```

---

### Task 4: Разбор `services/ingest.py` на модули

Чистый перенос, поведение не меняется. Страховка — существующий `tests/test_ingest.py`
и весь набор.

**Файлы:**
- Создать: `app/ingest/chunking.py` (из `services/ingest.py:35-196`: `Section`,
  `_MD_HEADING`, `_NUMBERED_HEADING`, `_LIST_ITEM`, `_SENTENCE_END`, `MAX_CHUNK_CHARS`,
  `MIN_CHUNK_CHARS`, `MAX_HEADING_CHARS`, `_looks_like_heading`, `split_sections`,
  `_split_body`, `_MEANINGFUL`, `MIN_MEANINGFUL_CHARS`, `_is_junk`, `_merge_tiny`)
- Создать: `app/ingest/tables.py` (из `services/ingest.py:198-296`: `_TABLE_ROW`,
  `_TABLE_SEPARATOR`, `_QUESTION_COLUMNS`, `_ANSWER_COLUMNS`, `MIN_TABLE_ROWS`,
  `TABLE_DOMINANCE`, `_CAPTION`, `_normalized`, `_useless_answer`, `_split_row`,
  `_pick_column`, `parse_article_table`)
- Создать: `app/ingest/questions.py` (из `services/ingest.py:297-355`: `_QUESTION_PROMPT`,
  `fallback_question`, `generate_question`, `extract_keywords`)
- Создать: `app/ingest/figures.py` (из `services/ingest.py:61-73, 456-570`: `_FIGURE_REF`,
  `figure_numbers`, `attach_images`, `_section_by_anchor`, `_FIGURE_IN_PARENS`,
  `_FIGURE_CAPTION_LINE`, `_FIGURE_INLINE`, `strip_dangling_figures`)
- Создать: `app/ingest/documents.py` (из `services/ingest.py:356-455`: `extract_document`,
  `SUPPORTED_EXTENSIONS`, `extract_text_from_file`, `LLM_CONCURRENCY`,
  `MAX_IMAGES_PER_SECTION`, `store_pictures`)
- Изменить: `app/services/ingest.py` — остаётся `ingest_document`, `_ingest_table` и
  реэкспорт перенесённого
- Тест: существующий `tests/test_ingest.py` — не меняется

**Интерфейсы:**
- Отдаёт: те же публичные имена, но из новых модулей. `app.services.ingest` продолжает
  экспортировать всё, что экспортировал.

- [ ] **Шаг 1: Зафиксировать текущее поведение**

Запуск: `uv run pytest tests/test_ingest.py tests/test_media_and_images.py -q`
Ожидание: PASS. Это эталон: после переноса вывод обязан совпасть.

- [ ] **Шаг 2: Перенести код по модулям**

Копировать блоки целиком, вместе с комментариями. Менять разрешено только импорты.
Циклы импорта не возникают: `chunking` ни от кого не зависит, `tables` зависит от
`chunking`, `questions` — от `chunking`, `figures` — от `chunking`, `documents` — от
`chunking` и адаптеров.

- [ ] **Шаг 3: Превратить `services/ingest.py` в фасад**

```python
# app/services/ingest.py — шапка после переноса
"""Импорт документов в базу знаний.

Разбор переехал в пакет `app.ingest`: нарезка, таблицы, вопросы, рисунки и
форматы файлов живут отдельными модулями, здесь остаётся только сборка —
документ на входе, статьи на выходе. Имена реэкспортируются: их импортируют
роутеры, фоновые задачи и два десятка тестов.
"""

from app.ingest.chunking import (  # noqa: F401
    MAX_CHUNK_CHARS, MIN_CHUNK_CHARS, Section, split_sections,
)
from app.ingest.documents import (  # noqa: F401
    LLM_CONCURRENCY, MAX_IMAGES_PER_SECTION, SUPPORTED_EXTENSIONS,
    extract_document, extract_text_from_file, store_pictures,
)
from app.ingest.figures import (  # noqa: F401
    attach_images, figure_numbers, strip_dangling_figures,
)
from app.ingest.questions import (  # noqa: F401
    extract_keywords, fallback_question, generate_question,
)
from app.ingest.tables import parse_article_table  # noqa: F401
```

- [ ] **Шаг 4: Прогнать весь набор**

Запуск: `uv run pytest -q && uv run ruff check .`
Ожидание: 296 тестов PASS, линт чистый. Любое падение означает, что перенос был не
дословным — сравнить с эталоном шага 1, а не «чинить».

- [ ] **Шаг 5: Коммит**

```bash
git add app/ingest app/services/ingest.py
git commit -m "refactor(ingest): разбор документа разложен по модулям, services/ingest — фасад"
```

---

### Task 5: Слой картинок

**Файлы:**
- Создать: `app/ingest/assets.py`
- Изменить: `app/ingest/chunking.py` (`_is_junk` перестаёт отбрасывать кусок с картинкой)
- Тест: `tests/test_ingest_assets.py`

**Интерфейсы:**
- Потребляет: `AssetRecord`, `ImageRef`, `Provenance` (Task 1), `MediaStorage`
  (`app/adapters/storage.py`).
- Отдаёт: `async def store_assets(rt, slug, assets) -> list[ImageRef]`,
  `def bind_assets(records, images) -> int` — привязка картинок к статьям тремя
  способами; `def has_visual_answer(record) -> bool`.

- [ ] **Шаг 1: Написать падающий тест**

```python
# tests/test_ingest_assets.py
from app.ingest.assets import bind_assets, has_visual_answer
from app.ingest.chunking import Section, _is_junk
from app.ingest.records import ArticleRecord, ImageRef


def test_explicit_reference_wins():
    rec = ArticleRecord(question="Как войти?", answer="См. скриншот",
                        origin=None or __import__("app.ingest.records", fromlist=["Provenance"]).Provenance())
    rec.origin.locator = "строка 3"
    images = [ImageRef(url="/media/a.png", caption="", figure=""),
              ImageRef(url="/media/b.png", caption="", figure="")]
    bound = bind_assets([rec], images, refs={id(rec): ["/media/b.png"]})
    assert bound == 1
    assert [i.url for i in rec.images] == ["/media/b.png"]


def test_figure_number_binds_when_no_explicit_ref():
    rec = ArticleRecord(question="Как войти?", answer="Нажмите (Рисунок 26)")
    images = [ImageRef(url="/media/26.png", figure="26"),
              ImageRef(url="/media/27.png", figure="27")]
    bound = bind_assets([rec], images)
    assert bound == 1
    assert rec.images[0].figure == "26"


def test_image_only_answer_is_not_junk():
    rec = ArticleRecord(question="Где кнопка?", answer="",
                        images=[ImageRef(url="/media/a.png")])
    assert has_visual_answer(rec) is True
    section = Section(title="Где кнопка?", body="", images=[{"url": "/media/a.png"}])
    assert _is_junk(section.body, has_images=True) is False
```

- [ ] **Шаг 2: Убедиться, что тест падает**

Запуск: `uv run pytest tests/test_ingest_assets.py -q`
Ожидание: FAIL, `ModuleNotFoundError: No module named 'app.ingest.assets'`

- [ ] **Шаг 3: Написать минимальную реализацию**

```python
# app/ingest/assets.py
"""Картинки: сохранение в хранилище и привязка к записям.

Три способа привязки по убыванию надёжности. Явная ссылка (колонка «скриншот»,
путь в JSON) — это то, что написал автор данных, ей верим безусловно. Авторская
нумерация («Рисунок 26») — почти так же надёжно, так работает импорт методичек.
Соседство в документе — догадка, и применяется последней.

Картинка может быть ответом целиком: в выгрузках поддержки на «где эта кнопка»
отвечают скриншотом. Запись с пустым текстом и картинкой — валидная статья.
"""

from __future__ import annotations

import logging

from app.ingest.figures import figure_numbers
from app.ingest.records import ArticleRecord, AssetRecord, ImageRef

log = logging.getLogger(__name__)

MAX_IMAGES_PER_RECORD = 3


async def store_assets(rt, slug: str, assets: list[AssetRecord]) -> list[ImageRef]:
    """Сохранить картинки в хранилище (том или S3) и вернуть ссылки на них."""
    stored: list[ImageRef] = []
    for index, asset in enumerate(assets):
        key = f"kb/{slug}/{index:04d}.png"
        url = await rt.media.put(key, asset.data, "image/png")
        stored.append(ImageRef(url=url, caption=asset.caption,
                               page=asset.anchor.get("page"),
                               figure=str(asset.anchor.get("figure") or "")))
    return stored


def has_visual_answer(record: ArticleRecord) -> bool:
    """Ответ — картинка: текста нет, изображение есть."""
    return not record.answer.strip() and bool(record.images)


def bind_assets(records: list[ArticleRecord], images: list[ImageRef],
                refs: dict[int, list[str]] | None = None) -> int:
    """Привязать картинки к записям. Возвращает число привязанных."""
    bound = 0
    by_figure = {image.figure: image for image in images if image.figure}
    used: set[str] = set()

    for record in records:
        explicit = (refs or {}).get(id(record)) or []
        if explicit:
            chosen = [image for image in images if image.url in explicit]
            record.images = chosen[:MAX_IMAGES_PER_RECORD]
            used.update(image.url for image in record.images)
            bound += len(record.images)
            continue

        wanted = figure_numbers(f"{record.question}\n{record.answer}")
        chosen = [by_figure[number] for number in sorted(wanted) if number in by_figure]
        if chosen:
            record.images = chosen[:MAX_IMAGES_PER_RECORD]
            used.update(image.url for image in record.images)
            bound += len(record.images)

    return bound
```

В `app/ingest/chunking.py` сигнатура `_is_junk` получает флаг:

```python
def _is_junk(body: str, has_images: bool = False) -> bool:
    """Кусок без смысла: одни цифры, подписи и разделители.

    Кусок с картинкой мусором не считается: в инструкциях шаг «нажмите вот тут»
    состоит из скриншота и двух слов, и отбрасывать его — терять ответ.
    """
    if has_images:
        return False
    ...
```

Все вызовы `_is_junk(...)` внутри `chunking.py` передают `has_images=bool(section.images)`.

- [ ] **Шаг 4: Убедиться, что тесты проходят**

Запуск: `uv run pytest tests/test_ingest_assets.py tests/test_ingest.py -q && uv run pytest -q`
Ожидание: PASS

- [ ] **Шаг 5: Коммит**

```bash
git add app/ingest/assets.py app/ingest/chunking.py tests/test_ingest_assets.py
git commit -m "feat(ingest): общий слой картинок, ответ-картинка больше не отбрасывается"
```

---

### Task 6: Источник `qa_table` с распознаванием колонок по содержимому

**Файлы:**
- Создать: `app/ingest/sources/qa_table.py`
- Изменить: `app/ingest/tables.py` (добавить `detect_columns`)
- Тест: `tests/test_source_qa_table.py`

**Интерфейсы:**
- Потребляет: `FileBlob`, `IngestContext`, `register`, `ArticleRecord`, `Provenance`.
- Отдаёт: `QaTableSource` (`name="qa_table"`, `priority=10`), функцию
  `detect_columns(headers, rows) -> tuple[int, int]` в `tables.py`.

- [ ] **Шаг 1: Написать падающий тест**

```python
# tests/test_source_qa_table.py
import pytest

from app.ingest.blob import FileBlob
from app.ingest.registry import IngestContext
from app.ingest.sources.qa_table import QaTableSource
from app.ingest.tables import detect_columns

TABLE = """| Текст обращения | Решение | Линия |
|---|---|---|
| Как зарегистрироваться на портале? | Нужна ЭП и заявка в ЛК. | 1 |
| Как подписать контракт? | Откройте карточку и нажмите «Подписать». | 2 |
| Почему не приходит УПД? | Проверьте статус в разделе «Исполнение». | 2 |
| Как сменить банковские реквизиты? | В профиле организации. | 1 |
| Куда жаловаться на заказчика? | В ФАС через форму на портале. | 3 |
| Как выгрузить реестр? | Кнопка «Экспорт» в реестре сессий. | 2 |
"""


def test_columns_detected_by_content_not_by_name():
    headers = ["Текст обращения", "Решение", "Линия"]
    rows = [["Как зарегистрироваться?", "Нужна ЭП.", "1"],
            ["Как подписать контракт?", "Откройте карточку.", "2"],
            ["Почему не приходит УПД?", "Проверьте статус.", "2"]]
    question_at, answer_at = detect_columns(headers, rows)
    assert (question_at, answer_at) == (0, 1)


@pytest.mark.asyncio
async def test_source_yields_authored_articles():
    source = QaTableSource()
    blob = FileBlob(name="qa.md", data=TABLE.encode("utf-8"))
    assert await source.sniff(blob) > 0.7
    records = [r async for r in source.parse(blob, IngestContext())]
    assert len(records) == 6
    assert records[0].question == "Как зарегистрироваться на портале?"
    assert records[0].origin.authored is True
    assert records[0].origin.locator.startswith("строка")


@pytest.mark.asyncio
async def test_prose_is_refused():
    source = QaTableSource()
    blob = FileBlob(name="doc.md", data=b"# Regulation\n\nParagraph without a table.\n")
    assert await source.sniff(blob) < 0.15
```

- [ ] **Шаг 2: Убедиться, что тест падает**

Запуск: `uv run pytest tests/test_source_qa_table.py -q`
Ожидание: FAIL, `ModuleNotFoundError: No module named 'app.ingest.sources.qa_table'`

- [ ] **Шаг 3: Написать минимальную реализацию**

В `app/ingest/tables.py`:

```python
QUESTION_MARK_SHARE = 0.5
MAX_QUESTION_CHARS = 200


def detect_columns(headers: list[str], rows: list[list[str]]) -> tuple[int, int]:
    """Какая колонка — вопрос, какая — ответ. Имя заголовка это только подсказка.

    Выгрузки приезжают с колонками «Текст обращения» и «Решение», которых нет ни
    в одном списке синонимов. Поэтому решает содержимое: колонка, где больше
    половины непустых ячеек короткие и кончаются вопросительным знаком, — вопрос;
    самая длинная текстовая колонка из оставшихся — ответ. Имя проверяется первым
    и, если совпало со списком синонимов, содержимое уже не спрашиваем.
    """
    by_name_q = _pick_column(headers, _QUESTION_COLUMNS, -1)
    by_name_a = _pick_column(headers, _ANSWER_COLUMNS, -1)
    if by_name_q >= 0 and by_name_a >= 0 and by_name_q != by_name_a:
        return by_name_q, by_name_a

    columns = list(range(len(headers)))
    question_at = by_name_q
    if question_at < 0:
        best_share = 0.0
        for index in columns:
            values = [row[index].strip() for row in rows if index < len(row) and row[index].strip()]
            if not values:
                continue
            share = sum(
                1 for value in values if value.endswith("?") and len(value) <= MAX_QUESTION_CHARS
            ) / len(values)
            if share > best_share:
                best_share, question_at = share, index
        if best_share < QUESTION_MARK_SHARE:
            question_at = 0

    answer_at = by_name_a
    if answer_at < 0:
        lengths = {
            index: sum(len(row[index]) for row in rows if index < len(row))
            for index in columns if index != question_at
        }
        answer_at = max(lengths, key=lengths.get) if lengths else min(1, len(headers) - 1)
    return question_at, answer_at
```

```python
# app/ingest/sources/qa_table.py
"""Готовая выгрузка «вопрос — ответ»: markdown-таблица, CSV, вставленная таблица.

Такие данные уже структурированы человеком, и придумывать к ним вопросы значит
портить авторские формулировки. Поэтому записи помечаются authored: их ответ
уйдёт пользователю дословно.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from app.ingest.blob import FileBlob
from app.ingest.records import ArticleRecord, Provenance
from app.ingest.registry import IngestContext, register
from app.ingest.tables import parse_article_table


@register
class QaTableSource:
    name = "qa_table"
    priority = 10

    async def sniff(self, blob: FileBlob) -> float:
        if blob.is_binary or blob.suffix in (".xlsx", ".xls", ".xlsm"):
            return 0.0
        try:
            rows = parse_article_table(blob.text)
        except Exception:  # noqa: BLE001 — не наш формат, а не поломка
            return 0.0
        if not rows:
            return 0.0
        # Уверенность растёт с числом строк: пять строк могут оказаться случайной
        # табличкой внутри методички, сотня — уже точно выгрузка
        return min(0.95, 0.6 + len(rows) / 200)

    async def parse(self, blob: FileBlob, ctx: IngestContext) -> AsyncIterator[ArticleRecord]:
        rows = parse_article_table(blob.text) or []
        for index, row in enumerate(rows, start=1):
            yield ArticleRecord(
                question=row["question"],
                answer=row["answer"],
                category=row.get("category", "") or ctx.params.get("category", ""),
                line=row.get("line") or ctx.params.get("line"),
                audience=ctx.params.get("audience", "all"),
                origin=Provenance(file=blob.name, locator=f"строка {index}",
                                  source_name=self.name, authored=True),
            )
```

`parse_article_table` внутри переходит на `detect_columns` вместо прямых вызовов
`_pick_column`.

- [ ] **Шаг 4: Убедиться, что тесты проходят**

Запуск: `uv run pytest tests/test_source_qa_table.py tests/test_ingest.py -q && uv run pytest -q`
Ожидание: PASS

- [ ] **Шаг 5: Коммит**

```bash
git add app/ingest/sources/qa_table.py app/ingest/tables.py tests/test_source_qa_table.py
git commit -m "feat(ingest): источник qa_table, колонки распознаются по содержимому"
```

---

### Task 7: Источник `document` (методичка)

**Файлы:**
- Создать: `app/ingest/sources/document.py`
- Тест: `tests/test_source_document.py`

**Интерфейсы:**
- Потребляет: `split_sections`, `fallback_question`, `generate_question`,
  `extract_keywords`, `IngestContext`.
- Отдаёт: `DocumentSource` (`name="document"`, `priority=0`).

- [ ] **Шаг 1: Написать падающий тест**

```python
# tests/test_source_document.py
import pytest

from app.ingest.blob import FileBlob
from app.ingest.registry import IngestContext
from app.ingest.sources.document import DocumentSource

DOC = """# Регистрация на портале

Для регистрации нужна усиленная квалифицированная электронная подпись.
Заявка подаётся в личном кабинете и рассматривается уполномоченным органом
в течение трёх рабочих дней. После одобрения организация появляется в реестре.

## Подписание контракта

Карточка контракта формируется у заказчика после подписания оферты победителем.
Поставщик подписывает её электронной подписью в разделе «Мои контракты».
"""


@pytest.mark.asyncio
async def test_document_becomes_sections_without_llm():
    source = DocumentSource()
    blob = FileBlob(name="manual.md", data=DOC.encode("utf-8"))
    assert await source.sniff(blob) > 0.15
    ctx = IngestContext(params={"use_llm": False})
    records = [r async for r in source.parse(blob, ctx)]
    assert len(records) == 2
    assert records[0].question.startswith("Регистрация")
    assert "электронная подпись" in records[0].answer
    # Вопрос придуман нами, не автором: дословный ответ по такой статье запрещён
    assert records[0].origin.authored is False
    assert records[0].keywords
```

- [ ] **Шаг 2: Убедиться, что тест падает**

Запуск: `uv run pytest tests/test_source_document.py -q`
Ожидание: FAIL, `ModuleNotFoundError`

- [ ] **Шаг 3: Написать минимальную реализацию**

```python
# app/ingest/sources/document.py
"""Неформализованная методичка: режем по заголовкам, вопрос формулирует LLM.

Записи помечаются authored=False: вопрос придуман нами, и отдавать такой ответ
дословно нельзя — он писался как абзац инструкции, а не как ответ человеку.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

from app.ingest.blob import FileBlob
from app.ingest.chunking import MAX_CHUNK_CHARS, split_sections
from app.ingest.questions import extract_keywords, fallback_question, generate_question
from app.ingest.records import ArticleRecord, Provenance
from app.ingest.registry import IngestContext, register


@register
class DocumentSource:
    name = "document"
    priority = 0

    async def sniff(self, blob: FileBlob) -> float:
        if blob.is_binary:
            return 0.0
        try:
            sections = split_sections(blob.text, MAX_CHUNK_CHARS)
        except Exception:  # noqa: BLE001
            return 0.0
        if not sections:
            return 0.0
        # Средний источник: берёт всё, что похоже на текст, но уступает
        # структурированным выгрузкам
        return 0.45

    async def parse(self, blob: FileBlob, ctx: IngestContext) -> AsyncIterator[ArticleRecord]:
        sections = split_sections(blob.text, ctx.params.get("max_chunk_chars", MAX_CHUNK_CHARS))
        use_llm = bool(ctx.params.get("use_llm", True)) and ctx.rt is not None
        semaphore = asyncio.Semaphore(8)

        async def question_for(section) -> str:
            if not use_llm:
                return fallback_question(section)
            async with semaphore:
                return await generate_question(ctx.rt.dyn, section)

        for index, section in enumerate(sections, start=1):
            yield ArticleRecord(
                question=await question_for(section),
                answer=section.body,
                category=ctx.params.get("category", "") or section.title,
                line=ctx.params.get("line"),
                audience=ctx.params.get("audience", "all"),
                keywords=extract_keywords(section),
                origin=Provenance(file=blob.name, locator=f"фрагмент {index}",
                                  source_name=self.name, authored=False),
            )
```

- [ ] **Шаг 4: Убедиться, что тесты проходят**

Запуск: `uv run pytest tests/test_source_document.py -q && uv run pytest -q`
Ожидание: PASS

- [ ] **Шаг 5: Коммит**

```bash
git add app/ingest/sources/document.py tests/test_source_document.py
git commit -m "feat(ingest): источник document — нарезка методички по заголовкам"
```

---

### Task 8: Источник `spreadsheet` (Excel полистно)

**Файлы:**
- Создать: `app/ingest/sources/spreadsheet.py`
- Тест: `tests/test_source_spreadsheet.py`

**Интерфейсы:**
- Потребляет: `FileBlob.tables` (Task 2), `detect_columns` (Task 6).
- Отдаёт: `SpreadsheetSource` (`name="spreadsheet"`, `priority=20`), функцию
  `classify_sheet(headers, rows) -> str` со значениями `"qa" | "feedback" | "unknown"`.

- [ ] **Шаг 1: Написать падающий тест**

```python
# tests/test_source_spreadsheet.py
import io

import pandas as pd
import pytest

from app.ingest.blob import FileBlob
from app.ingest.records import ArticleRecord, FeedbackRecord
from app.ingest.registry import IngestContext
from app.ingest.sources.spreadsheet import SpreadsheetSource, classify_sheet


def _book() -> bytes:
    qa = pd.DataFrame({
        "Вопрос": ["Как зарегистрироваться?", "Как подписать контракт?",
                   "Где найти УПД?", "Как сменить реквизиты?", "Куда жаловаться?"],
        "Ответ": ["Нужна ЭП.", "В карточке контракта.", "В разделе исполнения.",
                  "В профиле организации.", "В ФАС."],
    })
    reviews = pd.DataFrame({
        "Оценка": [5, 2, 4],
        "Комментарий": ["Быстро помогли", "Долго ждал ответа", "Всё понятно"],
        "Специалист": ["Мария", "Иван", "Мария"],
    })
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        qa.to_excel(writer, sheet_name="Вопросы", index=False)
        reviews.to_excel(writer, sheet_name="Отзывы", index=False)
    return buffer.getvalue()


def test_sheet_kind_is_recognised():
    assert classify_sheet(["Вопрос", "Ответ"], [["Как?", "Так."]]) == "qa"
    assert classify_sheet(["Оценка", "Комментарий"], [["5", "Спасибо"]]) == "feedback"
    assert classify_sheet(["ИНН", "КПП"], [["7701", "770101"]]) == "unknown"


@pytest.mark.asyncio
async def test_two_sheets_give_two_kinds_of_records():
    source = SpreadsheetSource()
    blob = FileBlob(name="dump.xlsx", data=_book())
    assert await source.sniff(blob) > 0.7
    records = [r async for r in source.parse(blob, IngestContext())]
    articles = [r for r in records if isinstance(r, ArticleRecord)]
    feedback = [r for r in records if isinstance(r, FeedbackRecord)]
    assert len(articles) == 5
    assert len(feedback) == 3
    assert articles[0].origin.locator == "лист «Вопросы», строка 1"
    assert feedback[1].rating == 2
    assert feedback[1].operator == "Иван"
```

- [ ] **Шаг 2: Убедиться, что тест падает**

Запуск: `uv run pytest tests/test_source_spreadsheet.py -q`
Ожидание: FAIL, `ModuleNotFoundError`

- [ ] **Шаг 3: Написать минимальную реализацию**

```python
# app/ingest/sources/spreadsheet.py
"""Excel: каждый лист разбирается отдельно и по-своему.

Склеивать листы нельзя: в одной книге приезжают «Вопросы», «Отзывы» и
справочник организаций, и общая простыня превращает три набора данных в кашу.
Вид листа определяется по заголовкам, а колонки внутри — по содержимому.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from app.ingest.blob import FileBlob
from app.ingest.records import ArticleRecord, FeedbackRecord, Provenance, Record
from app.ingest.registry import IngestContext, register
from app.ingest.tables import detect_columns

RATING_COLUMNS = ("оценка", "рейтинг", "балл", "звёзд", "звезд", "rating", "score")
COMMENT_COLUMNS = ("комментарий", "отзыв", "текст", "comment", "review", "feedback")
OPERATOR_COLUMNS = ("специалист", "оператор", "сотрудник", "operator", "agent")
MIN_ROWS = 2


def _has(headers: list[str], names: tuple[str, ...]) -> int:
    for index, header in enumerate(headers):
        low = header.strip().lower()
        if any(name in low for name in names):
            return index
    return -1


def classify_sheet(headers: list[str], rows: list[list[str]]) -> str:
    if _has(headers, RATING_COLUMNS) >= 0 and _has(headers, COMMENT_COLUMNS) >= 0:
        return "feedback"
    question_at, answer_at = detect_columns(headers, rows)
    if question_at != answer_at and rows:
        filled = sum(1 for row in rows if len(row) > answer_at and row[answer_at].strip())
        if filled / len(rows) > 0.5:
            return "qa"
    return "unknown"


@register
class SpreadsheetSource:
    name = "spreadsheet"
    priority = 20

    async def sniff(self, blob: FileBlob) -> float:
        if blob.suffix not in (".xlsx", ".xls", ".xlsm"):
            return 0.0
        try:
            sheets = blob.tables
        except Exception:  # noqa: BLE001 — битая книга: пусть попробует markitdown
            return 0.0
        return 0.9 if sheets else 0.0

    async def parse(self, blob: FileBlob, ctx: IngestContext) -> AsyncIterator[Record]:
        for sheet_name, frame in blob.tables:
            headers = [str(column) for column in frame.columns]
            rows = [[str(cell) for cell in row] for row in frame.values.tolist()]
            if len(rows) < MIN_ROWS:
                continue
            kind = classify_sheet(headers, rows)
            place = f"лист «{sheet_name}»"

            if kind == "qa":
                question_at, answer_at = detect_columns(headers, rows)
                for index, row in enumerate(rows, start=1):
                    question = row[question_at].strip() if question_at < len(row) else ""
                    answer = row[answer_at].strip() if answer_at < len(row) else ""
                    if not question or not answer:
                        continue
                    yield ArticleRecord(
                        question=question, answer=answer,
                        category=ctx.params.get("category", "") or sheet_name,
                        line=ctx.params.get("line"),
                        audience=ctx.params.get("audience", "all"),
                        origin=Provenance(file=blob.name, locator=f"{place}, строка {index}",
                                          source_name=self.name, authored=True),
                    )
            elif kind == "feedback":
                rating_at = _has(headers, RATING_COLUMNS)
                comment_at = _has(headers, COMMENT_COLUMNS)
                operator_at = _has(headers, OPERATOR_COLUMNS)
                for index, row in enumerate(rows, start=1):
                    try:
                        rating = int(float(row[rating_at]))
                    except (ValueError, IndexError):
                        continue
                    yield FeedbackRecord(
                        rating=rating,
                        comment=row[comment_at].strip() if comment_at < len(row) else "",
                        operator=row[operator_at].strip() if 0 <= operator_at < len(row) else "",
                        origin=Provenance(file=blob.name, locator=f"{place}, строка {index}",
                                          source_name=self.name, authored=True),
                    )
```

- [ ] **Шаг 4: Убедиться, что тесты проходят**

Запуск: `uv run pytest tests/test_source_spreadsheet.py -q && uv run pytest -q`
Ожидание: PASS

- [ ] **Шаг 5: Коммит**

```bash
git add app/ingest/sources/spreadsheet.py tests/test_source_spreadsheet.py
git commit -m "feat(ingest): источник spreadsheet — Excel полистно, вопросы и отзывы"
```

---

### Task 9: Источник `json_records`

**Файлы:**
- Создать: `app/ingest/sources/json_records.py`
- Тест: `tests/test_source_json.py`

**Интерфейсы:**
- Отдаёт: `JsonRecordsSource` (`name="json_records"`, `priority=20`), функцию
  `map_fields(item: dict) -> tuple[str, str] | None` — какие ключи считать вопросом и
  ответом.

- [ ] **Шаг 1: Написать падающий тест**

```python
# tests/test_source_json.py
import json

import pytest

from app.ingest.blob import FileBlob
from app.ingest.registry import IngestContext
from app.ingest.sources.json_records import JsonRecordsSource, map_fields


def test_fields_are_mapped_by_synonyms():
    assert map_fields({"q": "Как?", "a": "Так."}) == ("Как?", "Так.")
    assert map_fields({"title": "Как?", "body": "Так."}) == ("Как?", "Так.")
    assert map_fields({"текст_обращения": "Как?", "решение": "Так."}) == ("Как?", "Так.")
    assert map_fields({"inn": "7701", "kpp": "770101"}) is None


@pytest.mark.asyncio
async def test_jsonl_and_nested_array():
    payload = json.dumps({"items": [
        {"question": "Как зарегистрироваться?", "answer": "Нужна ЭП."},
        {"question": "Как подписать контракт?", "answer": "В карточке."},
    ]}, ensure_ascii=False)
    source = JsonRecordsSource()
    blob = FileBlob(name="kb.json", data=payload.encode("utf-8"))
    assert await source.sniff(blob) > 0.7
    records = [r async for r in source.parse(blob, IngestContext())]
    assert [r.question for r in records] == ["Как зарегистрироваться?", "Как подписать контракт?"]
    assert records[0].origin.locator == "$.items[0]"
    assert records[0].origin.authored is True
```

- [ ] **Шаг 2: Убедиться, что тест падает**

Запуск: `uv run pytest tests/test_source_json.py -q`
Ожидание: FAIL, `ModuleNotFoundError`

- [ ] **Шаг 3: Написать минимальную реализацию**

```python
# app/ingest/sources/json_records.py
"""JSON и JSONL: массив объектов с вопросом и ответом.

Массив ищем не только в корне: выгрузки приезжают завёрнутыми — {"items": [...]},
{"data": {"faq": [...]}}. Обходим дерево до первого списка словарей, у которых
распознаются поля вопроса и ответа.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

from app.ingest.blob import FileBlob
from app.ingest.records import ArticleRecord, Provenance
from app.ingest.registry import IngestContext, register

QUESTION_KEYS = ("question", "q", "title", "вопрос", "заголовок", "тема",
                 "текст_обращения", "текст обращения", "обращение")
ANSWER_KEYS = ("answer", "a", "body", "text", "ответ", "решение", "описание", "response")
MAX_DEPTH = 6


def map_fields(item: dict) -> tuple[str, str] | None:
    lowered = {str(key).strip().lower(): value for key, value in item.items()}
    question = next((lowered[key] for key in QUESTION_KEYS if lowered.get(key)), None)
    answer = next((lowered[key] for key in ANSWER_KEYS if lowered.get(key)), None)
    if not question or not answer:
        return None
    return str(question).strip(), str(answer).strip()


def _find_records(node, path: str = "$", depth: int = 0) -> tuple[list[dict], str]:
    """Первый список словарей, из которых мапятся поля. Возвращает (список, путь)."""
    if depth > MAX_DEPTH:
        return [], path
    if isinstance(node, list):
        items = [item for item in node if isinstance(item, dict)]
        if items and any(map_fields(item) for item in items[:20]):
            return items, path
        for index, item in enumerate(node[:20]):
            found, found_path = _find_records(item, f"{path}[{index}]", depth + 1)
            if found:
                return found, found_path
    if isinstance(node, dict):
        for key, value in node.items():
            found, found_path = _find_records(value, f"{path}.{key}", depth + 1)
            if found:
                return found, found_path
    return [], path


@register
class JsonRecordsSource:
    name = "json_records"
    priority = 20

    def _load(self, blob: FileBlob):
        raw = blob.decoded().strip()
        if blob.suffix == ".jsonl" or raw.startswith("{") and "\n{" in raw:
            return [json.loads(line) for line in raw.splitlines() if line.strip()]
        return json.loads(raw)

    async def sniff(self, blob: FileBlob) -> float:
        if blob.suffix not in (".json", ".jsonl"):
            return 0.0
        try:
            items, _ = _find_records(self._load(blob))
        except (ValueError, TypeError):
            return 0.0
        return 0.9 if items else 0.0

    async def parse(self, blob: FileBlob, ctx: IngestContext) -> AsyncIterator[ArticleRecord]:
        items, path = _find_records(self._load(blob))
        for index, item in enumerate(items):
            mapped = map_fields(item)
            if not mapped:
                continue
            question, answer = mapped
            yield ArticleRecord(
                question=question, answer=answer,
                category=str(item.get("category") or ctx.params.get("category", "")),
                line=item.get("line") or ctx.params.get("line"),
                audience=str(item.get("audience") or ctx.params.get("audience", "all")),
                origin=Provenance(file=blob.name, locator=f"{path}[{index}]",
                                  source_name=self.name, authored=True),
            )
```

- [ ] **Шаг 4: Убедиться, что тесты проходят**

Запуск: `uv run pytest tests/test_source_json.py -q && uv run pytest -q`
Ожидание: PASS

- [ ] **Шаг 5: Коммит**

```bash
git add app/ingest/sources/json_records.py tests/test_source_json.py
git commit -m "feat(ingest): источник json_records с поиском массива в глубине"
```

---

### Task 10: Источник `pdf_figures` и `image_folder`

**Файлы:**
- Создать: `app/ingest/sources/pdf_figures.py`
- Создать: `app/ingest/sources/image_folder.py`
- Тест: `tests/test_source_images.py`

**Интерфейсы:**
- Потребляет: `extract_document` (docling через `app/adapters/docparse.py`),
  `store_assets`, `bind_assets` (Task 5), `IngestContext.siblings`.
- Отдаёт: `PdfFiguresSource` (`name="pdf_figures"`, `priority=30`),
  `ImageFolderSource` (`name="image_folder"`, `priority=5`).

- [ ] **Шаг 1: Написать падающий тест**

```python
# tests/test_source_images.py
import pytest

from app.ingest.blob import FileBlob
from app.ingest.registry import IngestContext
from app.ingest.sources.image_folder import ImageFolderSource, caption_from_name

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


def test_caption_comes_from_file_name():
    assert caption_from_name("01_kak-podpisat-kontrakt.png") == "Как подписать контракт"
    assert caption_from_name("screenshot 2026-09-11.png") == "Screenshot 2026 09 11"


@pytest.mark.asyncio
async def test_image_becomes_visual_answer():
    source = ImageFolderSource()
    blob = FileBlob(name="kak-vygruzit-upd.png", data=PNG)
    assert await source.sniff(blob) > 0.5
    records = [r async for r in source.parse(blob, IngestContext())]
    assert len(records) == 1
    assert records[0].question == "Как выгрузить упд"
    assert records[0].answer == ""          # ответ — сама картинка
    assert records[0].origin.authored is True
```

- [ ] **Шаг 2: Убедиться, что тест падает**

Запуск: `uv run pytest tests/test_source_images.py -q`
Ожидание: FAIL, `ModuleNotFoundError`

- [ ] **Шаг 3: Написать минимальную реализацию**

```python
# app/ingest/sources/image_folder.py
"""Папка скриншотов: картинка сама по себе ответ.

В выгрузках поддержки на «где эта кнопка» отвечают снимком экрана, и вопрос
написан в имени файла — «kak-vygruzit-upd.png». Текста в такой статье нет, и это
нормально: ответ показывается картинкой.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator

from app.ingest.blob import IMAGE_SUFFIXES, FileBlob
from app.ingest.records import ArticleRecord, AssetRecord, Provenance, Record
from app.ingest.registry import IngestContext, register

_TRANSLIT = str.maketrans({
    "a": "а", "b": "б", "v": "в", "g": "г", "d": "д", "e": "е", "z": "з", "i": "и",
    "k": "к", "l": "л", "m": "м", "n": "н", "o": "о", "p": "п", "r": "р", "s": "с",
    "t": "т", "u": "у", "f": "ф", "y": "ы",
})
_SEPARATORS = re.compile(r"[_\-.]+")
_LEADING_INDEX = re.compile(r"^\d{1,3}[_\-. ]+")


def caption_from_name(name: str) -> str:
    """Имя файла → человеческая подпись. Латиница транслитерируется обратно."""
    stem = _LEADING_INDEX.sub("", name.rsplit(".", 1)[0])
    words = _SEPARATORS.sub(" ", stem).strip()
    if re.fullmatch(r"[a-zA-Z0-9 ]+", words) and re.search(r"[a-zA-Z]{3,}", words):
        cyrillic = words.lower().translate(_TRANSLIT)
        if re.search(r"[а-яё]{3,}", cyrillic):
            words = cyrillic
    return words[:1].upper() + words[1:]


@register
class ImageFolderSource:
    name = "image_folder"
    priority = 5

    async def sniff(self, blob: FileBlob) -> float:
        return 0.8 if blob.suffix in IMAGE_SUFFIXES else 0.0

    async def parse(self, blob: FileBlob, ctx: IngestContext) -> AsyncIterator[Record]:
        caption = caption_from_name(blob.name)
        origin = Provenance(file=blob.name, locator="файл целиком",
                            source_name=self.name, authored=True)
        yield AssetRecord(data=blob.data, caption=caption,
                          anchor={"ref": blob.name}, origin=origin)
        yield ArticleRecord(
            question=caption, answer="",
            category=ctx.params.get("category", ""),
            line=ctx.params.get("line"),
            audience=ctx.params.get("audience", "all"),
            origin=origin,
        )
```

```python
# app/ingest/sources/pdf_figures.py
"""PDF с иллюстрациями: docling размечает страницу и отдаёт картинки с подписями.

Работает только при поднятом профиле `docs` — сервис разбора необязателен.
Когда его нет, sniff отдаёт ноль, и PDF достаётся источнику document текстом.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from app.ingest.blob import FileBlob
from app.ingest.chunking import MAX_CHUNK_CHARS, split_sections
from app.ingest.questions import extract_keywords, fallback_question
from app.ingest.records import ArticleRecord, AssetRecord, Provenance, Record
from app.ingest.registry import IngestContext, register


@register
class PdfFiguresSource:
    name = "pdf_figures"
    priority = 30

    async def sniff(self, blob: FileBlob) -> float:
        if blob.suffix != ".pdf":
            return 0.0
        # Без сервиса разбора картинок нет вовсе — пусть PDF идёт текстом
        return 0.85

    async def parse(self, blob: FileBlob, ctx: IngestContext) -> AsyncIterator[Record]:
        from app.services.ingest import extract_document

        if ctx.rt is None or getattr(ctx.rt, "docparse", None) is None:
            from app.ingest.sources.base import SourceMismatch
            raise SourceMismatch("сервис разбора PDF не поднят")

        parsed = await extract_document(ctx.rt, blob.name, blob.data,
                                        do_ocr=bool(ctx.params.get("do_ocr")))
        for picture in parsed.pictures:
            yield AssetRecord(
                data=picture.data, caption=picture.caption,
                anchor={"figure": picture.figure, "page": picture.page},
                origin=Provenance(file=blob.name, locator=f"стр. {picture.page}",
                                  source_name=self.name, authored=True),
            )
        for index, section in enumerate(split_sections(parsed.text, MAX_CHUNK_CHARS), start=1):
            yield ArticleRecord(
                question=fallback_question(section), answer=section.body,
                category=ctx.params.get("category", "") or section.title,
                line=ctx.params.get("line"),
                audience=ctx.params.get("audience", "all"),
                keywords=extract_keywords(section),
                origin=Provenance(file=blob.name, locator=f"фрагмент {index}",
                                  source_name=self.name, authored=False),
            )
```

- [ ] **Шаг 4: Убедиться, что тесты проходят**

Запуск: `uv run pytest tests/test_source_images.py tests/test_media_and_images.py -q && uv run pytest -q`
Ожидание: PASS

- [ ] **Шаг 5: Коммит**

```bash
git add app/ingest/sources/pdf_figures.py app/ingest/sources/image_folder.py tests/test_source_images.py
git commit -m "feat(ingest): источники pdf_figures и image_folder, картинка как ответ"
```

---

### Task 11: Источник `llm_fallback`

**Файлы:**
- Создать: `app/ingest/sources/llm_fallback.py`
- Изменить: `app/dynamic_settings.py` (ключ `ingest.llm_fallback_enabled`)
- Тест: `tests/test_source_llm_fallback.py`

**Интерфейсы:**
- Потребляет: `ctx.rt.dyn` (настройки), `app/services/agent.py::build_model`.
- Отдаёт: `LlmFallbackSource` (`name="llm_fallback"`, `priority=-100`).

- [ ] **Шаг 1: Написать падающий тест**

```python
# tests/test_source_llm_fallback.py
import pytest

from app.ingest.blob import FileBlob
from app.ingest.registry import IngestContext
from app.ingest.sources.base import SourceMismatch
from app.ingest.sources.llm_fallback import LlmFallbackSource, records_from_payload


def test_payload_becomes_records():
    payload = [{"question": "Как войти?", "answer": "Через ЭП."}]
    records = records_from_payload(payload, file="strange.dat", source_name="llm_fallback")
    assert records[0].question == "Как войти?"
    # Вопрос и ответ вытащила модель, а не автор: дословный ответ запрещён
    assert records[0].origin.authored is False


@pytest.mark.asyncio
async def test_refuses_without_llm():
    source = LlmFallbackSource()
    blob = FileBlob(name="strange.dat", data=b"nonsense")
    ctx = IngestContext(rt=None)
    with pytest.raises(SourceMismatch):
        [r async for r in source.parse(blob, ctx)]


@pytest.mark.asyncio
async def test_sniff_is_last_resort():
    source = LlmFallbackSource()
    assert source.priority < 0
    assert await source.sniff(FileBlob(name="x.dat", data=b"y")) == pytest.approx(0.16, abs=0.05)
```

- [ ] **Шаг 2: Убедиться, что тест падает**

Запуск: `uv run pytest tests/test_source_llm_fallback.py -q`
Ожидание: FAIL, `ModuleNotFoundError`

- [ ] **Шаг 3: Написать минимальную реализацию**

```python
# app/ingest/sources/llm_fallback.py
"""Последний в очереди: модель сама размечает непонятный файл.

Дорого и недетерминированно, поэтому и последний — приоритет отрицательный,
уверенность чуть выше порога отсечения. Берёт первые 40 КБ текста: если формат
не распознался, нам нужно понять его устройство, а не перемолоть весь объём.
Записи помечаются authored=False — дословно такой ответ отдавать нельзя.
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator

from app.ingest.blob import FileBlob
from app.ingest.records import ArticleRecord, Provenance
from app.ingest.registry import IngestContext, register
from app.ingest.sources.base import SourceMismatch

log = logging.getLogger(__name__)

MAX_SAMPLE_CHARS = 40_000
PROMPT = (
    "Ниже фрагмент файла неизвестного формата из службы поддержки. Извлеки из него "
    "пары «вопрос пользователя — ответ поддержки». Верни JSON-массив объектов с "
    "ключами question и answer, без пояснений. Если пар нет, верни пустой массив."
)


def records_from_payload(payload: list, *, file: str, source_name: str) -> list[ArticleRecord]:
    records: list[ArticleRecord] = []
    for index, item in enumerate(payload):
        if not isinstance(item, dict):
            continue
        question = str(item.get("question") or "").strip()
        answer = str(item.get("answer") or "").strip()
        if not question or not answer:
            continue
        records.append(ArticleRecord(
            question=question, answer=answer,
            origin=Provenance(file=file, locator=f"фрагмент {index + 1}",
                              source_name=source_name, authored=False),
        ))
    return records


@register
class LlmFallbackSource:
    name = "llm_fallback"
    priority = -100

    async def sniff(self, blob: FileBlob) -> float:
        # Чуть выше MIN_CONFIDENCE: берётся только когда не взялся никто другой
        return 0.16

    async def parse(self, blob: FileBlob, ctx: IngestContext) -> AsyncIterator[ArticleRecord]:
        if ctx.rt is None:
            raise SourceMismatch("нет рантайма — модель недоступна")
        if not ctx.rt.dyn.get("ingest.llm_fallback_enabled"):
            raise SourceMismatch("разбор моделью выключен в настройках")

        from app.services.agent import build_model, llm_configured
        if not llm_configured(ctx.rt.dyn):
            raise SourceMismatch("LLM не настроена")

        try:
            sample = blob.text[:MAX_SAMPLE_CHARS]
        except Exception as exc:  # noqa: BLE001
            raise SourceMismatch(f"файл не читается: {exc}") from exc

        from pydantic_ai import Agent
        agent = Agent(build_model(ctx.rt.dyn), system_prompt=PROMPT)
        result = await agent.run(sample)
        try:
            payload = json.loads(str(result.output).strip().removeprefix("```json").removesuffix("```"))
        except ValueError as exc:
            raise SourceMismatch(f"модель вернула не JSON: {exc}") from exc
        if not isinstance(payload, list) or not payload:
            raise SourceMismatch("модель не нашла пар «вопрос — ответ»")

        for record in records_from_payload(payload, file=blob.name, source_name=self.name):
            yield record
```

Ключ настроек рядом с прочими `ingest.*` в `app/dynamic_settings.py`:

```python
    "ingest.llm_fallback_enabled": (
        True, "ingest",
        "Разбирать непонятные файлы моделью, когда не взялся ни один парсер",
    ),
```

- [ ] **Шаг 4: Убедиться, что тесты проходят**

Запуск: `uv run pytest tests/test_source_llm_fallback.py -q && uv run pytest -q`
Ожидание: PASS

- [ ] **Шаг 5: Коммит**

```bash
git add app/ingest/sources/llm_fallback.py app/dynamic_settings.py tests/test_source_llm_fallback.py
git commit -m "feat(ingest): запасной разбор непонятного файла моделью"
```

---

### Task 12: Конвейер записей в базу

**Файлы:**
- Создать: `app/ingest/pipeline.py`
- Тест: `tests/test_ingest_pipeline.py`

**Интерфейсы:**
- Потребляет: все типы записей, `store_assets`/`bind_assets` (Task 5), модели
  `KBArticle`, `Feedback`, `ChatSession`, `Message`.
- Отдаёт: `async def persist(rt, records, *, title, params, on_progress=None) -> dict`
  с ключами `created`, `feedback`, `dialogs`, `images`, `skipped`.

- [ ] **Шаг 1: Написать падающий тест**

```python
# tests/test_ingest_pipeline.py
import pytest
from sqlalchemy import select

from app.ingest.pipeline import persist
from app.ingest.records import ArticleRecord, DialogRecord, FeedbackRecord, Provenance
from app.models import ChatSession, Feedback, KBArticle


@pytest.mark.asyncio
async def test_articles_feedback_and_dialogs_land_in_their_tables(runtime):
    records = [
        ArticleRecord(question="Как войти?", answer="Через ЭП.",
                      origin=Provenance(file="qa.xlsx", locator="строка 1",
                                        source_name="qa_table", authored=True)),
        FeedbackRecord(rating=2, comment="Долго ждал", operator="Иван"),
        DialogRecord(messages=[("user", "Как войти?"), ("assistant", "Через ЭП.")], line=1),
    ]
    result = await persist(runtime, records, title="Выгрузка", params={"status": "draft"})
    assert result == {"created": 1, "feedback": 1, "dialogs": 1, "images": 0, "skipped": 0}

    async with runtime.db.session() as session:
        article = (await session.execute(select(KBArticle))).scalars().first()
        assert article.origin["authored"] is True
        assert article.origin["locator"] == "строка 1"
        assert article.status == "draft"
        assert (await session.execute(select(Feedback))).scalars().first().rating == 2
        imported = (await session.execute(select(ChatSession))).scalars().first()
        # Чужие диалоги не должны подменять собой статистику дашборда
        assert imported.channel == "import"


@pytest.mark.asyncio
async def test_empty_answer_without_image_is_skipped(runtime):
    result = await persist(runtime, [ArticleRecord(question="Где кнопка?", answer="")],
                           title="Пустая", params={})
    assert result["created"] == 0
    assert result["skipped"] == 1
```

Фикстура `runtime` уже есть в `tests/conftest.py` — использовать её, не заводя новую.

- [ ] **Шаг 2: Убедиться, что тест падает**

Запуск: `uv run pytest tests/test_ingest_pipeline.py -q`
Ожидание: FAIL, `ModuleNotFoundError: No module named 'app.ingest.pipeline'`

- [ ] **Шаг 3: Написать минимальную реализацию**

```python
# app/ingest/pipeline.py
"""Записи → база. Единственное место, которое пишет результат разбора.

Разделение с источниками не косметическое: пока запись в базу жила внутри
разбора, добавить формат значило тронуть и то и другое. Теперь источник ничего
не знает про SQLAlchemy, а конвейер — про markitdown.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable

from app.ingest.assets import bind_assets, has_visual_answer, store_assets
from app.ingest.records import (
    ArticleRecord, AssetRecord, DialogRecord, FeedbackRecord, Record,
)
from app.models import ChatSession, Feedback, KBArticle, Message
from app.utils import new_id

log = logging.getLogger(__name__)

# Канал импортированных диалогов. Аналитика и рейтинг операторов его отфильтруют,
# обучение классификатора линий — нет: чужие обращения ему как раз полезны.
IMPORT_CHANNEL = "import"


async def persist(rt, records: list[Record], *, title: str, params: dict,
                  on_progress: Callable[[int, int], Awaitable[None]] | None = None) -> dict:
    articles = [r for r in records if isinstance(r, ArticleRecord)]
    assets = [r for r in records if isinstance(r, AssetRecord)]
    feedback = [r for r in records if isinstance(r, FeedbackRecord)]
    dialogs = [r for r in records if isinstance(r, DialogRecord)]

    images = []
    if assets:
        slug = new_id()[:8]
        images = await store_assets(rt, slug, assets)
        bind_assets(articles, images)

    created = skipped = 0
    total = len(articles)
    async with rt.db.session() as session:
        for index, record in enumerate(articles, start=1):
            if not record.question.strip() or (
                not record.answer.strip() and not has_visual_answer(record)
            ):
                skipped += 1
                continue
            session.add(KBArticle(
                question=record.question, answer=record.answer,
                category=record.category or "Импорт",
                keywords=record.keywords,
                images=[image.as_dict() for image in record.images],
                line=record.line or 1,
                audience=record.audience,
                status=params.get("status", "draft"),
                source="import",
                origin=record.origin.as_dict(),
            ))
            created += 1
            if on_progress and index % 20 == 0:
                await on_progress(index, total)

        for review in feedback:
            session.add(Feedback(
                session_id="", rating=review.rating, comment=review.comment,
                operator_id=review.operator or "",
            ))

        for dialog in dialogs:
            chat = ChatSession(id=new_id(), channel=IMPORT_CHANNEL,
                               status="resolved" if dialog.resolved else "closed",
                               line=dialog.line)
            session.add(chat)
            for role, content in dialog.messages:
                session.add(Message(session_id=chat.id, role=role, content=content))

        await session.commit()

    if created:
        await rt.reindex_kb()
    return {"created": created, "feedback": len(feedback), "dialogs": len(dialogs),
            "images": len(images), "skipped": skipped}
```

Перед реализацией сверить имена полей `Feedback`, `ChatSession`, `Message` в
`app/models.py:94-120` и способ переиндексации в `app/services/kb.py` — подставить
фактические, а не написанные по памяти.

- [ ] **Шаг 4: Убедиться, что тесты проходят**

Запуск: `uv run pytest tests/test_ingest_pipeline.py -q && uv run pytest -q`
Ожидание: PASS

- [ ] **Шаг 5: Коммит**

```bash
git add app/ingest/pipeline.py tests/test_ingest_pipeline.py
git commit -m "feat(ingest): конвейер записей в базу — статьи, отзывы, диалоги"
```

---

### Task 13: Подключение реестра к ручкам и фоновой задаче

**Файлы:**
- Изменить: `app/services/ingest.py` (`ingest_document` идёт через реестр)
- Изменить: `app/routers/kb.py:305-360` (`/ingest/file` больше не отбраковывает формат
  по списку расширений)
- Изменить: `app/tasks/jobs.py:44-115` (задача передаёт байты файла в реестр)
- Тест: `tests/test_ingest_wiring.py`

**Интерфейсы:**
- Потребляет: `parse_blob`, `IngestContext` (Task 3), `persist` (Task 12).
- Отдаёт: `async def ingest_file(rt, filename, raw, *, title, params, on_progress=None)`
  — единая точка входа для роутера и фоновой задачи.

- [ ] **Шаг 1: Написать падающий тест**

```python
# tests/test_ingest_wiring.py
import pytest

from app.services.ingest import ingest_file

TABLE = (
    "| Вопрос | Ответ |\n|---|---|\n"
    "| Как зарегистрироваться? | Нужна ЭП. |\n"
    "| Как подписать контракт? | В карточке. |\n"
    "| Где найти УПД? | В разделе исполнения. |\n"
    "| Как сменить реквизиты? | В профиле. |\n"
    "| Куда жаловаться? | В ФАС. |\n"
)


@pytest.mark.asyncio
async def test_table_goes_through_registry_and_stays_authored(runtime):
    result = await ingest_file(runtime, "qa.md", TABLE.encode("utf-8"),
                               title="Выгрузка", params={"status": "active"})
    assert result["created"] == 5
    assert result["source"] == "qa_table"


@pytest.mark.asyncio
async def test_unknown_extension_is_not_rejected_upfront(runtime):
    # Раньше .dat отсекался списком расширений до всякого разбора
    result = await ingest_file(runtime, "dump.dat", TABLE.encode("utf-8"),
                               title="Странный", params={})
    assert result["created"] >= 1
```

- [ ] **Шаг 2: Убедиться, что тест падает**

Запуск: `uv run pytest tests/test_ingest_wiring.py -q`
Ожидание: FAIL, `ImportError: cannot import name 'ingest_file'`

- [ ] **Шаг 3: Написать минимальную реализацию**

В `app/services/ingest.py`:

```python
async def ingest_file(rt, filename: str, raw: bytes, *, title: str, params: dict,
                      on_progress=None) -> dict:
    """Файл → записи → база. Формат выбирает реестр, а не список расширений.

    Список расширений остался только подсказкой в интерфейсе: отсекать по нему
    до разбора значит отказать выгрузке с непривычным именем, а на хакатоне
    именно такая и приедет.
    """
    from app.ingest import sources  # noqa: F401 — импорт регистрирует источники
    from app.ingest.blob import FileBlob
    from app.ingest.pipeline import persist
    from app.ingest.registry import IngestContext, parse_blob, pick_source

    blob = FileBlob(name=filename, data=raw)
    ctx = IngestContext(rt=rt, params=params)
    picked = await pick_source(blob)
    records = await parse_blob(blob, ctx)
    if not records:
        return {"created": 0, "feedback": 0, "dialogs": 0, "images": 0, "skipped": 0,
                "source": "", "error": "ни один источник не разобрал файл"}
    result = await persist(rt, records, title=title, params=params, on_progress=on_progress)
    result["source"] = picked.name if picked else ""
    return result
```

`ingest_document` (текстом) остаётся как есть — он вызывается из `/api/kb/ingest`, где
текст уже пришёл строкой; внутри переводит текст в `FileBlob(name=f"{title}.md")` и
зовёт тот же путь.

В `app/routers/kb.py` проверка расширения превращается в предупреждение: файл с
незнакомым расширением принимается, а 415 возвращается только если реестр никого не
нашёл (сообщение из `result["error"]`).

В `app/tasks/jobs.py` вызов `ingest_document(...)` заменяется на `ingest_file(...)`
с байтами из хранилища (`source_key`), прогресс прокидывается тем же `on_progress`.

- [ ] **Шаг 4: Убедиться, что тесты проходят**

Запуск: `uv run pytest tests/test_ingest_wiring.py tests/test_api_kb.py -q && uv run pytest -q`
Ожидание: PASS

- [ ] **Шаг 5: Коммит**

```bash
git add app/services/ingest.py app/routers/kb.py app/tasks/jobs.py tests/test_ingest_wiring.py
git commit -m "feat(ingest): импорт идёт через реестр источников, расширение больше не приговор"
```

---

### Task 14: Разведчик данных

**Файлы:**
- Создать: `app/tools/__init__.py`
- Создать: `app/tools/recon.py`
- Изменить: `app/routers/kb.py:441` (`/ingest/preview` зовёт ту же функцию)
- Тест: `tests/test_recon.py`

**Интерфейсы:**
- Потребляет: `FileBlob`, `registry.ranked`, `parse_blob`.
- Отдаёт: `async def inspect_path(path, rt=None) -> list[FileReport]`,
  датакласс `FileReport(name, size, suffix, source, confidence, counts, samples, note)`,
  `def render(reports) -> str`, точку входа `python -m app.tools.recon`.

- [ ] **Шаг 1: Написать падающий тест**

```python
# tests/test_recon.py
import pytest

from app.tools.recon import inspect_path, render

TABLE = (
    "| Вопрос | Ответ |\n|---|---|\n"
    "| Как зарегистрироваться? | Нужна ЭП. |\n"
    "| Как подписать контракт? | В карточке. |\n"
    "| Где найти УПД? | В разделе исполнения. |\n"
    "| Как сменить реквизиты? | В профиле. |\n"
    "| Куда жаловаться? | В ФАС. |\n"
)


@pytest.mark.asyncio
async def test_report_names_source_and_counts(tmp_path):
    (tmp_path / "qa.md").write_text(TABLE, encoding="utf-8")
    (tmp_path / "shot.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 16)

    reports = await inspect_path(tmp_path)
    by_name = {report.name: report for report in reports}
    assert by_name["qa.md"].source == "qa_table"
    assert by_name["qa.md"].counts["ArticleRecord"] == 5
    assert by_name["qa.md"].samples
    assert by_name["shot.png"].source == "image_folder"

    text = render(reports)
    assert "qa.md" in text and "qa_table" in text


@pytest.mark.asyncio
async def test_unreadable_file_is_reported_not_raised(tmp_path):
    (tmp_path / "junk.bin").write_bytes(b"\x00\x01\x02\x03")
    reports = await inspect_path(tmp_path)
    assert reports[0].source in ("", "llm_fallback")
    assert reports[0].note
```

- [ ] **Шаг 2: Убедиться, что тест падает**

Запуск: `uv run pytest tests/test_recon.py -q`
Ожидание: FAIL, `ModuleNotFoundError: No module named 'app.tools'`

- [ ] **Шаг 3: Написать минимальную реализацию**

```python
# app/tools/recon.py
"""Разведка выданных данных: что нам дали и чем мы это прочитаем.

Запуск:  uv run python -m app.tools.recon <папка|файл> [--json]

Первое, что запускается, когда организаторы выкладывают архив. Ничего не пишет
в базу: только смотрит, кто из источников возьмётся за файл и что получится.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from app.ingest import sources  # noqa: F401 — импорт регистрирует источники
from app.ingest.blob import FileBlob
from app.ingest.records import ArticleRecord
from app.ingest.registry import IngestContext, parse_blob, registry

MAX_SAMPLES = 3
SKIP_DIRS = {".git", "__pycache__", ".venv", "node_modules"}


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

    def as_dict(self) -> dict:
        return {"name": self.name, "size": self.size, "suffix": self.suffix,
                "source": self.source, "confidence": round(self.confidence, 2),
                "counts": self.counts, "samples": self.samples, "note": self.note}


async def inspect_blob(blob: FileBlob, rt=None) -> FileReport:
    report = FileReport(name=blob.name, size=len(blob.data), suffix=blob.suffix)
    ranked = await registry.ranked(blob)
    if not ranked:
        report.note = "ни один источник не взялся"
        return report
    report.confidence, source = ranked[0]
    report.source = source.name

    try:
        records = await parse_blob(blob, IngestContext(rt=rt))
    except Exception as exc:  # noqa: BLE001 — разведка не имеет права падать
        report.note = f"разбор упал: {type(exc).__name__}: {exc}"
        return report

    report.counts = dict(Counter(type(record).__name__ for record in records))
    for record in records[:MAX_SAMPLES]:
        if isinstance(record, ArticleRecord):
            report.samples.append(f"{record.question[:70]} → {record.answer[:70]}")
    if not records:
        report.note = "источник не дал ни одной записи"
    return report


async def inspect_path(path: Path | str, rt=None) -> list[FileReport]:
    path = Path(path)
    files = [path] if path.is_file() else [
        item for item in sorted(path.rglob("*"))
        if item.is_file() and not any(part in SKIP_DIRS for part in item.parts)
    ]
    return [await inspect_blob(FileBlob(name=item.name, data=item.read_bytes()), rt)
            for item in files]


def render(reports: list[FileReport]) -> str:
    lines = [f"Файлов: {len(reports)}", ""]
    for report in reports:
        head = f"  {report.name}  ({report.size / 1024:.0f} КБ)"
        if report.source:
            head += f"  → {report.source} [{report.confidence:.2f}]"
        lines.append(head)
        if report.counts:
            lines.append("      записи: " + ", ".join(
                f"{name} × {count}" for name, count in report.counts.items()))
        for sample in report.samples:
            lines.append(f"      · {sample}")
        if report.note:
            lines.append(f"      ! {report.note}")
    unread = [r.name for r in reports if not r.counts]
    if unread:
        lines += ["", "Не разобрано: " + ", ".join(unread)]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Разведка выданных данных")
    parser.add_argument("path", type=Path)
    parser.add_argument("--json", action="store_true", help="машиночитаемый вывод")
    args = parser.parse_args()

    reports = asyncio.run(inspect_path(args.path))
    if args.json:
        print(json.dumps([report.as_dict() for report in reports], ensure_ascii=False, indent=2))
    else:
        print(render(reports))
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

Ручка `/api/kb/ingest/preview` начинает звать `inspect_blob` и отдавать `FileReport`
как JSON — админка получает предпросмотр без второй реализации.

- [ ] **Шаг 4: Убедиться, что тесты проходят**

Запуск: `uv run pytest tests/test_recon.py -q && uv run pytest -q`
Ожидание: PASS

- [ ] **Шаг 5: Коммит**

```bash
git add app/tools tests/test_recon.py app/routers/kb.py
git commit -m "feat(ingest): разведчик выданных данных и предпросмотр импорта"
```

---

### Task 15: Дословный ответ

**Файлы:**
- Создать: `app/services/verbatim.py`
- Изменить: `app/dynamic_settings.py` (три ключа `answer.verbatim_*`)
- Изменить: `app/services/chat.py:290-300` (шаг между уточнением и агентом)
- Тест: `tests/test_verbatim.py`

**Интерфейсы:**
- Потребляет: `RetrievedDoc` (`app/services/retrieval.py:38`), `KBArticle.origin`
  (Task 1), `rt.dyn`.
- Отдаёт: `def pick_verbatim(dyn, question, docs, origins) -> RetrievedDoc | None`,
  `def similarity(first, second) -> float`.

- [ ] **Шаг 1: Написать падающий тест**

```python
# tests/test_verbatim.py
from app.services.retrieval import RetrievedDoc
from app.services.verbatim import pick_verbatim, similarity


class FakeDyn:
    def __init__(self, **values):
        self.values = {"answer.verbatim_enabled": True,
                       "answer.verbatim_similarity": 0.75,
                       "answer.verbatim_score": 0.6, **values}

    def get(self, key):
        return self.values[key]


def _doc(question, score=0.9):
    return RetrievedDoc(article_id="a1", question=question, answer="Нужна ЭП и заявка.",
                        category="Регистрация", line=1, score=score)


def test_exact_authored_question_answers_verbatim():
    docs = [_doc("Как зарегистрироваться на портале?")]
    picked = pick_verbatim(FakeDyn(), "Как зарегистрироваться на портале?", docs,
                           origins={"a1": {"authored": True}})
    assert picked is not None
    assert picked.answer == "Нужна ЭП и заявка."


def test_derived_article_never_answers_verbatim():
    docs = [_doc("Как зарегистрироваться на портале?")]
    assert pick_verbatim(FakeDyn(), "Как зарегистрироваться на портале?", docs,
                         origins={"a1": {"authored": False}}) is None


def test_different_question_falls_through_to_agent():
    docs = [_doc("Как зарегистрироваться на портале?")]
    assert pick_verbatim(FakeDyn(), "Почему не приходит УПД?", docs,
                         origins={"a1": {"authored": True}}) is None


def test_toggle_off_disables_the_whole_path():
    docs = [_doc("Как зарегистрироваться на портале?")]
    assert pick_verbatim(FakeDyn(**{"answer.verbatim_enabled": False}),
                         "Как зарегистрироваться на портале?", docs,
                         origins={"a1": {"authored": True}}) is None


def test_similarity_ignores_case_and_punctuation():
    assert similarity("Как выгрузить УПД?", "как выгрузить упд") > 0.9
    assert similarity("Как выгрузить УПД?", "Как создать сессию?") < 0.5
```

- [ ] **Шаг 2: Убедиться, что тест падает**

Запуск: `uv run pytest tests/test_verbatim.py -q`
Ожидание: FAIL, `ModuleNotFoundError: No module named 'app.services.verbatim'`

- [ ] **Шаг 3: Написать минимальную реализацию**

```python
# app/services/verbatim.py
"""Дословный ответ: когда вопрос уже отвечен в выданных данных.

Правило простое: если верхняя статья — авторская пара «вопрос — ответ» и её
вопрос почти совпадает с заданным, отдаём ответ как написано. Пересказ чужого
готового ответа своими словами — потеря, а не польза: в нём формулировки
регламента, номера разделов и порядок шагов.

Шаг детерминированный и не зовёт модель: так он работает и в mock-режиме, то
есть на защите без интернета.
"""

from __future__ import annotations

import re

_WORD = re.compile(r"[а-яёa-z0-9]+")


def _tokens(text: str) -> set[str]:
    return set(_WORD.findall(text.lower().replace("ё", "е")))


def similarity(first: str, second: str) -> float:
    """Жаккар по словам. Не эмбеддинги: нужна дешёвая проверка «это тот же вопрос»."""
    left, right = _tokens(first), _tokens(second)
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def pick_verbatim(dyn, question: str, docs: list, origins: dict[str, dict]) -> object | None:
    """Верхняя статья, ответ которой можно отдать дословно, либо None."""
    if not dyn.get("answer.verbatim_enabled") or not docs:
        return None
    top = docs[0]
    origin = origins.get(top.article_id) or {}
    if not origin.get("authored"):
        return None
    if top.score < float(dyn.get("answer.verbatim_score")):
        return None
    if similarity(question, top.question) < float(dyn.get("answer.verbatim_similarity")):
        return None
    if not (top.answer or "").strip():
        return None
    return top
```

Ключи в `app/dynamic_settings.py`:

```python
    "answer.verbatim_enabled": (
        True, "agent",
        "Отдавать ответ дословно, когда вопрос совпал с авторской парой из выгрузки",
    ),
    "answer.verbatim_similarity": (
        0.75, "agent",
        "Насколько вопрос должен совпасть с вопросом статьи, чтобы ответить дословно",
    ),
    "answer.verbatim_score": (
        0.60, "agent", "Минимальная оценка поиска для дословного ответа",
    ),
```

В `app/services/chat.py`, между блоком уточнения (3b) и `# 4. ReAct-агент`:

```python
        # 3c. Вопрос уже отвечен в выданных данных — отдаём ответ как написано.
        #     Пересказывать авторскую формулировку значит терять номера разделов и
        #     порядок шагов, ради которых её и писали.
        verbatim = await self._maybe_verbatim(session_id, text, probe, started)
        if verbatim is not None:
            return verbatim
```

Метод рядом с `_maybe_clarify`:

```python
    async def _maybe_verbatim(self, session_id: str, text: str, docs: list | None,
                              started: float) -> dict | None:
        if not docs:
            return None
        origins = await self._origins_of([doc.article_id for doc in docs[:1]])
        top = verbatim.pick_verbatim(self.rt.dyn, text, docs, origins)
        if top is None:
            return None
        citations = [{"article_id": top.article_id, "question": top.question,
                      "score": top.score}]
        await self._attach_citation_images(citations)
        await self._stream_answer(session_id, top.answer)
        meta = {"confidence": top.score, "answered": True, "escalated": False,
                "tool_trace": ["verbatim"], "verbatim": True,
                "latency_ms": int((time.perf_counter() - started) * 1000),
                "citations": citations}
        msg = await self._save_message(session_id, "assistant", top.answer, meta=meta)
        return {"status": "answered", "message": self._msg_event(msg)}
```

`_origins_of` читает `KBArticle.origin` тем же приёмом, что `_attach_citation_images`
читает `KBArticle.images` (`chat.py:404-425`) — одним запросом по списку id.

- [ ] **Шаг 4: Убедиться, что тесты проходят**

Запуск: `uv run pytest tests/test_verbatim.py tests/test_api_chat.py -q && uv run pytest -q`
Ожидание: PASS

- [ ] **Шаг 5: Прогнать регресс качества**

Запуск: `uv run pytest tests/test_quality.py -q`
Ожидание: PASS. Метрики золотого набора не должны просесть: дословный путь включается
только на авторских статьях, которых в сид-наборе нет.

- [ ] **Шаг 6: Коммит**

```bash
git add app/services/verbatim.py app/services/chat.py app/dynamic_settings.py tests/test_verbatim.py
git commit -m "feat(chat): дословный ответ на вопрос, уже отвеченный в выданных данных"
```

---

### Task 16: Документация для команды

**Файлы:**
- Изменить: `README.md` (раздел про структурирование БЗ)
- Создать: `docs/INGEST.md`

**Интерфейсы:**
- Потребляет: всё предыдущее.
- Отдаёт: инструкцию «как добавить источник за 15 минут» и «что делать, когда выдали архив».

- [ ] **Шаг 1: Написать `docs/INGEST.md`**

Содержание: схема «файл → источник → записи → конвейер → база» на пять строк; таблица
источников с уверенностями; готовый шаблон нового источника (копируемый класс с
`sniff`/`parse` и тестом к нему); порядок действий в день выдачи данных —
`uv run python -m app.tools.recon <папка>`, чтение отчёта, добавление источника под
непонятый формат, импорт через админку.

- [ ] **Шаг 2: Обновить README**

В разделе про структурирование БЗ заменить перечисление форматов на ссылку
`docs/INGEST.md` и одну фразу про реестр источников.

- [ ] **Шаг 3: Прогнать весь набор и линт**

Запуск: `uv run pytest -q && uv run ruff check .`
Ожидание: PASS

- [ ] **Шаг 4: Коммит**

```bash
git add README.md docs/INGEST.md
git commit -m "docs: как добавить источник данных и что делать в день выдачи архива"
```

---

## Что дальше

Конвейер шагов вместо god-объекта `ChatService` (раздел 7 спеки) — отдельный план,
пишется после того, как этот выполнен. Он не блокирует приём данных: дословный ответ
встаёт в нынешний `handle_user_message` одним вызовом (Task 15) и переедет в шаг
конвейера без изменений в поведении.
