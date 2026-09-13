"""Темы обращений и их рост: о чём спрашивают и что начало болеть на этой неделе.

Частотность по тексту, которая была раньше (см. TOP_QUERIES_KEY в chat.py), считает
«как зарегистрироваться», «не могу создать личный кабинет» и «регистрация не
проходит» тремя разными редкими вопросами. Для подсказок этого хватает, для отчёта
руководителю поддержки — нет: там нужна одна тема с тремя формулировками.

Поэтому кластеризуем по эмбеддингам. Рост важнее топа: верхние темы в поддержке и
так все знают наизусть, а ценно то, что выросло втрое за неделю — это либо сломанный
релиз портала, либо дыра в документации.
"""

import logging
import math
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from sqlalchemy import select

from app.models import AnalyticsReport, ChatSession, Message
from app.services import guardrails

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.adapters.embeddings import Embedder
    from app.runtime import AppRuntime

log = logging.getLogger(__name__)

REPORT_KIND = "topics"
# Эмбеддер принимает пачками; шестьдесят четыре — компромисс между числом
# round-trip'ов к TEI и размером тела запроса.
_EMBED_BATCH = 64


def _normalize(vector: list[float]) -> list[float]:
    length = math.sqrt(sum(x * x for x in vector))
    return [x / length for x in vector] if length else vector


def _dot(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b, strict=True))


def cluster(vectors: list[list[float]], threshold: float) -> list[list[int]]:
    """Жадная кластеризация «лидерами»: один проход, без внешних зависимостей.

    Агломеративная кластеризация дала бы аккуратнее, но она квадратична по
    памяти и времени и тянет scipy/sklearn, которых в проекте нет. На масштабе
    тысяч сообщений и сотен тем одного прохода достаточно, а разница в качестве
    не стоит новой зависимости и минут расчёта.

    Порядок входа влияет на результат — это цена простоты. Сообщения идут по
    времени, то есть раскладка стабильна между пересчётами одного и того же окна.
    """
    centroids: list[list[float]] = []
    members: list[list[int]] = []
    for index, vector in enumerate(vectors):
        best, best_sim = -1, threshold - 1e-9
        for position, centroid in enumerate(centroids):
            sim = _dot(vector, centroid)
            if sim > best_sim:
                best, best_sim = position, sim
        if best < 0:
            centroids.append(list(vector))
            members.append([index])
            continue
        members[best].append(index)
        size = len(members[best])
        centroids[best] = _normalize(
            [(old * (size - 1) + new) / size for old, new in zip(centroids[best], vector, strict=True)]
        )
    return members


def _centroid(vectors: list[list[float]]) -> list[float]:
    size = len(vectors)
    dims = len(vectors[0])
    return _normalize([sum(v[d] for v in vectors) / size for d in range(dims)])


async def _embed(embedder: Embedder, texts: list[str]) -> list[list[float]]:
    out: list[list[float]] = []
    for start in range(0, len(texts), _EMBED_BATCH):
        chunk = await embedder.embed(texts[start : start + _EMBED_BATCH])
        out.extend(_normalize(vector) for vector in chunk)
    return out


