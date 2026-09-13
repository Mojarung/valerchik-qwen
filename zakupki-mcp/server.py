"""MCP-сервер «Каталог Портала поставщиков»: поиск товаров и их картинки.

Даёт ИИ-агенту (нашему «Валерчику» и любому внешнему MCP-клиенту) четыре тула
поверх каталога zakupki.mos.ru: найти товары, открыть карточку, взять все
картинки, подсказать варианты по началу фразы.

Транспорт — streamable-http, потому что бэкенд подключает MCP-серверы по URL из
реестра в админке. Запуск:  uv run server.py   (слушает 0.0.0.0:9200/mcp)

Env:
    ZAKUPKI_MCP_HOST / ZAKUPKI_MCP_PORT — где слушать (0.0.0.0 / 9200)
    ZAKUPKI_RPS                         — свой лимит запросов к порталу (2.0)
    ZAKUPKI_THUMB_SIZE                  — сторона бокса превью в пикселях (300)
    ZAKUPKI_DOWN_PAUSE_S                — сколько секунд после неудачного соединения
                                          с порталом отвечать ошибкой сразу, не ходя
                                          в сеть (60; 0 — не ставить такую пометку)

Соглашение с нашим бэкендом: любой тул, вернувший ключ `products`, показывается
пользователю карточками с картинками. Поэтому список товаров всегда лежит именно
в `products`, а не в `items`/`results`.
"""

import logging
import os
from typing import Any

from mcp.server import MCPServer

