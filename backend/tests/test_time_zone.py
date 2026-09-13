"""Время уходит из API со смещением, в какой бы базе оно ни хранилось.

SQLite хранит datetime строкой и теряет часовой пояс. Наивное значение браузер
читает как местное время: в очереди оператора обращение, созданное в 01:05 по
Москве, показывалось созданным в 22:05. Точечное «пришить UTC» стояло в двух
местах из двадцати с лишним, остальные отдавали время без смещения.
"""

from datetime import UTC, datetime, timedelta, timezone


def _aware_utc(value: str) -> bool:
    moment = datetime.fromisoformat(value)
    return moment.tzinfo is not None and moment.utcoffset() == timedelta(0)


def test_column_type_reads_naive_storage_as_utc():
    from app.db import UTCDateTime

    loaded = UTCDateTime().process_result_value(datetime(2026, 9, 10, 22, 5), None)

    assert loaded == datetime(2026, 9, 10, 22, 5, tzinfo=UTC)
    assert loaded is not None and loaded.tzinfo is not None


def test_column_type_stores_any_zone_as_utc():
    from app.db import UTCDateTime

    moscow = datetime(2026, 9, 11, 1, 5, tzinfo=timezone(timedelta(hours=3)))

    stored = UTCDateTime().process_bind_param(moscow, None)

    assert stored == datetime(2026, 9, 10, 22, 5, tzinfo=UTC)
    assert stored is not None and stored.utcoffset() == timedelta(0)


def test_chat_messages_carry_offset(client):
    sid = client.post("/api/chat/sessions").json()["session_id"]
    client.post(f"/api/chat/sessions/{sid}/messages", json={"text": "Как зарегистрироваться на портале?"})

    messages = client.get(f"/api/chat/sessions/{sid}/messages").json()

    assert messages
    assert all(_aware_utc(m["created_at"]) for m in messages)


def test_operator_queue_times_carry_offset(client, operator_headers):
    sid = client.post("/api/chat/sessions").json()["session_id"]
    client.post(f"/api/chat/sessions/{sid}/messages", json={"text": "нужен оператор срочно"})

    queue = client.get("/api/operator/sessions?status=queue", headers=operator_headers).json()

    mine = next(s for s in queue if s["session_id"] == sid)
    assert _aware_utc(mine["created_at"])
    assert _aware_utc(mine["escalated_at"])