async def _questions(db: AsyncSession, since: datetime, until: datetime, limit: int) -> list[Message]:
    """Реплики клиентов за окно. Вложения без текста отсекаем — кластеризовать нечего."""
    return list(
        (
            await db.execute(
                select(Message)
                .where(
                    Message.role == "user",
                    Message.created_at >= since,
                    Message.created_at < until,
                    Message.content != "",
                )
                .order_by(Message.created_at)
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )


async def _escalated_sessions(db: AsyncSession, since: datetime) -> set[str]:
    return set(
        (
            await db.execute(
                select(ChatSession.id).where(
                    ChatSession.escalated_at.is_not(None), ChatSession.created_at >= since
                )
            )
        )
        .scalars()
        .all()
    )


def _growth(now: int, before: int) -> float:
    """Сглаженное отношение: без единиц тема 0 → 2 давала бы бесконечный рост.

    Сглаживание намеренно грубое. Оно занижает рост у маленьких тем — именно этого
    мы и хотим: скачок с одного обращения на три не должен возглавлять сводку.
    """
    return round((now + 1) / (before + 1), 2)


async def build_report(rt: AppRuntime, days: int) -> dict[str, Any]:
    """Кластеры вопросов за окно и их изменение к предыдущему такому же окну.

    Предыдущий период НЕ кластеризуется заново: его сообщения раскладываются по
    уже найденным темам. Иначе сравнивать было бы нечего — два независимых
    прогона дают разные кластеры, и «рост темы» превратился бы в сравнение
    случайных группировок.
    """
    similarity = float(rt.dyn.get("topics.similarity"))
    min_size = int(rt.dyn.get("topics.min_cluster_size"))
    limit = int(rt.dyn.get("topics.max_messages"))

    now = datetime.now(UTC)
    since = now - timedelta(days=days)
    previous_since = since - timedelta(days=days)

    async with rt.db.session_factory() as db:
        current = await _questions(db, since, now, limit)
        earlier = await _questions(db, previous_since, since, limit)
        escalated = await _escalated_sessions(db, previous_since)

    if not current:
        return {"days": days, "generated_at": now.isoformat(), "messages": 0, "topics": []}

    embedder = rt.retriever.embedder
    vectors = await _embed(embedder, [m.content for m in current])
    groups = cluster(vectors, similarity)

    old_vectors = await _embed(embedder, [m.content for m in earlier]) if earlier else []
    centroids = [_centroid([vectors[i] for i in group]) for group in groups]
    previous_counts = [0] * len(groups)
    for vector in old_vectors:
        best, best_sim = -1, similarity - 1e-9
        for position, centroid in enumerate(centroids):
            sim = _dot(vector, centroid)
            if sim > best_sim:
                best, best_sim = position, sim
        if best >= 0:
            previous_counts[best] += 1

    topics = []
    for position, group in enumerate(groups):
        if len(group) < min_size:
            continue
        messages = [current[i] for i in group]
        centroid = centroids[position]
        # Ярлык — медоид: реально заданный вопрос, ближайший к центру темы.
        # Формулировка модели читалась бы глаже, но это лишний вызов LLM на
        # каждую тему и риск получить пересказ вместо того, что писали люди.
        medoid = max(group, key=lambda i: _dot(vectors[i], centroid))
        before = previous_counts[position]
        topics.append(
            {
                "label": _clean(current[medoid].content, 90),
                "count": len(group),
                "previous": before,
                "delta": len(group) - before,
                "growth": _growth(len(group), before),
                "escalated": sum(1 for m in messages if m.session_id in escalated),
                "examples": [_clean(m.content, 140) for m in messages[:3]],
            }
        )

    topics.sort(key=lambda t: -int(str(t["count"])))
    return {
        "days": days,
        "generated_at": now.isoformat(),
        "messages": len(current),
        "topics": topics,
    }


def _clean(text: str, limit: int) -> str:
    """Маскируем ПДн: тема и примеры показываются персоналу и уезжают в выгрузки,
    а в реплики люди вписывают телефоны, почту и ИНН прямо посреди вопроса."""
    return guardrails.mask_pii(text.strip())[:limit]


async def refresh(rt: AppRuntime, days: int) -> dict[str, Any]:
    """Считает отчёт и сохраняет его. Возвращает то же, что и build_report."""
    report = await build_report(rt, days)
    async with rt.db.session_factory() as db:
        db.add(AnalyticsReport(kind=REPORT_KIND, payload=report))
        await db.commit()
    log.info(
        "Отчёт по темам за %d дн.: %d тем из %d сообщений", days, len(report["topics"]), report["messages"]
    )
    return report


async def last_report(rt: AppRuntime, days: int) -> dict[str, Any] | None:
    """Последний сохранённый отчёт по этому окну.

    Окно — часть ключа: отчёт за неделю и за месяц это разные отчёты, и отдавать
    один вместо другого значит показывать неверные числа под верным заголовком.
    """
    async with rt.db.session_factory() as db:
        rows = (
            (
                await db.execute(
                    select(AnalyticsReport)
                    .where(AnalyticsReport.kind == REPORT_KIND)
                    .order_by(AnalyticsReport.created_at.desc())
                    .limit(20)
                )
            )
            .scalars()
            .all()
        )
    for row in rows:
        if (row.payload or {}).get("days") == days:
            return row.payload
    return None


def is_stale(report: dict[str, Any] | None, max_age_hours: float) -> bool:
    if not report:
        return True
    try:
        generated = datetime.fromisoformat(report["generated_at"])
    except KeyError, ValueError:
        return True
    return datetime.now(UTC) - generated > timedelta(hours=max_age_hours)
