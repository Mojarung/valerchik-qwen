"""Работа с базой знаний внутри диалога: поиск, цитаты, учёт вопросов.

Отдельным модулем от оркестрации: здесь всё, что диалог спрашивает у базы
знаний и что кладёт обратно — предварительный поиск, иллюстрации к цитатам,
происхождение статей, счётчик частых вопросов и запись пробелов.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, cast

from sqlalchemy import select

from app.models import KBArticle, KBGap
from app.rag.text import lemmas
from app.services import audience as audience_of
from app.services import guardrails, guides
from app.services.retrieval import RetrievedDoc, tokenize

if TYPE_CHECKING:
    from redis.asyncio import Redis

    from app.services.chat import ChatService

log = logging.getLogger(__name__)

TOP_QUERIES_KEY = "queries:top"
# Сколько раз вопрос должен прозвучать, чтобы попасть в подсказки посетителям.
# Порог отделяет «часто спрашивают» от «кто-то один написал»: одиночная реплика
# в подсказках — это чужое сообщение, показанное постороннему.
MIN_QUERY_HITS = 3
# Картинок под одним ответом: больше — это уже альбом, а не иллюстрация к шагам
MAX_ANSWER_IMAGES = 2


def _confident(citation: dict[str, Any], floor: float) -> bool:
    return float(citation.get("score") or 0.0) >= floor


def _pick_images(
    citations: list[dict[str, Any]], images: dict[str, list[dict[str, Any]]], answer: str
) -> dict[str, list[dict[str, Any]]]:
    """Картинки к ответу: не больше MAX_ANSWER_IMAGES, те, чья подпись ближе к тексту ответа.

    У раздела методички бывает по четыре скриншота, а источников у ответа — несколько:
    показывать всё подряд значит завалить ответ картинками кнопок, о которых в нём ни
    слова. Если ни одна подпись с ответом не совпала — одна картинка главного источника.
    """
    words = set(lemmas(answer))
    ranked = [
        (
            -len(words & set(lemmas(image.get("caption") or ""))),
            order,
            position,
            citation["article_id"],
            image,
        )
        for order, citation in enumerate(citations)
        for position, image in enumerate(images.get(citation.get("article_id") or "", []))
    ]
    ranked.sort(key=lambda row: row[:3])
    best = [row for row in ranked if row[0] < 0][:MAX_ANSWER_IMAGES] or ranked[:1]
    chosen: dict[str, list[dict[str, Any]]] = {}
    for _, _, _, article_id, image in sorted(best, key=lambda row: row[1:3]):  # в порядке документа
        chosen.setdefault(article_id, []).append(image)
    return chosen


async def _attach_citation_images(
    svc: ChatService, citations: list[dict[str, Any]], answer: str = ""
) -> None:
    """Дополняет цитаты иллюстрациями их статей — одним запросом на весь ответ, не больше двух картинок.

    Картинки не лежат в векторном индексе (там только текстовые поля), поэтому
    берём их из БЗ по идентификаторам уже отобранных статей. Только у цитат выше
    порога уверенности: слабая цитата — «возможно, пригодится», и её картинка рядом
    с ответом выглядела бы иллюстрацией к нему.
    """
    floor = float(svc.rt.dyn.get("agent.confidence_threshold"))
    confident = [c for c in citations if c.get("article_id") and _confident(c, floor)]
    ids = [c["article_id"] for c in confident]
    if not ids:
        return
    try:
        async with svc.rt.db.session_factory() as db:
            rows = (
                await db.execute(select(KBArticle.id, KBArticle.images).where(KBArticle.id.in_(ids)))
            ).all()
    except Exception:
        log.exception("Не удалось подтянуть иллюстрации к цитатам")
        return
    chosen = _pick_images(confident, {article_id: value or [] for article_id, value in rows}, answer)
    for citation in citations:
        found = chosen.get(citation.get("article_id") or "")
        if found:
            citation["images"] = found


async def _provenance_of(svc: ChatService, article_ids: list[str]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Происхождение и картинки статей одним запросом.

    Оба поля живут только в базе: в векторном индексе их нет. Берём разом —
    решение «отвечать дословно» зависит и от того, и от другого.
    """
    ids = [article_id for article_id in article_ids if article_id]
    if not ids:
        return {}, {}
    try:
        async with svc.rt.db.session_factory() as db:
            rows = (
                await db.execute(
                    select(KBArticle.id, KBArticle.origin, KBArticle.images).where(KBArticle.id.in_(ids))
                )
            ).all()
    except Exception:
        log.exception("Не удалось прочитать происхождение статей")
        return {}, {}
    origins = {article_id: (origin or {}) for article_id, origin, _ in rows}
    visuals = {article_id: (images or []) for article_id, _, images in rows}
    return origins, visuals


