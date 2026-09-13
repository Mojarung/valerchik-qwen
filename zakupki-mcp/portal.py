"""Клиент внутреннего API Портала поставщиков (zakupki.mos.ru).

Официального API нам не дали, поэтому ходим тем же путём, что и сам сайт: его SPA
берёт данные из двух корней, адреса которых портал сам публикует в рантайм-конфиге
`/newapi/api/Js/GetJsSettings`:

    newapi     = https://zakupki.mos.ru/newapi      — поиск по каталогу, картинки
    classifier = https://zakupki.mos.ru/classifier  — карточка товара целиком

Авторизации, кук и заголовков не требуется — проверено: даже запрос с честным
`User-Agent: SomeBot/1.0` получает 200. Раз никто нас не ограничивает, ограничиваем
себя сами: gzip, keep-alive, свой лимит запросов и кэш (см. ниже). Портал — чужая
инфраструктура, и вести себя на ней надо тише, чем позволено.

Про кириллицу. Фильтр едет JSON-строкой в query-параметре, и битая кодировка здесь
ломается МОЛЧА: портал отвечает 200 и выдачей не по делу — на «ноутбук» приезжают
«OPPO F3» и «ZM-F3», потому что индекс токенизирует куски процентной записи (`f3`,
`e8`). Ни статуса, ни сообщения об ошибке. Правильный UTF-8 портал понимает, так что
обязательного требования тут нет, но мы всё равно отправляем ASCII-only JSON
(`\\u043d\\u043e...`): в URL не остаётся ни одного не-ASCII байта, и перекодировать
по дороге нечего — ни шеллу, ни прокси, ни чужой библиотеке. `json.dumps` делает так
по умолчанию, поэтому здесь важно НЕ выключать `ensure_ascii` «ради читаемости».
Себя можно проверить по ответу: в `additionalInfo.rawPhrase` портал возвращает фразу
такой, какой он её увидел — читаемое слово означает, что доехало верно.
"""

import asyncio
import json
import logging
import random
import time
from dataclasses import dataclass
from http import HTTPStatus
from typing import Any

import httpx

log = logging.getLogger("zakupki.portal")

NEW_API = "https://zakupki.mos.ru/newapi"
CLASSIFIER_API = "https://zakupki.mos.ru/classifier"
SITE = "https://zakupki.mos.ru"

# Признак нашей же кривой сериализации фильтра: портал отвечает на неё 500, и
# повторять такой запрос бессмысленно — ответ не изменится.
OUR_BUG = "Object reference not set to an instance of an object."

# Только ASCII: заголовки HTTP кодируются latin-1, и кириллица в User-Agent роняет
# запрос ещё до отправки. Представляемся честно — фильтра по UA у портала нет, но
# если он однажды появится, узнаваемый клиент получит вопрос, а не молчаливый бан.
USER_AGENT = "ValerchikSupportBot/1.0 (+https://zakupki.mos.ru) Mozilla/5.0 (compatible)"


class PortalError(RuntimeError):
    """Портал не ответил или ответил ошибкой, а в кэше ничего нет."""


@dataclass
class _CacheEntry:
    value: Any
    stored_at: float


@dataclass
class PortalStats:
    """Счётчики для health-тула: по ним видно, живём мы на кэше или на портале."""

    requests: int = 0
    errors: int = 0
    cache_hits: int = 0
    stale_hits: int = 0
    last_trace_id: str = ""
    last_error: str = ""
    breaker_open_until: float = 0.0
    # Пометка «портал недоступен» после неудачного соединения: до какого момента
    # (по time.monotonic) отвечаем ошибкой, не ходя в сеть, по какой причине и
    # сколько запросов так отбили.
    down_until: float = 0.0
    down_reason: str = ""
    down_hits: int = 0


