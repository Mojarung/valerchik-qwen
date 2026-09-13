"""Реестр источников: выбор по уверенности, отказ в пользу следующего, самозагрузка."""

import subprocess
import sys

import pytest

from app.ingest.blob import FileBlob
from app.ingest.records import ArticleRecord
from app.ingest.registry import (
    IngestContext,
    SourceMismatch,
    parse_blob,
    pick_source,
    registry,
)


@pytest.fixture
def isolated_registry():
    """Реестр глобальный — тест обязан вернуть его в исходное состояние."""
    saved = list(registry.sources)
    was_loaded = registry._loaded
    registry.sources.clear()
    registry._loaded = True  # не подтягивать настоящие источники в тесте
    yield registry
    registry.sources[:] = saved
    registry._loaded = was_loaded


def _stub(name: str, confidence: float, question: str, *, priority: int = 0, refuses: bool = False):
    class Stub:
        pass

    Stub.name = name
    Stub.priority = priority

    async def sniff(self, blob):
        return confidence

    async def parse(self, blob, ctx):
        if refuses:
            raise SourceMismatch("передумал")
        yield ArticleRecord(question=question, answer=f"ответ {question}")

    Stub.sniff = sniff
    Stub.parse = parse
    return Stub()


@pytest.mark.asyncio
async def test_most_confident_source_wins(isolated_registry):
    isolated_registry.add(_stub("weak", 0.2, "w"))
    isolated_registry.add(_stub("strong", 0.9, "s"))
    picked = await pick_source(FileBlob(name="x.txt", data=b"anything"))
    assert picked.name == "strong"


@pytest.mark.asyncio
async def test_mismatch_falls_through_to_next(isolated_registry):
    isolated_registry.add(_stub("refusing", 0.9, "r", refuses=True))
    isolated_registry.add(_stub("backup", 0.5, "b"))
    records = await parse_blob(FileBlob(name="x.txt", data=b"y"), IngestContext())
    assert [record.question for record in records] == ["b"]


@pytest.mark.asyncio
async def test_source_below_threshold_is_not_asked(isolated_registry):
    isolated_registry.add(_stub("shy", 0.1, "shy"))
    assert await pick_source(FileBlob(name="x.txt", data=b"y")) is None


@pytest.mark.asyncio
async def test_negative_priority_yields_at_equal_confidence(isolated_registry):
    """Запасной разбор моделью не должен перехватывать файл у настоящего источника."""
    isolated_registry.add(_stub("fallback", 0.5, "f", priority=-100))
    isolated_registry.add(_stub("normal", 0.5, "n", priority=0))
    picked = await pick_source(FileBlob(name="x.txt", data=b"y"))
    assert picked.name == "normal"


@pytest.mark.asyncio
async def test_broken_source_does_not_break_the_import(isolated_registry):
    class Broken:
        name = "broken"
        priority = 0

        async def sniff(self, blob):
            raise RuntimeError("я сломан")

        async def parse(self, blob, ctx):  # pragma: no cover — до него не дойдёт
            yield ArticleRecord(question="q", answer="a")

    isolated_registry.add(Broken())
    isolated_registry.add(_stub("healthy", 0.5, "h"))
    records = await parse_blob(FileBlob(name="x.txt", data=b"y"), IngestContext())
    assert [record.question for record in records] == ["h"]


def test_registry_fills_itself_whatever_the_import_order():
    """Голый импорт реестра обязан давать рабочий реестр, а не пустой.

    Пока протокол источника лежал в пакете sources, импорт реестра тянул пакет,
    источники импортировали реестр обратно, ImportError глушился общим except —
    и `import app.ingest.registry` давал ноль источников молча. Работало всё лишь
    потому, что вызывающий случайно импортировал sources первым.
    """
    code = (
        "import asyncio;"
        "import app.ingest.registry as r;"
        "from app.ingest.blob import FileBlob;"
        "asyncio.run(r.registry.ranked(FileBlob('x.md', b'# h')));"
        "print(len(r.registry.sources))"
    )
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert int(result.stdout.strip()) >= 7, result.stderr