async def _probe_kb(
    svc: ChatService, text: str, page_context: str = "", audience: str = audience_of.ANY
) -> list[RetrievedDoc] | None:
    """Предварительный поиск по базе знаний — один на сообщение.

    Его выдача нужна дважды: решить, двусмысленный ли вопрос, и решить,
    поднимать ли внешние инструменты. Раньше такой поиск делался только ради
    второго; заводить ещё один проход по индексу ради первого значило бы
    платить лишними десятками миллисекунд на каждом сообщении.

    None — поиск сломался: это отличается от «ничего не нашлось» и
    обрабатывается по-разному.
    """
    try:
        return await svc.rt.retriever.search(text, context=page_context, audience=audience)
    except Exception:
        log.exception("Предварительный поиск по базе знаний не удался")
        return None


def _guide_for(svc: ChatService, text: str, audience: str) -> dict[str, Any] | None:
    """Сценарий по порталу к вопросу, если он есть и включён."""
    if not svc.rt.dyn.get("guides.enabled"):
        return None
    try:
        found = guides.match(text, audience, float(svc.rt.dyn.get("guides.min_overlap")))
    except Exception:
        log.exception("Подбор сценария по порталу не удался")
        return None
    return guides.public(found) if found else None


# Короче трёх символов — не запрос, длиннее ста двадцати — уже рассказ:
# в популярные вопросы такое не попадает
_TOP_QUERY_MIN_CHARS = 3
_TOP_QUERY_MAX_CHARS = 120


async def popular_queries(cache: Redis, limit: int) -> list[str]:
    """Частые вопросы посетителей, самые частые первыми, — только прозвучавшие
    не меньше MIN_QUERY_HITS раз: одиночная реплика в подсказках — это чужое
    сообщение, показанное постороннему."""
    # redis-py описывает ответ zrevrange объединением всех вариантов вызова; у
    # клиента с decode_responses=True и withscores это пары (строка, счёт)
    top = cast(list[tuple[str, float]], await cache.zrevrange(TOP_QUERIES_KEY, 0, limit - 1, withscores=True))
    return [text for text, score in top if score >= MIN_QUERY_HITS]


async def _track_query(svc: ChatService, text: str) -> None:
    """Счётчик популярных вопросов.

    Текст обязательно маскируем: этот список уходит обратно посетителям как
    подсказки автодополнения и чипсы на приветственном экране. Сырые реплики
    там означали, что любой аноним вычитывал чужие сообщения вместе с
    телефонами, почтой и ИНН — достаточно было перебрать короткие подстроки.

    Многострочный текст — не формулировка посетителя, а склейка: продолжение с
    прошлым вопросом (chat/followup.py) или повтор после 👎 с уточнением. В
    подсказках такое читалось бы как два вопроса в одной карточке.
    """
    masked = guardrails.mask_pii(text.strip())
    if "\n" in masked:
        return
    if _TOP_QUERY_MIN_CHARS <= len(masked) <= _TOP_QUERY_MAX_CHARS:
        await svc.rt.cache.zincrby(TOP_QUERIES_KEY, 1, masked)


async def _record_gap(svc: ChatService, query: str, best_score: float) -> None:
    norm = " ".join(tokenize(query))[:500]
    if not norm:
        return
    async with svc.rt.db.session_factory() as db:
        row = (await db.execute(select(KBGap).where(KBGap.query_norm == norm))).scalar_one_or_none()
        if row:
            row.hits += 1
            row.last_seen = datetime.now(UTC)
            row.best_score = max(row.best_score, best_score)
        else:
            db.add(KBGap(query_norm=norm, query_example=query, best_score=best_score))
        await db.commit()
