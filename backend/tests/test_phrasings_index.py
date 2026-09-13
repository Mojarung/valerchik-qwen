"""Формулировки вопроса в поиске: точка на формулировку, статья — одна в выдаче.

Статья из выгрузки поддержки хранит реальные формулировки своей группы
обращений, и каждая становится отдельной точкой векторного индекса. Проверяем
три обещания: поиск отдаёт статью один раз, по лучшей точке; снятие, архив и
переимпорт убирают все её точки, а не одну основную; лексический индекс видит
формулировки так же, как ключевые слова.
"""

import json
import uuid

import httpx
import pytest
from sqlalchemy import select

from app.adapters.embeddings import HashEmbedder
from app.adapters.vectorstore import (
    ARTICLE_FIELD,
    PHRASING_FIELD,
    Hit,
    MemoryStore,
    QdrantStore,
    VectorDoc,
)
from app.dynamic_settings import SETTINGS
from app.models import KBArticle, new_id
from app.rag import ingest as rag_ingest
from app.services import phrasings
from app.services.retrieval import Retriever, vector_confidence

ARTICLE = "a" * 32
OTHER = "b" * 32


class _Dyn:
    def get(self, key, default=None):
        return SETTINGS[key].default


def _unit(axis: int, dim: int = 4, lean: float = 0.0) -> list[float]:
    vector = [0.0] * dim
    vector[axis] = 1.0
    vector[(axis + 1) % dim] = lean
    return vector


async def test_search_gives_an_article_once_by_its_best_point(tmp_path):
    store = MemoryStore(tmp_path)
    await store.reindex(
        [
            VectorDoc(ARTICLE, _unit(0, lean=0.9), {"question": "основная"}),
            VectorDoc(f"{ARTICLE}:1", _unit(0), {"question": "основная", PHRASING_FIELD: "ф1"}, ARTICLE),
            VectorDoc(f"{ARTICLE}:2", _unit(0, lean=0.1), {PHRASING_FIELD: "ф2"}, ARTICLE),
            VectorDoc(OTHER, _unit(0, lean=0.5), {"question": "другая"}),
        ]
    )

    hits = await store.search(_unit(0), top_k=5)

    # Три точки первой статьи ближе второй статьи, но в выдаче она одна — и по
    # лучшей точке, формулировке, совпавшей с вопросом дословно
    assert [hit.id for hit in hits] == [ARTICLE, OTHER]
    assert hits[0].score == pytest.approx(1.0)
    assert hits[0].payload[PHRASING_FIELD] == "ф1"
    assert await store.count() == 2
    assert await store.points() == 4
    # top_k считает статьи, а не точки
    assert [hit.id for hit in await store.search(_unit(0), top_k=1)] == [ARTICLE]


async def test_memory_store_removes_and_replaces_all_points_of_an_article(tmp_path):
    store = MemoryStore(tmp_path)
    await store.reindex(
        [
            VectorDoc(ARTICLE, _unit(0), {}),
            VectorDoc(f"{ARTICLE}:1", _unit(1), {}, ARTICLE),
            VectorDoc(f"{ARTICLE}:2", _unit(2), {}, ARTICLE),
            VectorDoc(OTHER, _unit(3), {}),
        ]
    )

    # Новая версия статьи — с одной формулировкой: вторая уходит, а не остаётся
    await store.upsert([VectorDoc(ARTICLE, _unit(0), {}), VectorDoc(f"{ARTICLE}:1", _unit(1), {}, ARTICLE)])
    assert await store.points() == 3
    # Вопрос, совпавший с ушедшей формулировкой, её больше не находит
    assert all(hit.score < 0.5 for hit in await store.search(_unit(2), top_k=5))

    await store.delete([ARTICLE])
    assert await store.points() == 1
    assert {hit.id for hit in await store.search(_unit(1), top_k=5)} == {OTHER}

    # Статьи точек переживают перезапуск: новый инстанс читает их с диска
    reloaded = MemoryStore(tmp_path)
    assert await reloaded.points() == 1
    assert await reloaded.count() == 1


