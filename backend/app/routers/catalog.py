"""Картинки товаров портала через наш домен.

Ссылки на превью, которые отдаёт MCP-сервер каталога, ведут прямо на
zakupki.mos.ru, и сам портал их оттуда отдаёт кому угодно — без кук, без
Referer, без CORS. Но между браузером пользователя и чужим доменом стоит целый
слой того, чем мы не управляем: блокировщики, корпоративные прокси, политики
приватности, отключённые сторонние запросы. Достаточно одного из них — и вместо
фотографии товара человек видит пустой квадрат, а мы даже не узнаем почему.

Поэтому картинка идёт с того же адреса, что и страница. Побочные выгоды ровно
те, ради которых прокси обычно и заводят: портал разрешает кэшировать превью
неделю, и это мы делаем у себя, а не гоняем его сервис на каждый показ.

Ходим только за картинками и только по числовому идентификатору файла: хост
задаётся настройкой сервера (portal_image_base), из запроса пользователя в него
ничего не подставляется — иначе ручка превратилась бы в открытый прокси и в SSRF.
Настройка нужна потому, что портал отдаёт картинки только на адреса в РФ: сервер
за границей берёт их через свой прокси картинок в РФ.
"""

import asyncio
from http import HTTPStatus

import httpx
from fastapi import APIRouter, HTTPException
from fastapi.responses import Response

from app.config import get_settings

router = APIRouter(prefix="/api/catalog", tags=["catalog"])

THUMBNAIL_PATH = "/newapi/api/Core/Thumbnail"
ORIGINAL_PATH = "/newapi/api/FileStorage/Download"

# Просить у портала больше оригинала бессмысленно: он апскейлит, растёт только
# вес. Меньше 64 — уже не картинка, а точка.
MIN_SIDE, MAX_SIDE = 64, 2000
# Портал отдаёт превью за доли секунды, но изредка уходит в хвост на ~10 секунд.
FETCH_TIMEOUT_S = 20.0
# Лента карточек — это до восьми картинок разом на каждого читающего. Ограничение
# здесь именно на одновременность: обычный счётчик «столько-то запросов в минуту»
# не годится, потому что в проекте он один на клиента и общий с перепиской — одна
# лента карточек съедала бы почти половину минутного лимита сообщений. Семафор
# держит нагрузку на чужой сервис, а браузер после первого показа неделю берёт
# картинку из своего кэша и сюда вообще не приходит.
_slots = asyncio.Semaphore(16)


@router.get(
    "/image/{file_id}",
    response_class=Response,
    responses={
        200: {
            "content": {"image/jpeg": {}, "image/png": {}, "image/webp": {}},
            "description": "Фотография товара с портала",
        }
    },
)
async def image(file_id: int, w: int = 300, h: int = 0, original: bool = False) -> Response:
    """Превью картинки товара с портала. `original=true` — файл в исходном размере."""
    if file_id <= 0:
        # У портала «картинки нет» кодируется и нулём, и минус единицей
        raise HTTPException(404, "У товара нет фотографии")
    width = max(MIN_SIDE, min(int(w), MAX_SIDE))
    height = max(MIN_SIDE, min(int(h or w), MAX_SIDE))
    base = get_settings().portal_image_base.rstrip("/")
    if original:
        url = f"{base}{ORIGINAL_PATH}?id={file_id}"
    else:
        url = f"{base}{THUMBNAIL_PATH}/{file_id}/{width}/{height}"

    async with _slots, httpx.AsyncClient(timeout=FETCH_TIMEOUT_S) as client:
        try:
            resp = await client.get(url)
        except httpx.HTTPError as exc:
            raise HTTPException(502, "Каталог портала сейчас недоступен") from exc

    content_type = resp.headers.get("Content-Type", "")
    # Портал сообщает об ошибке не статусом, а телом: на несуществующий файл
    # приходит JSON. Отдать его как картинку значит показать пользователю
    # сломанный квадрат вместо честного «фотографии нет».
    if resp.status_code != HTTPStatus.OK or not content_type.startswith("image/"):
        raise HTTPException(404, "Фотография товара не найдена")

    return Response(
        content=resp.content,
        media_type=content_type.split(";")[0],
        headers={
            # Столько же, сколько разрешает сам портал: картинка товара за
            # неделю не меняется, а если поменяется — сменится и id файла.
            "Cache-Control": "public, max-age=604800, immutable",
            "X-Content-Type-Options": "nosniff",
        },
    )
