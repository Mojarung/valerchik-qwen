"""Векторное хранилище: Qdrant по REST (full) или in-memory numpy (lite/тесты).

Переиндексация без даунтайма: новая коллекция kb_v{N+1} наполняется в фоне,
затем атомарно переключается алиас kb_current (паттерн blue/green для поиска).

Точка — не статья. У статьи из выгрузки поддержки кроме основной точки (вопрос,
ключевые слова, начало ответа) есть по точке на каждую реальную формулировку
обращения: так её находит и тот, кто написал иначе, чем вопрос статьи. Поэтому
всё, что снаружи, говорит статьями: search отдаёт статью один раз, по лучшей её
точке, delete и upsert убирают и заменяют все точки статьи разом, count считает
статьи. Точки считает points — для сверки индекса с базой.
"""

import json
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import httpx
import numpy as np

ALIAS = "kb_current"
# Поле точки со статьёй, к которой она относится
ARTICLE_FIELD = "article_id"
# Поле точки-формулировки: у основной точки статьи его нет
PHRASING_FIELD = "phrasing"
# Сколько точек брать на одну нужную статью. Ближайшие к вопросу точки часто
# принадлежат одной статье (все её формулировки похожи между собой), и выдача
# в top_k точек дала бы меньше top_k статей. Недобор дочитывается следующей
# страницей, но не дальше SEARCH_PAGES страниц: хвост выдачи на порядок не влияет.
POINTS_PER_ARTICLE = 4
SEARCH_PAGES = 3
# Пространство имён для id точек-формулировок: Qdrant принимает только UUID, а
# точка «статья:номер» должна получать один и тот же id при каждой индексации —
# тогда повторная индексация перезаписывает точки, а не плодит их.
_POINT_NAMESPACE = uuid.UUID("6f1c4a52-9d0e-4b7a-8c3f-2e5d1a0b9c84")


@dataclass
class VectorDoc:
    id: str  # id точки
    vector: list[float]
    payload: dict[str, Any]
    # Статья, к которой относится точка; пусто — точка и есть статья
    article_id: str = ""

    def __post_init__(self) -> None:
        if not self.article_id:
            self.article_id = self.id


@dataclass
class Hit:
    id: str  # id статьи, а не точки
    score: float  # близость лучшей точки статьи
    payload: dict[str, Any]  # payload лучшей точки
    # Близость основной точки статьи, если она попала в прочитанную выдачу.
    # Формулировку поиск взвешивает иначе, чем основную точку (см. Retriever),
    # и без этого числа не знал бы, чем заменить формулировку после скидки.
    main_score: float | None = None

    @property
    def phrasing(self) -> bool:
        """Лучшая точка статьи — формулировка, а не основная точка."""
        return PHRASING_FIELD in self.payload


class VectorStore(Protocol):
    async def ensure_ready(self, dim: int) -> None: ...
    async def reindex(self, docs: list[VectorDoc]) -> None: ...
    async def upsert(self, docs: list[VectorDoc]) -> None: ...
    async def delete(self, ids: list[str]) -> None: ...
    async def search(self, vector: list[float], top_k: int) -> list[Hit]: ...
    async def count(self) -> int: ...
    async def points(self) -> int: ...


def best_per_article(hits: Iterable[tuple[str, float, dict[str, Any]]], top_k: int) -> list[Hit]:
    """Точки по убыванию близости → статьи по лучшей их точке, не больше top_k.

    Основная точка статьи запоминается отдельно (Hit.main_score), даже когда
    лучшей оказалась формулировка. Точки дочитываются до конца выдачи: основная
    точка статьи обычно идёт следом за её формулировками.
    """
    found: dict[str, Hit] = {}
    for article_id, score, payload in hits:
        hit = found.get(article_id)
        if hit is None:
            if len(found) >= top_k:
                continue
            hit = found[article_id] = Hit(article_id, score, payload)
        if hit.main_score is None and PHRASING_FIELD not in payload:
            hit.main_score = score
    return list(found.values())


