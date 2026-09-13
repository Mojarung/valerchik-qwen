"""Загрузки с лимитом: отказ 413 до того, как тело целиком окажется в памяти."""

import json
from dataclasses import dataclass

from fastapi import HTTPException, UploadFile
from starlette.types import ASGIApp, Message, Receive, Scope, Send

_CHUNK = 64 * 1024
_MB = 1024 * 1024

# Выгрузка обращений на 150 тысяч строк весит около 50 МБ, и парсер её тянет:
# лимит держим выше правдоподобного объёма данных, а не ниже. Живёт здесь, а не
# в routers/kb.py: от него же считаются потолок тела запроса и надпись в
# интерфейсе (через /api/meta) — копии этого числа уже расходились.
MAX_DOCUMENT_BYTES = 128 * _MB
#: Потолок тела любого запроса: самая крупная законная загрузка — документ, плюс
#: запас на обвязку multipart. client_max_body_size у nginx — то же число.
MAX_REQUEST_BYTES = MAX_DOCUMENT_BYTES + 2 * _MB


async def read_limited(file: UploadFile, max_bytes: int, too_big: str) -> bytes:
    """Содержимое загрузки, но не больше `max_bytes`.

    Обработчики читали файл целиком и только потом сравнивали длину с пределом:
    гигабайтный файл сперва ложился в память процесса. Заявленный размер
    отсекает сразу, незаявленный — на первом куске сверх предела.
    """
    if file.size is not None and file.size > max_bytes:
        raise HTTPException(413, too_big)
    chunks: list[bytes] = []
    total = 0
    while chunk := await file.read(_CHUNK):
        total += len(chunk)
        if total > max_bytes:
            raise HTTPException(413, too_big)
        chunks.append(chunk)
    return b"".join(chunks)


class BodyLimitMiddleware:
    """Потолок тела запроса для всего API — как client_max_body_size у nginx.

    Без прокси (lite) размер тела не ограничивал никто: многогигабайтная
    загрузка целиком писалась во временный файл раньше, чем обработчик успевал
    её отвергнуть. Заявленная длина отсекается до чтения; поток без длины — на
    первом куске сверх предела. Превышение — HTTPException(413): FastAPI любую
    другую ошибку чтения тела превращает в 400 «ошибка разбора».
    """

    def __init__(self, app: ASGIApp, max_bytes: int) -> None:
        self.app = app
        self.max_bytes = max_bytes
        self.detail = f"Запрос больше {max_bytes / 1024 / 1024:g} МБ"

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        declared = _content_length(scope)
        if declared is not None and declared > self.max_bytes:
            await self._refuse(send)
            return
        received = 0

        async def limited_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_bytes:
                    raise HTTPException(413, self.detail)
            return message

        await self.app(scope, limited_receive, send)

    async def _refuse(self, send: Send) -> None:
        body = json.dumps({"detail": self.detail}, ensure_ascii=False).encode()
        await send(
            {
                "type": "http.response.start",
                "status": 413,
                "headers": [
                    (b"content-type", b"application/json; charset=utf-8"),
                    (b"content-length", str(len(body)).encode()),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})


def _content_length(scope: Scope) -> int | None:
    for name, value in scope.get("headers") or []:
        if name == b"content-length":
            try:
                return int(value)
            except ValueError:
                return None
    return None


@dataclass(frozen=True, slots=True)
class StoredFile:
    """Файл, уже положенный в хранилище: ключ, адрес, имя, размер и тип.

    Роутер кладёт файл сам — чтобы не тащить мегабайты через сервисный слой, — а
    сервису передаёт описание одной записью вместо пяти аргументов подряд.
    """

    key: str
    url: str
    name: str
    size: int
    content_type: str

    def as_attachment(self, kind: str) -> dict[str, str | int]:
        """Вложение в meta сообщения: картинка или файл со ссылкой из хранилища."""
        return {
            "url": self.url,
            "key": self.key,
            "name": self.name,
            "size": self.size,
            "content_type": self.content_type,
            "kind": kind,
        }