# Пять отказов подряд — портал либо лежит, либо мы ему мешаем. Минута тишины
# дешевле, чем ещё сотня запросов в стену.
BREAKER_FAILURES = 5
BREAKER_PAUSE_S = 60
# Соединение с порталом не установилось (гео-блок, упала сеть, DNS) — минуту не
# пробуем снова. Каждая такая попытка стоит connect-таймаут, и без пометки каждый
# вызов каталога ждал его заново: на проде один search_products тратил около 18 с
# (три попытки с паузами), чтобы ответить всё тем же «каталог недоступен».
DOWN_PAUSE_S = 60.0
# Сбои, при которых соединения не было вовсе. Ответ с ошибкой (503, 429) — другое
# дело: портал жив и отвечает, такой запрос повторяем, как и раньше.
_NO_CONNECTION = (httpx.ConnectError, httpx.ConnectTimeout)


def _reason(exc: Exception) -> str:
    """«ConnectTimeout: текст» — или одно имя, если текста нет: у таймаутов httpx он бывает пустым."""
    text = str(exc).strip()
    return f"{type(exc).__name__}: {text}" if text else type(exc).__name__


# Перегрузка и сбои шлюза: через секунду портал обычно отвечает, повтор оправдан
_ALWAYS_RETRY = frozenset(
    {
        HTTPStatus.BAD_GATEWAY,
        HTTPStatus.SERVICE_UNAVAILABLE,
        HTTPStatus.GATEWAY_TIMEOUT,
        HTTPStatus.TOO_MANY_REQUESTS,
    }
)
# Флажки выдачи: поле запроса → ключ фильтра портала; значение у портала одно — True
_FLAG_FILTERS = (
    ("only_with_offers", "hasOffers"),
    ("moscow_only", "isMoscowProduction"),
    ("innovation_only", "isInnovation"),
)


@dataclass(frozen=True)
class SkuQuery:
    """Запрос к каталогу СТЕ.

    Фильтры здесь только те, которые ПРОВЕРЕНЫ живыми запросами. Незнакомый
    ключ портал молча игнорирует и отвечает 200 с полной выдачей — то есть
    «фильтр», которого на самом деле нет, выглядит работающим и врёт
    пользователю. Формы значений тоже неочевидны и между полями не совпадают:
    диапазон — это `{start, end}`, список — `{values: [...]}`, а не наоборот;
    например `referencePriceGreatEqual` и `offersCountGreatEqual` существуют
    в фильтре оферт и здесь молча игнорируются.
    """

    keyword: str = ""
    take: int = 8
    skip: int = 0
    exact: bool = False
    price_from: float | None = None
    price_to: float | None = None
    category_id: int | None = None
    category_path: str | None = None
    only_with_offers: bool = False
    moscow_only: bool = False
    innovation_only: bool = False
    sku_ids: list[int] | None = None
    order: list[dict] | None = None

    def to_filter(self) -> dict[str, Any]:
        """queryFilter для /api/Sku/QueryIndex."""
        query_filter: dict[str, Any] = {"take": self.take, "skip": self.skip, "withCount": True, "filter": {}}
        flt: dict[str, Any] = query_filter["filter"]
        if self.keyword:
            flt["keyword"] = self.keyword
            flt["isExact"] = self.exact
        if price := self._price_range():
            # Фильтруется только референтная цена — та, что в выдаче лежит в
            # referencePrice. У множества карточек она 0, и они попадают в любой
            # диапазон, начинающийся с нуля: «дешевле 100 рублей» — это в основном
            # товары без указанной цены, а не дешёвые.
            flt["referencePrice"] = price
        if self.category_id:
            flt["productionDirectoryId"] = {"values": [self.category_id]}
        if self.category_path:
            # Путь — префикс ветки дерева («.1.72092567.793371670.»), поэтому одним
            # значением фильтруется целое поддерево. Корни: .1. товары, .2. работы,
            # .3. услуги.
            flt["productionDirectoryPaths"] = {"values": [self.category_path]}
        for flag, key in _FLAG_FILTERS:
            if getattr(self, flag):
                flt[key] = True
        if self.sku_ids:
            flt["ids"] = {"values": list(self.sku_ids)}
        if self.order:
            query_filter["order"] = self.order
        return query_filter

    def _price_range(self) -> dict[str, float]:
        price: dict[str, float] = {}
        if self.price_from is not None:
            price["start"] = self.price_from
        if self.price_to is not None:
            price["end"] = self.price_to
        return price