from portal import (
    DOWN_PAUSE_S,
    PortalClient,
    PortalError,
    SkuQuery,
    compact_card,
    compact_product,
    thumbnail_url,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("zakupki.mcp")

THUMB = int(os.environ.get("ZAKUPKI_THUMB_SIZE", "300"))
MAX_LIMIT = 20  # позиция каталога весит ~17 КБ; два десятка — потолок разумного ответа

mcp = MCPServer("zakupki-catalog")
portal = PortalClient(
    rps=float(os.environ.get("ZAKUPKI_RPS", "2.0")),
    down_pause_s=float(os.environ.get("ZAKUPKI_DOWN_PAUSE_S", str(DOWN_PAUSE_S))),
)

SORTS = {
    # Порядок по умолчанию у портала — новизна: сверху то, что добавили последним.
    "new": None,
    "relevance": [{"field": "relevance", "desc": True}],
    "price_asc": [{"field": "referencePrice", "desc": False}],
    "price_desc": [{"field": "referencePrice", "desc": True}],
}


def _fail(message: str) -> dict[str, Any]:
    """Ошибка тула — обычный ответ, а не исключение.

    Упавший инструмент модель, скорее всего, просто вызовет ещё раз с тем же
    результатом. Внятный текст ошибки даёт ей шанс сказать человеку правду:
    каталог сейчас недоступен, вот что можно сделать вместо этого.
    """
    return {"error": message, "products": []}


# Параметры инструмента — это его схема для модели: каждое поле она заполняет
# сама, по описанию из docstring. Упаковка в одну запись спрятала бы поля во
# вложенный объект, с которым модели справляются хуже, — поэтому их много.
@mcp.tool()
async def search_products(  # noqa: PLR0913
    query: str,
    limit: int = 8,
    sort: str = "relevance",
    exact: bool = False,
    min_price: float | None = None,
    max_price: float | None = None,
    only_with_offers: bool = False,
    moscow_only: bool = False,
    category_id: int | None = None,
) -> dict[str, Any]:
    """Найти товары в каталоге Портала поставщиков (zakupki.mos.ru) по названию.

    Возвращает карточки СТЕ с картинкой, ценой, единицей измерения, категорией и
    ссылкой на портал. Это про КАТАЛОГ ТОВАРОВ, а не про правила работы портала.

    query — что искать словами, как в поисковой строке: «ноутбук», «бумага А4»,
        «перчатки нитриловые». Поиск нечёткий, опечатку переживает.
    limit — сколько показать, 1..20 (по умолчанию 8).
    sort — relevance (по умолчанию), new (сначала новые), price_asc, price_desc.
    exact — искать точную фразу целиком; по умолчанию поиск свободный.
    min_price / max_price — диапазон референтной цены в рублях. Осторожно с нижней
        границей: у множества карточек цена не указана (0), и они попадают в любой
        диапазон, начинающийся с нуля.
    only_with_offers — только товары, у которых прямо сейчас есть предложения
        поставщиков; остальные купить нельзя, можно только посмотреть карточку.
    moscow_only — только продукция московских производителей.
    category_id — сузить до категории (id берётся из поля category_id найденного
        товара: нашли похожее — повторите поиск внутри его категории).
    """
    if sort not in SORTS:
        return _fail(f"sort должен быть одним из {', '.join(SORTS)}, получено «{sort}»")
    limit = max(1, min(int(limit), MAX_LIMIT))
    try:
        data = await portal.search(
            SkuQuery(
                keyword=query.strip(),
                take=limit,
                order=SORTS[sort],
                exact=exact,
                price_from=min_price,
                price_to=max_price,
                only_with_offers=only_with_offers,
                moscow_only=moscow_only,
                category_id=category_id,
            ),
        )
    except PortalError as exc:
        return _fail(f"каталог портала сейчас недоступен ({exc})")

    # Обрезаем и на выходе, а не только в запросе: портал волен вернуть больше,
    # чем просили, а каждая лишняя позиция — это ~17 КБ контекста модели.
    items = (data.get("items") or [])[:limit]
    products = [compact_product(item, thumb_size=THUMB) for item in items]
    total = data.get("count") or 0
    result: dict[str, Any] = {
        "query": query,
        "total": total,
        "products": products,
        "source": "Портал поставщиков, zakupki.mos.ru",
    }
    if not products:
        result["hint"] = (
            "По этому запросу в каталоге ничего нет. Попробуйте более общее слово: "
            "например «ноутбук» вместо конкретной модели."
        )
    return result


@mcp.tool()
async def get_product(sku_id: int) -> dict[str, Any]:
    """Полная карточка товара по его id: характеристики, все цены, все картинки.

    sku_id берётся из поля sku_id выдачи search_products. Здесь появляется то,
    чего нет в списке: описание, таблица характеристик (диагональ, вес, объём
    памяти и т.п.), разброс цен по предложениям и число заключённых контрактов.
    """
    try:
        card = await portal.sku(int(sku_id))
    except PortalError as exc:
        return _fail(f"карточка товара недоступна ({exc})")
    if not card:
        return _fail(f"товар {sku_id} на портале не найден")
    product = compact_card(card)
    # Ключ products — то же соглашение, что и в поиске: одна карточка тоже
    # должна доехать до пользователя картинкой, а не только текстом.
    return {"products": [product], "product": product, "source": "Портал поставщиков, zakupki.mos.ru"}


@mcp.tool()
async def product_images(sku_id: int, size: int = 600) -> dict[str, Any]:
    """Все картинки карточки товара: ссылки на превью нужного размера и оригиналы.

    size — сторона бокса в пикселях (по умолчанию 600). Картинка вписывается в
    бокс с сохранением пропорций; просить больше оригинала бессмысленно.
    Ссылки открытые: их можно вставлять в ответ пользователю как есть.
    """
    try:
        card = await portal.sku(int(sku_id))
    except PortalError as exc:
        return _fail(f"карточка товара недоступна ({exc})")
    product = compact_card(card, thumb_size=max(64, min(int(size), 2000)))
    return {
        "sku_id": product["sku_id"],
        "name": product["name"],
        "images": product["images"],
        "images_full": product["images_full"],
        "count": len(product["images"]),
        "note": "" if product["own_image"] else "У этого товара нет собственных фотографий",
    }


@mcp.tool()
async def suggest_products(prefix: str) -> dict[str, Any]:
    """Три подсказки товаров по началу фразы — то же, что выпадает в строке поиска портала.

    Ответ на порядок легче полной выдачи, поэтому годится, чтобы прикинуть, есть ли
    вообще такое в каталоге, и уточнить формулировку перед search_products. Ровно
    три позиции и без подробностей — за карточками идти в search_products.
    """
    try:
        # take портал здесь игнорирует и всегда отдаёт три позиции — параметра
        # «сколько подсказок» у этого тула поэтому и нет.
        data = await portal.autocomplete(prefix.strip())
    except PortalError as exc:
        return _fail(f"подсказки недоступны ({exc})")
    products = []
    for item in data.get("items") or []:
        specific = item.get("specific") or {}
        image_id = specific.get("imageId")
        sku_id = int(item.get("id")) if str(item.get("id", "")).isdigit() else None
        products.append(
            {
                "sku_id": sku_id,
                "name": item.get("title"),
                "url": f"https://zakupki.mos.ru/sku/view/{sku_id}" if sku_id else None,
                "image": thumbnail_url(image_id, THUMB)
                if isinstance(image_id, int) and image_id > 0
                else None,
                "price": specific.get("referencePrice") or None,
                "price_kind": "reference" if specific.get("referencePrice") else None,
                "unit": specific.get("okeiName"),
                "category": specific.get("productionDirectoryName"),
            }
        )
    return {
        "query": prefix,
        "total": data.get("count") or 0,
        "products": products,
        "source": "Портал поставщиков, zakupki.mos.ru",
    }


@mcp.tool()
async def catalog_status() -> dict[str, Any]:
    """Доступен ли каталог портала прямо сейчас и на чём мы отвечаем — на нём или на кэше.

    portal_down — стоит пометка «портал недоступен»: соединение с ним только что не
    установилось, и ещё down_for_s секунд инструменты отвечают ошибкой, не ходя в сеть.
    """
    stats = portal.stats
    alive, catalog_size = False, 0
    # Под пометкой проба не нужна: она отбилась бы той же ошибкой, а свежий кэш
    # прошлой пробы и вовсе ответил бы «жив».
    if not portal.down_for_s():
        try:
            probe = await portal.search(SkuQuery(take=1, skip=0))
            alive, catalog_size = True, probe.get("count") or 0
        except PortalError as exc:
            stats.last_error = str(exc)
    down_for = portal.down_for_s()
    return {
        "alive": alive,
        "catalog_size": catalog_size,
        "portal_down": down_for > 0,
        "down_for_s": round(down_for),
        "requests": stats.requests,
        "errors": stats.errors,
        "cache_hits": stats.cache_hits,
        "stale_hits": stats.stale_hits,
        "down_hits": stats.down_hits,
        "last_error": stats.last_error,
        # Trace-Id портала: с ним их техподдержка найдёт наш запрос в своих логах
        "last_trace_id": stats.last_trace_id,
    }


if __name__ == "__main__":
    # host=0.0.0.0 — сервис живёт внутри compose-сети и наружу не публикуется.
    # На этом адресе SDK не включает защиту от DNS rebinding (она рассчитана на
    # локальный сервер, куда может достучаться браузер), и Host-заголовок
    # «zakupki-mcp:9200» из соседнего контейнера проходит.
    mcp.run(
        transport="streamable-http",
        host=os.environ.get("ZAKUPKI_MCP_HOST", "0.0.0.0"),
        port=int(os.environ.get("ZAKUPKI_MCP_PORT", "9200")),
        # Без состояния: клиент подключается на время одного ответа агента, и
        # хранить между запросами сессию не для чего.
        stateless_http=True,
        json_response=True,
    )
