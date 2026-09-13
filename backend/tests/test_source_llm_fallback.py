"""Запасной разбор моделью: проверяем всё, что можно проверить без модели.

Сам вызов модели не трогаем намеренно — он дорогой и недетерминированный, а
ценность источника не в нём, а в двух вещах: что он никогда не перехватывает
файл у настоящего парсера и что отказывается заранее, не дойдя до сети.
"""

from __future__ import annotations

import asyncio

import pytest

from app.ingest.blob import FileBlob
from app.ingest.registry import MIN_CONFIDENCE, IngestContext
from app.ingest.sources.base import SourceMismatch
from app.ingest.sources.llm_fallback import (
    LlmFallbackSource,
    _json_array,
    _sample_text,
    records_from_payload,
)


class FakeDyn:
    """Настройки без базы: источнику от них нужен только .get."""

    def __init__(self, values: dict) -> None:
        self._values = values

    def get(self, key: str, default=None):
        return self._values.get(key, default)


class FakeRuntime:
    def __init__(self, dyn: FakeDyn) -> None:
        self.dyn = dyn


async def _collect(source, blob, ctx):
    return [record async for record in source.parse(blob, ctx)]


def test_payload_becomes_records():
    payload = [{"question": "Как войти?", "answer": "Через ЭП."}]
    records = records_from_payload(payload, file="strange.dat", source_name="llm_fallback")
    assert len(records) == 1
    assert records[0].question == "Как войти?"
    assert records[0].answer == "Через ЭП."
    assert records[0].origin.file == "strange.dat"
    assert records[0].origin.source_name == "llm_fallback"
    # Пары вытащила модель, а не автор: дословно такой ответ отдавать нельзя
    assert records[0].origin.authored is False


def test_locator_points_at_the_fragment():
    """Провенанс должен различать пары: на защите спрашивают «откуда это»."""
    payload = [
        {"question": "Первый?", "answer": "Да."},
        {"question": "Второй?", "answer": "Тоже да."},
    ]
    records = records_from_payload(payload, file="f.dat", source_name="llm_fallback")
    assert [r.origin.locator for r in records] == ["фрагмент 1", "фрагмент 2"]


def test_garbage_payload_is_dropped():
    """Модель фантазирует по-разному: половинчатые и не-словарные пары выкидываем."""
    payload = [
        "строка вместо объекта",
        None,
        42,
        {"question": "Без ответа?"},
        {"answer": "Без вопроса."},
        {"question": "   ", "answer": "   "},
        {"question": None, "answer": None},
        {"question": " Живой вопрос? ", "answer": " Живой ответ. "},
    ]
    records = records_from_payload(payload, file="f.dat", source_name="llm_fallback")
    assert len(records) == 1
    # Пробелы по краям модель оставляет постоянно — они не должны доехать до базы
    assert records[0].question == "Живой вопрос?"
    assert records[0].answer == "Живой ответ."


def test_nested_values_do_not_become_python_repr():
    """Вложенный объект в поле — не текст: repr словаря в базе знаний хуже пропуска."""
    payload = [
        {"question": {"text": "Как войти?"}, "answer": "Через ЭП."},
        {"question": "Как войти?", "answer": ["Через", "ЭП"]},
        {"question": 42, "answer": "Ответ на всё."},
    ]
    records = records_from_payload(payload, file="f.dat", source_name="llm_fallback")
    # Осталась только пара с числовым вопросом: число — всё-таки текст, объект — нет
    assert [(r.question, r.answer) for r in records] == [("42", "Ответ на всё.")]


def test_json_array_survives_model_habits():
    """Мелкие модели портят ответ предсказуемо — ради одной лишней строки его не выкидываем."""
    pair = '[{"question": "q", "answer": "a"}]'
    assert _json_array(f"```json\n{pair}\n```") == [{"question": "q", "answer": "a"}]
    # Массив завернули в объект
    assert _json_array('{"pairs": [{"question": "q", "answer": "a"}]}') == [{"question": "q", "answer": "a"}]
    # Перед массивом приписали фразу
    assert _json_array(f"Вот пары:\n{pair}") == [{"question": "q", "answer": "a"}]
    assert _json_array('```json[{"question": "q", "answer": "a"}]```') == [{"question": "q", "answer": "a"}]
    # А вот это уже не ответ, а отказ модели: списка нет ни в каком виде
    assert _json_array("Извините, пар не нашлось") is None
    assert _json_array('{"question": "q"}') is None


def test_empty_payload_gives_no_records():
    assert records_from_payload([], file="f.dat", source_name="llm_fallback") == []


@pytest.mark.asyncio
async def test_refuses_without_runtime():
    source = LlmFallbackSource()
    blob = FileBlob(name="strange.dat", data=b"nonsense")
    with pytest.raises(SourceMismatch):
        [record async for record in source.parse(blob, IngestContext(rt=None))]


@pytest.mark.asyncio
async def test_refuses_when_toggle_is_off():
    """Тумблер выключен — источник обязан отказаться до похода в модель."""
    source = LlmFallbackSource()
    blob = FileBlob(name="strange.dat", data=b"nonsense")
    rt = FakeRuntime(
        FakeDyn(
            {
                "ingest.llm_fallback_enabled": False,
                # Модель настроена: отказ должен случиться именно из-за тумблера
                "llm.base_url": "http://localhost:1234/v1",
                "llm.model": "qwen",
            }
        )
    )
    with pytest.raises(SourceMismatch):
        [record async for record in source.parse(blob, IngestContext(rt=rt))]