class PortalClient:
    """Один клиент на процесс: держит соединение, кэш и общий лимит скорости.

    Почему всё в одном объекте: соединение, троттлинг и кэш имеют смысл только
    вместе. Отдельный кэш без троттлинга не спасает портал от залпа, а троттлинг
    без кэша превращает наш собственный лимит в очередь ожидания для пользователя.
    """

    # Настройки — только именованные (после *), перепутать их местами нельзя, а
    # упаковка в отдельную запись ради счётчика аргументов спрятала бы умолчания.
    def __init__(  # noqa: PLR0913
        self,
        *,
        rps: float = 2.0,
        search_ttl_s: float = 900.0,  # выдача поиска живёт 15 минут
        card_ttl_s: float = 86400.0,  # карточка товара меняется куда реже — сутки
        connect_timeout_s: float = 5.0,
        read_timeout_s: float = 30.0,
        down_pause_s: float = DOWN_PAUSE_S,  # 0 — пометку «портал недоступен» не ставить
    ) -> None:
        self._down_pause = down_pause_s
        self._client = httpx.AsyncClient(
            # httpx сам просит gzip и сам распаковывает: на take=50 это 860 КБ -> 59 КБ,
            # то есть 93% трафика, которые мы порталу не создаём.
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "application/json",
                "Referer": f"{SITE}/",
            },
            # read=30с намеренно щедрый: медиана ответа 0.5с, но примерно каждый
            # двадцатый запрос уходит в хвост на 9-10 секунд. С таймаутом в 10с мы
            # рвали бы живые ответы и повторяли запрос, добавляя порталу работы.
            timeout=httpx.Timeout(connect=connect_timeout_s, read=read_timeout_s, write=10.0, pool=10.0),
            limits=httpx.Limits(max_connections=4, max_keepalive_connections=4),
            follow_redirects=True,
        )
        self._min_interval = 1.0 / rps if rps > 0 else 0.0
        self._next_slot = 0.0
        self._slot_lock = asyncio.Lock()
        self._cache: dict[str, _CacheEntry] = {}
        self._search_ttl = search_ttl_s
        self._card_ttl = card_ttl_s
        self._failures = 0
        self.stats = PortalStats()

    async def aclose(self) -> None:
        await self._client.aclose()

    # --- поиск и карточки -------------------------------------------------

    async def search(self, query: SkuQuery) -> dict:
        """Поиск по каталогу СТЕ. -> {"count": int, "items": [...]}."""
        return await self._get_json(f"{NEW_API}/api/Sku/QueryIndex", query.to_filter(), ttl=self._search_ttl)

    async def autocomplete(self, keyword: str) -> dict:
        """Подсказки строки поиска: тот же индекс, но ответ на порядок легче.

        Конверт другой, чем у поиска: `keyword` лежит в корне queryFilter, а не в
        `filter`. Портал всегда отдаёт ровно три позиции — `take` он игнорирует,
        поэтому параметра размера здесь нет.
        """
        return await self._get_json(
            f"{NEW_API}/api/SkuSearch/QueryAutocomplete",
            {"keyword": keyword, "isExact": False},
            ttl=self._search_ttl,
        )

    async def sku(self, sku_id: int) -> dict:
        """Полная карточка товара: характеристики, все картинки, цены, производитель."""
        return await self._get_json(
            f"{CLASSIFIER_API}/api/Sku/GetSku",
            None,
            ttl=self._card_ttl,
            params={"id": sku_id, "withRegionPriceStatistics": "false"},
        )

    # --- транспорт --------------------------------------------------------

    async def _get_json(
        self,
        url: str,
        query_filter: dict | None,
        *,
        ttl: float,
        params: dict | None = None,
    ) -> dict:
        request_params = dict(params or {})
        if query_filter is not None:
            # ensure_ascii=True (по умолчанию) — то самое, без чего поиск по-русски
            # молча возвращает мусор. sort_keys — чтобы ключ кэша не зависел от
            # порядка полей в питоновском словаре.
            request_params["queryFilter"] = json.dumps(query_filter, sort_keys=True)
        cache_key = f"{url}?{sorted(request_params.items())}"

        cached = self._cache.get(cache_key)
        if cached is not None and time.monotonic() - cached.stored_at < ttl:
            self.stats.cache_hits += 1
            return cached.value

        try:
            data = await self._fetch_with_retries(url, request_params)
        except PortalError:
            # stale-if-error: протухший ответ несравнимо лучше, чем «сервис недоступен».
            # Для поддержки вчерашняя цена — рабочий ответ, отказ — не ответ вообще.
            if cached is not None:
                self.stats.stale_hits += 1
                age_min = (time.monotonic() - cached.stored_at) / 60
                log.warning("Портал недоступен, отдаём кэш возрастом %.0f мин: %s", age_min, url)
                return cached.value
            raise

        self._cache[cache_key] = _CacheEntry(value=data, stored_at=time.monotonic())
        return data

    def down_for_s(self) -> float:
        """Сколько ещё секунд портал считается недоступным; 0 — пометки нет."""
        return max(0.0, self.stats.down_until - time.monotonic())

    async def _fetch_with_retries(self, url: str, params: dict) -> dict:
        self._refuse_early()

        delays = (1.0, 2.0, 4.0)
        last_reason = "неизвестно"
        for attempt in range(3):
            await self._throttle()
            try:
                response = await self._client.get(url, params=params)
            except _NO_CONNECTION as exc:
                # Соединения нет — повтор через секунду стоит ещё одного connect-таймаута
                # и почти никогда не помогает: портал не виден из нашей сети. Ставим
                # пометку и отвечаем сразу, а не после трёх попыток.
                last_reason = _reason(exc)
                self._mark_down(last_reason)
                break
            except httpx.HTTPError as exc:
                last_reason = _reason(exc)
            else:
                self.stats.requests += 1
                self.stats.last_trace_id = response.headers.get("Trace-Id", "")
                if response.status_code == HTTPStatus.OK:
                    self._failures = 0
                    self._mark_up()
                    return response.json()
                last_reason = f"HTTP {response.status_code}"
                if not self._retryable(response):
                    break
            if attempt < len(delays) - 1:
                # джиттер, чтобы несколько наших воркеров не повторяли синхронно
                await asyncio.sleep(delays[attempt] * random.uniform(0.7, 1.3))

        self._count_failure(last_reason)
        raise PortalError(f"портал не ответил: {last_reason}")

    def _refuse_early(self) -> None:
        """Отказ без сети: стоит пометка «портал недоступен» или разомкнута цепь."""
        now = time.monotonic()
        if now < self.stats.down_until:
            # Та же ошибка, что и при самой неудаче: для агента ничего не изменилось,
            # а человек не ждёт connect-таймаут ещё раз.
            self.stats.down_hits += 1
            raise PortalError(f"портал не ответил: {self.stats.down_reason}")
        if now < self.stats.breaker_open_until:
            raise PortalError("портал признан недоступным, пауза перед новой попыткой")

    def _mark_down(self, reason: str) -> None:
        if self._down_pause <= 0:
            return
        self.stats.down_until = time.monotonic() + self._down_pause
        self.stats.down_reason = reason
        log.warning(
            "Портал не принимает соединение (%s): %.0f с отвечаем ошибкой, не ходя в сеть",
            reason,
            self._down_pause,
        )

    def _mark_up(self) -> None:
        """Портал ответил — пометка снимается сразу, не дожидаясь конца паузы."""
        if self.stats.down_until:
            log.info("Портал снова отвечает, пометка «недоступен» снята")
        self.stats.down_until = 0.0
        self.stats.down_reason = ""

    def _count_failure(self, reason: str) -> None:
        """Запрос не удался: счётчики, а после серии отказов подряд — размыкание цепи."""
        self._failures += 1
        self.stats.errors += 1
        self.stats.last_error = reason
        if self._failures >= BREAKER_FAILURES:
            self.stats.breaker_open_until = time.monotonic() + BREAKER_PAUSE_S
            self._failures = 0
            log.error(
                "Портал не ответил %d раз подряд, размыкаем цепь на %d с", BREAKER_FAILURES, BREAKER_PAUSE_S
            )

    @staticmethod
    def _retryable(response: httpx.Response) -> bool:
        """400/404/414 — наши собственные ошибки, повтор их не исправит.

        500 у портала почти всегда означает «мы прислали кривой фильтр», а не
        «сервер упал»: у такой ошибки узнаваемое тело. Повторять её — впустую
        нагружать чужой сервис.
        """
        if response.status_code in _ALWAYS_RETRY:
            return True
        if response.status_code == HTTPStatus.FORBIDDEN:
            # Наблюдалось на /classifier: один и тот же запрос отдаёт 403, а через
            # секунду — 200 с теми же заголовками. Это не запрет, а нервный фильтр
            # на всплеск, и пауза с повтором его проходит.
            return True
        if response.status_code == HTTPStatus.INTERNAL_SERVER_ERROR:
            return OUR_BUG not in response.text
        return False

    async def _throttle(self) -> None:
        """Свой лимит скорости: у портала его нет, но это не повод им пользоваться."""
        if self._min_interval <= 0:
            return
        async with self._slot_lock:
            now = time.monotonic()
            wait = self._next_slot - now
            self._next_slot = max(now, self._next_slot) + self._min_interval
        if wait > 0:
            await asyncio.sleep(wait)