class MemoryStore:
    """Косинусный поиск на numpy. Персистится в data_dir, атомарная подмена при reindex."""

    def __init__(self, data_dir: Path | None = None) -> None:
        self._ids: list[str] = []  # id точек
        self._articles: list[str] = []  # статья каждой точки, в том же порядке
        self._matrix: np.ndarray | None = None
        self._payloads: dict[str, dict[str, Any]] = {}  # по id точки
        self._path = (data_dir / "memory_index") if data_dir else None
        if self._path and (self._path.with_suffix(".npz")).exists():
            self._load()

    def _load(self) -> None:
        assert self._path is not None
        data = np.load(self._path.with_suffix(".npz"), allow_pickle=False)
        meta = json.loads(self._path.with_suffix(".json").read_text(encoding="utf-8"))
        self._matrix = data["matrix"]
        self._ids = meta["ids"]
        # Индекс, сохранённый до точек-формулировок: точка и была статьёй
        self._articles = meta.get("articles") or list(self._ids)
        self._payloads = meta["payloads"]

    def _save(self) -> None:
        if not self._path:
            return
        self._path.parent.mkdir(parents=True, exist_ok=True)
        if self._matrix is not None:
            np.savez_compressed(self._path.with_suffix(".npz"), matrix=self._matrix)
            self._path.with_suffix(".json").write_text(
                json.dumps(
                    {"ids": self._ids, "articles": self._articles, "payloads": self._payloads},
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

    def _replace(self, kept: list[tuple[str, str, np.ndarray, dict[str, Any]]]) -> None:
        """Подмена всех ссылок разом — читатели не видят полусобранный индекс."""
        ids = [point for point, _, _, _ in kept]
        articles = [article for _, article, _, _ in kept]
        matrix = np.stack([vector for _, _, vector, _ in kept]) if kept else None
        payloads = {point: payload for point, _, _, payload in kept}
        self._ids, self._articles, self._matrix, self._payloads = ids, articles, matrix, payloads
        self._save()

    def _current(self) -> list[tuple[str, str, np.ndarray, dict[str, Any]]]:
        # На пустом индексе матрицы ещё нет: обращение по индексу к None —
        # падение на первом же добавлении статьи в свежую базу.
        matrix = self._matrix
        if matrix is None:
            return []
        return [
            (point, self._articles[n], matrix[n], self._payloads[point]) for n, point in enumerate(self._ids)
        ]

    @staticmethod
    def _rows(docs: list[VectorDoc]) -> list[tuple[str, str, np.ndarray, dict[str, Any]]]:
        return [(d.id, d.article_id, np.array(d.vector, dtype=np.float32), d.payload) for d in docs]

    async def ensure_ready(self, dim: int) -> None:
        return None

    async def reindex(self, docs: list[VectorDoc]) -> None:
        self._replace(self._rows(docs))

    async def upsert(self, docs: list[VectorDoc]) -> None:
        # Статья приезжает всеми своими точками: прежние точки этих статей уходят
        # целиком, иначе формулировка, которой в новой версии нет, жила бы дальше
        touched = {d.article_id for d in docs}
        kept = [row for row in self._current() if row[1] not in touched]
        self._replace(kept + self._rows(docs))

    async def delete(self, ids: list[str]) -> None:
        drop = set(ids)
        self._replace([row for row in self._current() if row[1] not in drop])

    async def search(self, vector: list[float], top_k: int) -> list[Hit]:
        if self._matrix is None or not len(self._ids):
            return []
        q = np.array(vector, dtype=np.float32)
        qn = np.linalg.norm(q) or 1.0
        norms = np.linalg.norm(self._matrix, axis=1)
        norms[norms == 0] = 1.0
        sims = (self._matrix @ q) / (norms * qn)
        order = np.argsort(-sims)
        return best_per_article(
            ((self._articles[i], float(sims[i]), self._payloads[self._ids[i]]) for i in order), top_k
        )

    async def count(self) -> int:
        return len(set(self._articles))

    async def points(self) -> int:
        return len(self._ids)


class QdrantStore:
    """Клиент Qdrant поверх чистого REST (httpx) — без компилируемых зависимостей."""

    def __init__(self, base_url: str, alias: str = ALIAS) -> None:
        self._client = httpx.AsyncClient(base_url=base_url.rstrip("/"), timeout=30.0)
        self._dim = 0
        # Алиас и префикс коллекций: рядом с боевым индексом можно держать опытный
        # (другая модель эмбеддингов), и переиндексация одного не трогает другой.
        self._alias = alias
        self._prefix = f"{alias.removesuffix('_current')}_v"

    @staticmethod
    def _pid(point_id: str) -> str:
        """id точки для Qdrant: основная точка статьи — сам id статьи как UUID
        (так было всегда, и точки старых индексов остаются адресуемыми),
        формулировка — детерминированный UUID от «статья:номер»."""
        try:
            return str(uuid.UUID(hex=point_id))
        except ValueError:
            return str(uuid.uuid5(_POINT_NAMESPACE, point_id))

    async def _collections(self) -> list[str]:
        resp = await self._client.get("/collections")
        resp.raise_for_status()
        return [c["name"] for c in resp.json()["result"]["collections"]]

    async def _alias_target(self) -> str | None:
        resp = await self._client.get("/aliases")
        resp.raise_for_status()
        for a in resp.json()["result"]["aliases"]:
            if a["alias_name"] == self._alias:
                return str(a["collection_name"])
        return None

    async def _create_collection(self, name: str) -> None:
        resp = await self._client.put(
            f"/collections/{name}",
            json={"vectors": {"size": self._dim, "distance": "Cosine"}},
        )
        resp.raise_for_status()
        # Удаление статьи — фильтр по этому полю: без индекса Qdrant перебирал бы
        # все точки коллекции на каждое снятие статьи с публикации
        resp = await self._client.put(
            f"/collections/{name}/index",
            params={"wait": "true"},
            json={"field_name": ARTICLE_FIELD, "field_schema": "keyword"},
        )
        resp.raise_for_status()

    async def ensure_ready(self, dim: int) -> None:
        self._dim = dim
        if await self._alias_target() is None:
            first = f"{self._prefix}1"
            if first not in await self._collections():
                await self._create_collection(first)
            await self._set_alias(first)

    async def _set_alias(self, collection: str) -> None:
        current = await self._alias_target()
        actions: list[dict[str, Any]] = []
        if current:
            actions.append({"delete_alias": {"alias_name": self._alias}})
        actions.append({"create_alias": {"collection_name": collection, "alias_name": self._alias}})
        resp = await self._client.post("/collections/aliases", json={"actions": actions})
        resp.raise_for_status()

    async def _put_points(self, collection: str, docs: list[VectorDoc]) -> None:
        for i in range(0, len(docs), 64):
            batch = docs[i : i + 64]
            resp = await self._client.put(
                f"/collections/{collection}/points",
                params={"wait": "true"},
                json={
                    "points": [
                        {
                            "id": self._pid(d.id),
                            "vector": d.vector,
                            "payload": {**d.payload, ARTICLE_FIELD: d.article_id},
                        }
                        for d in batch
                    ]
                },
            )
            resp.raise_for_status()

    async def _delete_where(self, selector: dict[str, Any]) -> None:
        resp = await self._client.post(
            f"/collections/{self._alias}/points/delete",
            params={"wait": "true"},
            json={"filter": selector},
        )
        resp.raise_for_status()

    async def reindex(self, docs: list[VectorDoc]) -> None:
        current = await self._alias_target() or f"{self._prefix}0"
        version = int(current.rsplit("_v", 1)[-1]) + 1
        new_name = f"{self._prefix}{version}"
        await self._create_collection(new_name)
        await self._put_points(new_name, docs)
        await self._set_alias(new_name)  # атомарное переключение — поиск не прерывается
        for name in await self._collections():
            if name.startswith(self._prefix) and name != new_name:
                await self._client.delete(f"/collections/{name}")

    async def upsert(self, docs: list[VectorDoc]) -> None:
        """Точки статей записываются, а прежние точки тех же статей, которых в
        новой версии нет, удаляются. Сначала запись, потом чистка: между ними
        статья остаётся в поиске, пусть и с лишней формулировкой."""
        if not docs:
            return
        await self._put_points(self._alias, docs)
        await self._delete_where(
            {
                "must": [{"key": ARTICLE_FIELD, "match": {"any": sorted({d.article_id for d in docs})}}],
                "must_not": [{"has_id": [self._pid(d.id) for d in docs]}],
            }
        )

    async def delete(self, ids: list[str]) -> None:
        """Все точки статей: по полю статьи и по id основной точки — у точек,
        записанных до формулировок, поля статьи нет."""
        if not ids:
            return
        await self._delete_where(
            {
                "should": [
                    {"key": ARTICLE_FIELD, "match": {"any": list(ids)}},
                    {"has_id": [self._pid(i) for i in ids]},
                ]
            }
        )

    async def search(self, vector: list[float], top_k: int) -> list[Hit]:
        limit = top_k * POINTS_PER_ARTICLE
        points: list[tuple[str, float, dict[str, Any]]] = []
        for page in range(SEARCH_PAGES):
            resp = await self._client.post(
                f"/collections/{self._alias}/points/search",
                json={"vector": vector, "limit": limit, "offset": page * limit, "with_payload": True},
            )
            resp.raise_for_status()
            found = resp.json()["result"]
            for p in found:
                payload = p.get("payload") or {}
                # Точка старого индекса поля статьи не несёт: её id и есть статья
                article_id = str(payload.get(ARTICLE_FIELD) or uuid.UUID(p["id"]).hex)
                points.append((article_id, float(p["score"]), payload))
            if len(found) < limit or len({article for article, _, _ in points}) >= top_k:
                break
        return best_per_article(points, top_k)

    async def _count(self, selector: dict[str, Any] | None = None) -> int:
        target = await self._alias_target()
        if not target:
            return 0
        body: dict[str, Any] = {"exact": True}
        if selector:
            body["filter"] = selector
        resp = await self._client.post(f"/collections/{target}/points/count", json=body)
        resp.raise_for_status()
        return int(resp.json()["result"]["count"])

    async def count(self) -> int:
        """Статьи в индексе: у каждой ровно одна основная точка — без формулировки."""
        return await self._count({"must": [{"is_empty": {"key": PHRASING_FIELD}}]})

    async def points(self) -> int:
        return await self._count()


def create_vector_store(qdrant_url: str, data_dir: Path) -> VectorStore:
    return QdrantStore(qdrant_url) if qdrant_url else MemoryStore(data_dir)
