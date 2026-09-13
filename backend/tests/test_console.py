"""Логи lite на Windows: вывод в файл или трубу шёл в cp1251 — кириллица
становилась кракозябрами у любого читателя в UTF-8, а «→» ронял запись лога."""

import io

from app.console import use_utf8

LINE = "Сервис запущен → режим lite, уверенность ≥ 0.4"


def test_cp1251_stream_is_switched_to_utf8():
    raw = io.BytesIO()
    stream = io.TextIOWrapper(raw, encoding="cp1251")

    use_utf8(stream)
    stream.write(LINE)
    stream.flush()

    assert raw.getvalue().decode("utf-8") == LINE


def test_utf8_stream_and_foreign_objects_are_left_alone():
    stream = io.TextIOWrapper(io.BytesIO(), encoding="utf-8", errors="strict")

    use_utf8(stream, object())

    assert (stream.encoding, stream.errors) == ("utf-8", "strict")