# --- ссылки -----------------------------------------------------------------
# Шаблоны восстановлены из бандла портала (класс Utils, метод getThumbnailUrl) и
# проверены скачиванием: отдаётся настоящий JPEG/PNG, без кук и авторизации.


def thumbnail_url(file_storage_id: int, width: int = 300, height: int | None = None) -> str:
    """Превью, вписанное в бокс width x height с сохранением пропорций.

    Оба сегмента обязательны — путь с одним числом отдаёт 404. Просить размер
    больше оригинала бессмысленно: сервис апскейлит, растёт только вес.
    """
    return f"{NEW_API}/api/Core/Thumbnail/{file_storage_id}/{width}/{height or width}"


def original_url(file_storage_id: int) -> str:
    """Оригинал файла. Отдаётся с `Content-Disposition: attachment` и без кэша,
    поэтому для показа в интерфейсе берут превью, а это — для зума и скачивания."""
    return f"{NEW_API}/api/FileStorage/Download?id={file_storage_id}"


def product_url(sku_id: int) -> str:
    """Страница товара на портале — то, куда человек перейдёт из ответа бота."""
    return f"{SITE}/sku/view/{sku_id}"


# --- приведение выдачи к компактному виду -----------------------------------


def _has_image(image_id: Any) -> bool:
    """У портала «нет картинки» кодируется и как null, и как 0, и как -1."""
    return isinstance(image_id, int) and image_id > 0


