"""Реестр источников: кто такой источник, кому отдать файл и что делать при отказе.

Контракт источника живёт здесь, а не в пакете `sources`, намеренно. Пока протокол
лежал в `sources/base.py`, импорт реестра тянул за собой пакет источников, каждый
источник импортировал реестр обратно — и на недостроенном модуле падал ImportError,
который глушился общим `except`. Итог был худшим из возможных: `import
app.ingest.registry` давал ПУСТОЙ реестр без единой ошибки, а работало всё только
потому, что вызывающий случайно импортировал `sources` первым.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.runtime import AppRuntime
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from app.ingest.blob import FileBlob
from app.ingest.records import Record

log = logging.getLogger(__name__)

# Ниже этого порога источник считается отказавшимся. Запасной разбор моделью
# отдаёт чуть больше — так он берётся ровно тогда, когда не взялся никто.
MIN_CONFIDENCE = 0.15


class SourceMismatch(Exception):
    """Источник взялся за файл и понял, что это не его формат."""


@runtime_checkable
class Source(Protocol):
    name: str
    # Тай-брейк при равной уверенности. У запасного разбора моделью он
    # отрицательный, чтобы тот никогда не перехватывал файл у настоящего источника.
    priority: int

    async def sniff(self, blob: FileBlob) -> float:
        """0..1 — насколько уверенно источник берётся за файл."""
        ...

    def parse(self, blob: FileBlob, ctx: object) -> AsyncIterator[Record]:
        """Асинхронный генератор записей."""
        ...


@dataclass
class IngestContext:
    """Что источнику нужно кроме самого файла."""

    rt: AppRuntime | None = None
    params: dict[str, Any] = field(default_factory=dict)
    # Соседние файлы из той же папки: картинки, на которые ссылается таблица
    siblings: dict[str, bytes] = field(default_factory=dict)


class Registry:
    def __init__(self) -> None:
        self.sources: list[Source] = []
        self._loaded = False

    def add(self, source: Source) -> Source:
        self.sources.append(source)
        return source

    def ensure_loaded(self) -> None:
        """Подтянуть модули источников, если этого ещё не сделал импорт.

        Вызывается на входе в любой запрос к реестру, а не при импорте модуля:
        порядок импортов перестаёт что-либо значить, и пустой реестр больше не
        может притвориться «просто никто не взялся».
        """
        if self._loaded:
            return
        self._loaded = True
        import app.ingest.sources  # noqa: F401 — импорт и есть регистрация

    async def ranked(self, blob: FileBlob) -> list[tuple[float, Source]]:
        """Источники, готовые взяться за файл, — по убыванию уверенности."""
        self.ensure_loaded()
        scored: list[tuple[float, Source]] = []
        for source in self.sources:
            try:
                confidence = float(await source.sniff(blob))
            except Exception:
                log.exception("Источник %s упал на распознавании", source.name)
                continue
            if confidence >= MIN_CONFIDENCE:
                scored.append((confidence, source))
        scored.sort(key=lambda item: (-item[0], -getattr(item[1], "priority", 0)))
        return scored


registry = Registry()


def register(cls: type) -> type:
    """Декоратор класса: источник регистрируется самим фактом импорта модуля."""
    registry.add(cls())
    return cls


async def pick_source(blob: FileBlob) -> Source | None:
    ranked = await registry.ranked(blob)
    return ranked[0][1] if ranked else None


async def parse_blob(blob: FileBlob, ctx: IngestContext) -> list[Record]:
    """Разобрать файл первым источником, который справился.

    Отказ (SourceMismatch), падение и пустая выдача для вызывающего означают одно:
    пробуем следующего. Пустая выдача — тоже отказ, потому что файл, из которого
    не вышло ни одной записи, стоит показать следующему источнику, а не считать
    разобранным.
    """
    for confidence, source in await registry.ranked(blob):
        try:
            records = [record async for record in source.parse(blob, ctx)]
        except SourceMismatch as exc:
            log.info("Источник %s отказался от %s: %s", source.name, blob.name, exc)
            continue
        except Exception:
            log.exception("Источник %s упал на разборе %s", source.name, blob.name)
            continue
        if records:
            log.info(
                "Файл %s разобран источником %s (уверенность %.2f), записей %d",
                blob.name,
                source.name,
                confidence,
                len(records),
            )
            return records
        log.info("Источник %s не дал записей из %s", source.name, blob.name)
    return []
