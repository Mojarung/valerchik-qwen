"""Пошаговые сценарии по порталу: какой из них подходит к вопросу.

Расширение браузера умеет вести человека по zakupki.mos.ru: подсвечивать кнопку,
ставить рядом маскота и объяснять шаг. Сценарии описаны в guides/portal.json
селекторами, снятыми с живого портала, а здесь решается только одно — какой
сценарий приложить к ответу и прикладывать ли вообще.

Подбор лексический и намеренно консервативный: сценарий приходит с кнопкой
«Показать на портале», и кнопка, ведущая не туда, хуже её отсутствия. Поэтому
нужен почти полный охват формулировки сценария словами вопроса, а при
неизвестной роли два сценария для разных сторон сделки не выбираются наугад.
"""

import json
import logging
from pathlib import Path
from typing import Any

from app.services import audience as audience_of
from app.services.retrieval import tokenize

log = logging.getLogger(__name__)

CATALOG_PATH = Path(__file__).resolve().parent.parent / "guides" / "portal.json"
DEFAULT_MIN_OVERLAP = 0.75

_file_cache: tuple[float, dict[str, Any]] | None = None
_cache: tuple[tuple[float, int], dict[str, Any]] | None = None

# Сценарии, заведённые из админки: id -> запись. Кладёт сюда guides_store,
# перечитывая БД на старте и после каждой правки. Держим отдельно от файла,
# чтобы заводской набор всегда можно было вернуть удалением записи.
_overrides: dict[str, dict[str, Any]] = {}
_overrides_version = 0


def set_overrides(records: list[dict[str, Any]]) -> None:
    """Заменить наложение из БД целиком (см. services/guides_store.py)."""
    global _overrides, _overrides_version
    _overrides = {r["id"]: r for r in records if isinstance(r, dict) and r.get("id")}
    _overrides_version += 1


def factory() -> dict[str, Any]:
    """Заводской каталог из файла, без правок админов.

    Кэш привязан к времени изменения файла: правка сценария подхватывается без
    перезапуска, как и всё остальное в сервисе. Проверка stat — микросекунды,
    и она много дешевле удивления «поменял JSON, а бот ведёт по-старому».
    """
    global _file_cache
    try:
        stamp = CATALOG_PATH.stat().st_mtime
    except OSError:
        stamp = 0.0
    if _file_cache is not None and _file_cache[0] == stamp:
        return _file_cache[1]
    data = _load()
    _file_cache = (stamp, data)
    return data


def catalog() -> dict[str, Any]:
    """Каталог целиком: {"origin", "version", "guides": [...]}, файл плюс правки."""
    global _cache
    base = factory()
    key = (_file_cache[0] if _file_cache else 0.0, _overrides_version)
    if _cache is not None and _cache[0] == key:
        return _cache[1]
    data = _merge(base)
    _cache = (key, data)
    return data


def _merge(base: dict[str, Any]) -> dict[str, Any]:
    """Записи админов поверх заводских: своя перекрывает одноимённую,
    выключенная прячет её, новая встаёт в конец списка."""
    if not _overrides:
        return base
    by_id = {g["id"]: g for g in base["guides"]}
    order = [g["id"] for g in base["guides"]]
    for guide_id, record in _overrides.items():
        if not record.get("enabled", True):
            by_id.pop(guide_id, None)
            continue
        by_id[guide_id] = _prepare(dict(record))
        if guide_id not in order:
            order.append(guide_id)
    return {**base, "guides": [by_id[gid] for gid in order if gid in by_id]}


def _prepare(guide: dict[str, Any]) -> dict[str, Any]:
    """Общие умолчания сценария — одинаковые для файла и для записи из БД."""
    guide.setdefault("audience", audience_of.ANY)
    guide["audience"] = audience_of.normalize(guide["audience"])
    guide.setdefault("steps", [])
    # Откуда сценарий начинается — адрес первого шага с адресом, если
    # явно не задано иначе. Виджет вне расширения показывает его ссылкой.
    if not guide.get("start"):
        guide["start"] = next((s.get("url") for s in guide["steps"] if s.get("url")), "/")
    return guide


def _load() -> dict[str, Any]:
    try:
        data: dict[str, Any] = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    except OSError, ValueError:
        log.exception("Каталог сценариев не прочитан: %s", CATALOG_PATH)
        return {"origin": "", "version": 0, "guides": []}
    data["guides"] = [_prepare(g) for g in data.get("guides") or [] if isinstance(g, dict) and g.get("id")]
    return data


def all_guides(audience: str = audience_of.ANY) -> list[dict[str, Any]]:
    """Сценарии, подходящие роли (неизвестная роль — все)."""
    return [g for g in catalog()["guides"] if audience_of.fits(g["audience"], audience)]


def get(guide_id: str) -> dict[str, Any] | None:
    return next((g for g in catalog()["guides"] if g["id"] == guide_id), None)


def _coverage(pattern: str, question: set[str]) -> float:
    """Доля слов формулировки сценария, которые есть в вопросе: 0..1."""
    tokens = set(tokenize(pattern))
    if not tokens or not question:
        return 0.0
    return len(tokens & question) / len(tokens)


def match(
    question: str, audience: str = audience_of.ANY, min_overlap: float = DEFAULT_MIN_OVERLAP
) -> dict[str, Any] | None:
    """Сценарий к вопросу либо None.

    Берём лучший охват среди формулировок каждого сценария; при равном охвате
    побеждает более длинная формулировка — она конкретнее. Если лучшие
    сценарии адресованы разным сторонам сделки, а роль не выбрана, не
    выбираем: чат в этот момент и так спрашивает «вы поставщик или заказчик?».
    """
    words = set(tokenize(question))
    if not words:
        return None
    scored: list[tuple[float, int, dict[str, Any]]] = []
    for guide in all_guides(audience):
        best = 0.0
        best_len = 0
        for pattern in guide.get("questions") or []:
            score = _coverage(pattern, words)
            length = len(tokenize(pattern))
            if score > best or (score == best and length > best_len):
                best, best_len = score, length
        if best >= min_overlap:
            scored.append((best, best_len, guide))
    if not scored:
        return None
    scored.sort(key=lambda item: (-item[0], -item[1]))
    top_score, top_len = scored[0][0], scored[0][1]
    leaders = [g for score, length, g in scored if score == top_score and length == top_len]
    roles = {g["audience"] for g in leaders}
    if not audience_of.is_known(audience) and {audience_of.SUPPLIER, audience_of.CUSTOMER} <= roles:
        return None
    # При известной роли адресный сценарий важнее общего
    leaders.sort(key=lambda g: 0 if g["audience"] == audience_of.normalize(audience) else 1)
    return leaders[0]


def public(guide: dict[str, Any]) -> dict[str, Any]:
    """Сценарий для интерфейса: без служебных формулировок подбора."""
    return {
        "id": guide["id"],
        "title": guide.get("title", ""),
        "audience": guide.get("audience", audience_of.ANY),
        "origin": catalog().get("origin", ""),
        "start": guide.get("start", "/"),
        "steps": guide.get("steps", []),
    }