def compact_product(item: dict, *, thumb_size: int = 300) -> dict:
    """Позиция выдачи -> то, что осмысленно показать человеку и модели.

    Обрезка не косметика: сырая позиция каталога весит около 17 КБ (в основном
    характеристики), и десяток таких, отданных модели целиком, съест контекст
    без всякой пользы для ответа.
    """
    own_image = item.get("skuImageId")
    image_id = own_image if _has_image(own_image) else item.get("productionDirectoryDefaultImageId")
    gallery = [i for i in (item.get("skuImageIds") or []) if _has_image(i)]
    price, price_kind = _price_of(item)
    return {
        "sku_id": item.get("id"),
        "name": (item.get("name") or "").strip(),
        "url": product_url(item.get("id")),
        "image": thumbnail_url(image_id, thumb_size) if _has_image(image_id) else None,
        "image_full": original_url(image_id) if _has_image(image_id) else None,
        "images": [thumbnail_url(i, thumb_size) for i in gallery],
        # Флаг честности: заглушка категории — не фотография этого товара, и
        # выдавать её за неё нельзя. У услуг своих картинок почти никогда нет.
        "own_image": _has_image(own_image),
        "price": price,
        "price_kind": price_kind,
        "unit": item.get("okeiName"),
        "category": item.get("productionDirectoryName"),
        "category_id": item.get("productionDirectoryId"),
        "manufacturer": item.get("manufacturerName"),
        "country": item.get("oksmName"),
        "okpd": item.get("productionCode"),
        "offers_count": item.get("offersCount"),
        "is_moscow_production": bool(item.get("isMoscowProduction")),
        "is_innovation": bool(item.get("isInnovation")),
    }