class _FakeQdrant:
    """Qdrant без сети: запоминает запросы, отдаёт заготовленную выдачу."""

    def __init__(self, found: list[dict] | None = None) -> None:
        self.found = found or []
        self.calls: list[tuple[str, str, dict]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else {}
        self.calls.append((request.method, request.url.path, body))
        if request.url.path.endswith("/points/search"):
            offset = body.get("offset", 0)
            return httpx.Response(200, json={"result": self.found[offset : offset + body["limit"]]})
        return httpx.Response(200, json={"result": {}})


def _qdrant(fake: _FakeQdrant) -> QdrantStore:
    store = QdrantStore("http://qdrant")
    store._client = httpx.AsyncClient(base_url="http://qdrant", transport=httpx.MockTransport(fake))
    return store


async def test_qdrant_delete_takes_every_point_of_the_article():
    fake = _FakeQdrant()

    await _qdrant(fake).delete([ARTICLE])

    [(method, path, body)] = fake.calls
    assert (method, path) == ("POST", "/collections/kb_current/points/delete")
    # По полю статьи — точки формулировок, по id — основная точка старого
    # индекса, у которой поля статьи ещё нет
    assert body["filter"]["should"] == [
        {"key": ARTICLE_FIELD, "match": {"any": [ARTICLE]}},
        {"has_id": [str(uuid.UUID(hex=ARTICLE))]},
    ]


async def test_qdrant_upsert_writes_points_then_drops_stale_ones():
    fake = _FakeQdrant()
    docs = [VectorDoc(ARTICLE, [1.0], {"question": "в"}), VectorDoc(f"{ARTICLE}:1", [1.0], {}, ARTICLE)]

    await _qdrant(fake).upsert(docs)

    (_, _, put), (_, delete_path, delete) = fake.calls
    ids = [point["id"] for point in put["points"]]
    assert ids[0] == str(uuid.UUID(hex=ARTICLE))
    # id формулировки — UUID, одинаковый при каждой индексации
    assert ids[1] == QdrantStore._pid(f"{ARTICLE}:1") == str(uuid.UUID(ids[1]))
    assert all(point["payload"][ARTICLE_FIELD] == ARTICLE for point in put["points"])
    assert delete_path.endswith("/points/delete")
    assert delete["filter"]["must"] == [{"key": ARTICLE_FIELD, "match": {"any": [ARTICLE]}}]
    assert delete["filter"]["must_not"] == [{"has_id": ids}]


async def test_qdrant_search_collapses_points_and_reads_old_points():
    legacy = str(uuid.UUID(hex=OTHER))
    fake = _FakeQdrant(
        [
            {
                "id": str(uuid.uuid4()),
                "score": 0.95,
                "payload": {ARTICLE_FIELD: ARTICLE, PHRASING_FIELD: "ф"},
            },
            {"id": str(uuid.UUID(hex=ARTICLE)), "score": 0.90, "payload": {ARTICLE_FIELD: ARTICLE}},
            # Точка индекса до формулировок: поля статьи нет, её id и есть статья
            {"id": legacy, "score": 0.80, "payload": {"question": "старая"}},
        ]
    )

    hits = await _qdrant(fake).search([1.0], top_k=5)

    assert [(hit.id, hit.score) for hit in hits] == [(ARTICLE, 0.95), (OTHER, 0.80)]


async def test_main_point_score_is_kept_when_a_phrasing_wins(tmp_path):
    store = MemoryStore(tmp_path)
    await store.reindex(
        [
            VectorDoc(f"{ARTICLE}:1", _unit(0), {PHRASING_FIELD: "ф1"}, ARTICLE),
            VectorDoc(ARTICLE, _unit(1, lean=1.0), {}),
        ]
    )

    [hit] = await store.search(_unit(0), top_k=5)

    assert hit.phrasing
    assert hit.score == pytest.approx(1.0)
    assert hit.main_score == pytest.approx(0.0, abs=1e-6)


def test_phrasing_counts_with_a_margin_and_main_point_can_take_over():
    phrasing = {PHRASING_FIELD: "сколько стоит доставка"}
    # Основная точка ближе остатка — уверенность от неё
    assert vector_confidence(Hit(ARTICLE, 0.48, phrasing, main_score=0.42), 0.09) == pytest.approx(0.42)
    # Основной точки нет в выдаче — формулировка за вычетом скидки
    assert vector_confidence(Hit(ARTICLE, 0.48, phrasing), 0.09) == pytest.approx(0.39)
    # Основная точка статьи — без скидки, выключенная скидка — формулировка как есть
    assert vector_confidence(Hit(ARTICLE, 0.48, {}, main_score=0.48), 0.09) == pytest.approx(0.48)
    assert vector_confidence(Hit(ARTICLE, 0.48, phrasing), 0.0) == pytest.approx(0.48)


class _SemanticStub:
    semantic = True
    dim = 1
    version = "stub"

    async def embed(self, texts):
        return [[1.0] for _ in texts]


class _ScriptedStore:
    def __init__(self, hits: list[Hit]) -> None:
        self.hits = hits

    async def search(self, vector, top_k):
        return self.hits[:top_k]


async def test_near_threshold_phrasing_match_is_not_an_answer():
    """«Сколько стоит доставка пиццы» близко к чужому «сколько стоит доставка
    товара» на 0.48 при пороге 0.40: без скидки бот отвечал бы на вопрос не по
    теме. С той же близостью к основной точке статьи — отвечает, как и раньше."""
    article = {
        "article_id": ARTICLE,
        "question": "Как рассчитать стоимость доставки товара заказчику",
        "answer": "Стоимость доставки включается в цену оферты.",
        "category": "Оферты",
        "line": 1,
        "keywords": [],
        "phrasings": ["Сколько стоит доставка товара"],
    }
    query = "сколько стоит доставка пиццы"
    threshold = SETTINGS["agent.confidence_threshold"].default

    def retriever(hit: Hit) -> Retriever:
        found = Retriever(_SemanticStub(), _ScriptedStore([hit]), _Dyn())
        found.load([article])
        return found

    by_phrasing = await retriever(Hit(ARTICLE, 0.48, {PHRASING_FIELD: "ф"})).search(query)
    by_article = await retriever(Hit(ARTICLE, 0.48, {})).search(query)

    assert by_phrasing[0].score < threshold
    assert by_article[0].score >= threshold


def test_phrasings_from_origin_are_cleaned_and_capped():
    origin = {"phrasings": ["  Не могу  подписать УПД ", "Не могу подписать УПД", 42, "", "УПД не уходит"]}
    assert phrasings.of(origin) == ["Не могу подписать УПД", "УПД не уходит"]
    assert phrasings.of({"phrasings": "не список"}) == []
    assert phrasings.of(None) == []
    many = {"phrasings": [f"формулировка {n}" for n in range(30)]}
    assert len(phrasings.of(many)) == phrasings.LIMIT


async def test_keyword_index_sees_phrasings(tmp_path):
    """Вопрос, слова которого есть только в формулировке, находит статью по словам."""
    article = {
        "article_id": ARTICLE,
        "question": "Как подписать документ о приёмке",
        "answer": "Дождитесь, пока заказчик примет приглашение, и повторите подписание.",
        "category": "Исполнение",
        "line": 1,
        "keywords": [],
        "phrasings": ["Как подписать документ о приёмке", "Контрагент не видит наши счета-фактуры"],
    }
    other = {**article, "article_id": OTHER, "question": "Как войти на портал", "answer": "Через ЕСИА."}
    other["phrasings"] = []
    query = "контрагент не видит счета-фактуры"

    with_phrasings = Retriever(HashEmbedder(), MemoryStore(tmp_path), _Dyn())
    with_phrasings.load([article, other])
    without = Retriever(HashEmbedder(), MemoryStore(tmp_path), _Dyn())
    without.load([{**article, "phrasings": []}, other])

    assert with_phrasings.kw_index.search(query, 5)[0][0] == ARTICLE
    assert without.kw_index.search(query, 5) == []


# --- индексация статей с формулировками на поднятом приложении ---
#
# Индекс приложения строит конвейер RAG (app/rag/index.points_of): отдельная точка — на
# каждую формулировку фрагмента из origin["examples"] (реальные обращения) и
# origin["questions"] (вопросы к фрагменту, doc2query). Прежнее поле origin["phrasings"]
# он не читает.


def _extra_points(origin: dict | None) -> int:
    origin = origin or {}
    return len(dict.fromkeys([*(origin.get("examples") or []), *(origin.get("questions") or [])]))


async def _add_article(rt, texts: list[str], status: str = "active") -> str:
    article_id = new_id()
    async with rt.db.session_factory() as db:
        db.add(
            KBArticle(
                id=article_id,
                question="Не могу подписать УПД в разделе исполнения",
                answer="Заказчику нужно принять приглашение в ЭДО, затем повторите подписание УПД.",
                category="Исполнение",
                keywords=[],
                line=1,
                status=status,
                source="import",
                origin={"pipeline": "rag", "kind": "ticket", "examples": texts},
            )
        )
        await db.commit()
    return article_id


PHRASES = [
    "УПД не уходит заказчику",
    "Нет связи по ЭДО с заказчиком",
    "Заказчик не получает документ о приёмке",
]


@pytest.mark.asyncio
async def test_article_is_indexed_with_its_phrasings_and_removed_whole(client):
    rt = client.app.state.rt
    articles, points = await rt.store.count(), await rt.store.points()
    article_id = await _add_article(rt, PHRASES)

    await rt.kb.index_article(article_id)

    assert await rt.store.count() == articles + 1
    assert await rt.store.points() == points + 1 + len(PHRASES)
    [vector] = await rt.embedder.embed([PHRASES[1]])
    hits = await rt.store.search(vector, top_k=50)
    assert [hit.id for hit in hits].count(article_id) == 1

    await rt.kb.remove_article(article_id)

    assert (await rt.store.count(), await rt.store.points()) == (articles, points)


@pytest.mark.asyncio
async def test_bulk_index_and_archive_move_all_points(client):
    rt = client.app.state.rt
    points = await rt.store.points()
    ids = [await _add_article(rt, PHRASES[:2]), await _add_article(rt, PHRASES)]

    assert await rt.kb.index_many(ids) == 2
    assert await rt.store.points() == points + (1 + 2) + (1 + 3)

    await rt.kb.remove_many(ids)
    assert await rt.store.points() == points


@pytest.mark.asyncio
async def test_full_reindex_counts_articles_not_points(client):
    rt = client.app.state.rt
    await _add_article(rt, PHRASES)

    indexed = await rt.kb.index_all()

    async with rt.db.session_factory() as db:
        active = (await db.execute(select(KBArticle).where(KBArticle.status == "active"))).scalars().all()
    assert indexed == len(active) == await rt.store.count()
    assert await rt.store.points() == len(active) + sum(_extra_points(a.origin) for a in active)
    # Сверка /api/health: статьи индекса сходятся со статьями базы, точек больше
    health = client.get("/api/health").json()["checks"]
    assert int(health["vector_index"]) == len(active)
    assert int(health["vector_points"]) > len(active)


@pytest.mark.asyncio
async def test_import_keeps_phrasings_and_reimport_drops_their_points(client, monkeypatch):
    """Вопросы к фрагментам ложатся в происхождение статьи, а переимпорт того же
    документа убирает из индекса и их точки, не только основную."""
    rt = client.app.state.rt

    async def questions(_dyn, passages):
        # doc2query зовёт модель; в тесте вопросы — детерминированные, по два на фрагмент
        for passage in passages:
            passage.extra["questions"] = [passage.title, f"Иначе сказанный {passage.title.lower()}"]
        return len(passages)

    monkeypatch.setattr(rag_ingest, "attach", questions)

    def version(tag: str) -> bytes:
        rows = [{"вопрос": f"Вопрос {n} выгрузки", "ответ": f"Ответ {n}, редакция {tag}"} for n in range(2)]
        return json.dumps(rows, ensure_ascii=False).encode("utf-8")

    title = "Выгрузка с формулировками"
    articles, points = await rt.store.count(), await rt.store.points()
    await rag_ingest.ingest_bytes(rt, "faq.json", version("1"), rag_ingest.IngestOptions(title=title))
    async with rt.db.session_factory() as db:
        stored = (
            await db.execute(select(KBArticle).where(KBArticle.question == "Вопрос 0 выгрузки"))
        ).scalar_one()
    assert stored.origin["questions"] == ["Вопрос 0 выгрузки", "Иначе сказанный вопрос 0 выгрузки"]
    assert await rt.store.points() == points + 2 * 3

    await rag_ingest.ingest_bytes(rt, "faq.json", version("2"), rag_ingest.IngestOptions(title=title))

    assert await rt.store.count() == articles + 2
    assert await rt.store.points() == points + 2 * 3, "точки формулировок прошлой версии должны уйти"