@pytest.mark.asyncio
async def test_refuses_without_configured_llm():
    """Тумблер включён, но модели нет: build_model подсунул бы скриптованную заглушку."""
    source = LlmFallbackSource()
    blob = FileBlob(name="strange.dat", data=b"nonsense")
    rt = FakeRuntime(FakeDyn({"ingest.llm_fallback_enabled": True}))
    with pytest.raises(SourceMismatch):
        [record async for record in source.parse(blob, IngestContext(rt=rt))]


def test_unknown_extension_still_gives_the_model_text():
    """«dump.dat» — тот самый случай, ради которого источник есть: markitdown его не берёт."""
    blob = FileBlob(name="dump.dat", data="Вопрос: Как войти?".encode("cp1251"))
    assert "Как войти?" in _sample_text(blob)


def test_binary_file_gives_no_sample():
    """Раскодированные байты картинки — мусор: лучше отказаться, чем платить за него токенами."""
    assert _sample_text(FileBlob(name="scan.tiff", data=b"II*\x00" + b"\x01\x02" * 100)) == ""


def _fake_agent(monkeypatch, run):
    """Подменяем pydantic_ai.Agent: путь проходим целиком, в сеть не ходим."""

    class FakeAgent:
        def __init__(self, model, system_prompt=""):
            pass

        async def run(self, text):
            return await run(text)

    monkeypatch.setattr("pydantic_ai.Agent", FakeAgent)
    monkeypatch.setattr("app.services.agent.build_model", lambda dyn: object())


def _runtime_with_model(timeout_s: float):
    return FakeRuntime(
        FakeDyn(
            {
                "ingest.llm_fallback_enabled": True,
                "llm.base_url": "http://localhost:1234/v1",
                "llm.model": "qwen",
                "agent.timeout_s": timeout_s,
            }
        )
    )


@pytest.mark.asyncio
async def test_cp1251_text_reaches_the_model_unbroken(monkeypatch):
    """Выгрузки из старых систем приезжают в 1251 — модель должна увидеть текст, а не мусор."""
    seen: list[str] = []

    async def echo(text):
        seen.append(text)

        class Result:
            output = '[{"question": "Как войти?", "answer": "Через ЭП."}]'

        return Result()

    _fake_agent(monkeypatch, echo)
    source = LlmFallbackSource()
    blob = FileBlob(name="old.txt", data="Вопрос: Как войти?".encode("cp1251"))
    records = await _collect(source, blob, IngestContext(rt=_runtime_with_model(30)))
    assert "Как войти?" in seen[0]
    assert records[0].origin.authored is False


@pytest.mark.asyncio
async def test_binary_file_refused_before_the_model(monkeypatch):
    """Двоичный мусор до модели доезжать не должен: это оплаченные токены впустую."""
    called: list[str] = []

    async def should_not_run(text):
        called.append(text)
        raise AssertionError("модель не должна вызываться")

    _fake_agent(monkeypatch, should_not_run)
    source = LlmFallbackSource()
    blob = FileBlob(name="scan.tiff", data=b"II*\x00" + b"\x01\x02" * 100)
    with pytest.raises(SourceMismatch):
        await _collect(source, blob, IngestContext(rt=_runtime_with_model(30)))
    assert called == []


@pytest.mark.asyncio
async def test_hung_model_does_not_hang_the_upload(monkeypatch):
    """Модель молчит — источник обязан сдаться сам, иначе загрузка файла висит вечно."""

    async def never_answers(text):
        await asyncio.sleep(30)

    _fake_agent(monkeypatch, never_answers)
    source = LlmFallbackSource()
    blob = FileBlob(name="strange.dat", data="вопрос\nответ".encode())
    with pytest.raises(SourceMismatch):
        await asyncio.wait_for(
            _collect(source, blob, IngestContext(rt=_runtime_with_model(0.05))),
            timeout=5,
        )


@pytest.mark.asyncio
async def test_unreachable_model_is_a_refusal(monkeypatch):
    """LM Studio не поднят — это отказ источника, а не падение конвейера."""

    async def refuses_connection(text):
        raise ConnectionError("Connection refused")

    _fake_agent(monkeypatch, refuses_connection)
    source = LlmFallbackSource()
    blob = FileBlob(name="strange.dat", data="вопрос\nответ".encode())
    with pytest.raises(SourceMismatch):
        await _collect(source, blob, IngestContext(rt=_runtime_with_model(30)))


@pytest.mark.asyncio
async def test_sniff_is_last_resort():
    source = LlmFallbackSource()
    # Отрицательный приоритет: при равной уверенности проигрывает любому настоящему источнику
    assert source.priority < 0
    confidence = await source.sniff(FileBlob(name="x.dat", data=b"y"))
    # Чуть выше порога отсечения — берётся ровно тогда, когда не взялся никто
    assert confidence > MIN_CONFIDENCE
    assert confidence == pytest.approx(0.16, abs=0.05)


@pytest.mark.asyncio
async def test_sniff_never_refuses_a_file():
    """Уверенность не зависит от файла: запасной разбор берётся за что угодно."""
    source = LlmFallbackSource()
    for blob in (
        FileBlob("a.pdf", b"%PDF-1.7"),
        FileBlob("b.xlsx", b"PK\x03\x04"),
        FileBlob("c.txt", "вопрос\nответ".encode()),
    ):
        assert await source.sniff(blob) >= MIN_CONFIDENCE