def _price_of(item: dict) -> tuple[float | None, str | None]:
    """У позиции три разные цены, и путать их нельзя.

    referencePrice — референтная цена каталога, medianPrice — медиана по реальным
    предложениям, costPerUnit — цена конкретного предложения. Возвращаем первую
    заполненную и ОБЯЗАТЕЛЬНО говорим, какая именно: «цена 192 620 ₽» без пометки
    «референтная» — это уже дезинформация, за которой человек пойдёт в закупку.
    """
    for field_name, kind in (
        ("referencePrice", "reference"),
        ("medianPrice", "median"),
        ("costPerUnit", "offer"),
    ):
        value = item.get(field_name)
        if isinstance(value, int | float) and value > 0:
            return float(value), kind
    return None, None


def compact_card(card: dict, *, thumb_size: int = 600) -> dict:
    """Полная карточка (GetSku) -> карточка для ответа: цены, характеристики, галерея."""
    images = [
        img.get("fileStorageId") for img in (card.get("images") or []) if _has_image(img.get("fileStorageId"))
    ]
    # Именно id, а не entityId: у карточки есть оба, и они РАЗНЫЕ (id 47387580 при
    # entityId 47387146). Ссылка /sku/view/ и параметр GetSku?id= — это id.
    sku_id = card.get("id")
    return {
        "sku_id": sku_id,
        "name": (card.get("name") or "").strip(),
        "url": product_url(sku_id),
        "description": (card.get("description") or "").strip() or None,
        "image": thumbnail_url(images[0], thumb_size) if images else None,
        "images": [thumbnail_url(i, thumb_size) for i in images],
        "images_full": [original_url(i) for i in images],
        "own_image": bool(images),
        "prices": {
            "reference": card.get("referencePrice"),
            "median": card.get("medianPrice"),
            "min": card.get("minPrice"),
            "max": card.get("maxPrice"),
            "avg": card.get("avgPrice"),
        },
        "unit": card.get("okeiName"),
        "category": card.get("productionDirectoryName"),
        "country": card.get("oksmName"),
        "offers_count": card.get("offersCount"),
        "contracts_count": card.get("contractsCount"),
        "views": card.get("views"),
        "is_moscow_production": bool(card.get("isMoscowProduction")),
        "characteristics": _characteristics(card),
    }


def _characteristics(card: dict) -> list[dict]:
    """Характеристики СТЕ в плоском виде «название: значение единица».

    Значение лежит не там, где его ждёшь. Во вложенном объекте
    `productCharacteristicValue` есть поле `stringValue`, но у числовых
    характеристик оно null, а у справочных содержит внутренние идентификаторы
    вида «1988260825.2017707918» — показать такое человеку нельзя. Настоящее
    значение хранится на уровне строки, в одном из пяти полей по типу:
    строка, целое, дробное, булево или диапазон. Единица измерения — ещё этажом
    ниже, и без неё «вес 1.55» бесполезен.
    """
    out: list[dict] = []
    for row in card.get("skuCharacteristics") or []:
        meta = row.get("productCharacteristicValue") or {}
        name = meta.get("productCharacteristicName")
        value = _first_filled(
            row.get("characteristicValueStringValue"),
            row.get("characteristicValueDecimalValue"),
            row.get("characteristicValueIntValue"),
            row.get("characteristicValueBoolValue"),
            row.get("rangeValue"),
        )
        if not name or value is None:
            continue
        if isinstance(value, bool):
            value = "да" if value else "нет"
        elif isinstance(value, float) and value.is_integer():
            value = int(value)  # «10 ядер», а не «10.0 ядер»
        unit = (
            meta.get("productCharacteristicUnitDesignationNational")
            or meta.get("productCharacteristicNewOkeiName")
            or ""
        )
        out.append({"name": str(name), "value": f"{value} {unit}".strip()})
    return out


def _first_filled(*values: Any) -> Any:
    """Первое непустое значение. 0 и False — значения, а не пустота."""
    for value in values:
        if value not in (None, ""):
            return value
    return None
