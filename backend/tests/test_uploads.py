"""Загрузки читаются с лимитом: отказ 413 раньше, чем файл целиком ляжет в память.

Обработчики вызывали `await file.read()` и только потом сравнивали длину с
пределом: гигабайтный файл сначала целиком загружался в память процесса. Без
nginx (lite) размер тела запроса не ограничивал вообще никто.
"""

import io
import re
from pathlib import Path

import pytest
from fastapi import FastAPI, HTTPException, Request
from starlette.datastructures import UploadFile
from starlette.testclient import TestClient

from app.uploads import MAX_REQUEST_BYTES, BodyLimitMiddleware, read_limited


class _CountingFile(io.BytesIO):
    """Файл, который помнит, сколько из него прочитали."""

    def __init__(self, data: bytes) -> None:
        super().__init__(data)
        self.consumed = 0

    def read(self, size: int | None = -1) -> bytes:
        chunk = super().read(size)
        self.consumed += len(chunk)
        return chunk


async def test_small_upload_is_read_whole():
    upload = UploadFile(io.BytesIO(b"abc"), size=3, filename="a.png")

    assert await read_limited(upload, 10, "много") == b"abc"


async def test_declared_size_over_limit_is_refused_without_reading():
    source = _CountingFile(b"x" * 100)
    upload = UploadFile(source, size=100, filename="big.png")

    with pytest.raises(HTTPException) as exc:
        await read_limited(upload, 10, "Картинка больше 10 байт")

    assert exc.value.status_code == 413
    assert exc.value.detail == "Картинка больше 10 байт"
    assert source.consumed == 0


async def test_undeclared_size_stops_right_after_the_limit():
    source = _CountingFile(b"x" * (1024 * 1024))
    upload = UploadFile(source, size=None, filename="big.bin")

    with pytest.raises(HTTPException) as exc:
        await read_limited(upload, 10, "много")

    assert exc.value.status_code == 413
    assert source.consumed < 1024 * 1024  # весь файл не прочитан


def _client(limit: int) -> TestClient:
    app = FastAPI()
    app.add_middleware(BodyLimitMiddleware, max_bytes=limit)

    @app.post("/echo")
    async def echo(request: Request) -> dict:
        return {"size": len(await request.body())}

    return TestClient(app)


def test_body_within_limit_passes():
    resp = _client(10).post("/echo", content=b"x" * 10)

    assert resp.status_code == 200
    assert resp.json() == {"size": 10}


def test_declared_body_over_limit_is_refused():
    resp = _client(10).post("/echo", content=b"x" * 11)

    assert resp.status_code == 413
    assert resp.headers["content-type"].startswith("application/json")


def test_production_app_caps_request_body(client):
    """Потолок включён в самом приложении, а не только у nginx: lite живёт без прокси."""
    caps = [m.kwargs["max_bytes"] for m in client.app.user_middleware if m.cls is BodyLimitMiddleware]
    assert caps == [MAX_REQUEST_BYTES]


def test_nginx_lets_through_everything_the_api_accepts():
    """Предел жил в трёх местах и разошёлся: интерфейс писал «до 40 МБ», nginx
    пускал 48, код принимал 128. Прокси обязан пропускать всё, что примет API."""
    conf = Path(__file__).resolve().parents[2] / "frontend" / "nginx.conf"
    sizes = re.findall(r"client_max_body_size\s+(\d+)m;", conf.read_text(encoding="utf-8"))
    assert sizes == [str(MAX_REQUEST_BYTES // (1024 * 1024))]


def test_streamed_body_over_limit_is_refused():
    def chunks():
        for _ in range(4):
            yield b"x" * 5

    resp = _client(10).post("/echo", content=chunks())

    assert resp.status_code == 413
