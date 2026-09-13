"""Поиск: hash-эмбеддер, BM25, MemoryStore, гибридное слияние RRF."""

import asyncio
import math
import time
from datetime import UTC, datetime
from types import SimpleNamespace

from app.adapters.embeddings import HashEmbedder, TEIEmbedder
from app.adapters.vectorstore import MemoryStore, VectorDoc
from app.dynamic_settings import SETTINGS
from app.services import kbkind
from app.services.retrieval import KeywordIndex, Retriever, index_tokens, tokenize


class FakeDyn:
    def __init__(self, **overrides):
        self.values = {key: spec.default for key, spec in SETTINGS.items()} | overrides

    def get(self, key, default=None):
        return self.values.get(key, default)


ARTICLES = [
    {
        "article_id": "a" * 32,
        "question": "Как зарегистрироваться на портале?",
        "answer": "Регистрация через ЕСИА и Госуслуги.",
        "category": "Регистрация",
        "line": 1,
        "keywords": ["регистрация", "есиа"],
    },
    {
        "article_id": "b" * 32,
        "question": "Портал не видит электронную подпись",
        "answer": "Проверьте КриптоПро и плагин браузера.",
        "category": "ЭП",
        "line": 2,
        "keywords": ["эцп", "криптопро"],
    },
    {
        "article_id": "c" * 32,
        "question": "Что такое котировочная сессия?",
        "answer": "Мини-аукцион на понижение цены за 24 часа.",
        "category": "Сессии",
        "line": 3,
        "keywords": ["сессия", "аукцион"],
    },
]


def test_tokenize_stems_russian():
    assert tokenize("Регистрация регистрацию регистрации") == ["регист"] * 3


async def test_hash_embedder_deterministic():
    emb = HashEmbedder()
    [v1] = await emb.embed(["котировочная сессия"])
    [v2] = await emb.embed(["котировочная сессия"])
    assert v1 == v2
    assert len(v1) == emb.dim
    assert emb.semantic is False


def test_bm25_ranks_relevant_first():
    index = KeywordIndex()
    index.build(ARTICLES)
    results = index.search("не работает электронная подпись", top_k=3)
    assert results
    assert results[0][0] == "b" * 32
    assert 0 < results[0][2] <= 1


async def test_memory_store_lifecycle(tmp_path):
    emb = HashEmbedder()
    store = MemoryStore(tmp_path)
    vecs = await emb.embed([a["question"] for a in ARTICLES])
    docs = [
        VectorDoc(a["article_id"], v, {"question": a["question"]})
        for a, v in zip(ARTICLES, vecs, strict=True)
    ]
    await store.reindex(docs)
    assert await store.count() == 3

    [q] = await emb.embed(["котировочная сессия что это"])
    hits = await store.search(q, top_k=2)
    assert hits[0].id == "c" * 32

    await store.delete(["c" * 32])
    assert await store.count() == 2

    # персистентность: новый инстанс читает индекс с диска
    store2 = MemoryStore(tmp_path)
    assert await store2.count() == 2


async def test_hybrid_retriever_rrf(tmp_path):
    emb = HashEmbedder()
    store = MemoryStore(tmp_path)
    retriever = Retriever(emb, store, FakeDyn())
    vecs = await emb.embed([f"{a['question']} {a['answer']}" for a in ARTICLES])
    await store.reindex(
        [
            VectorDoc(
                a["article_id"],
                v,
                {
                    "question": a["question"],
                    "answer": a["answer"],
                    "category": a["category"],
                    "line": a["line"],
                },
            )
            for a, v in zip(ARTICLES, vecs, strict=True)
        ]
    )
    retriever.kw_index.build(ARTICLES)
    retriever._payloads = {a["article_id"]: a for a in ARTICLES}

    docs = await retriever.search("портал не видит подпись ЭЦП")
    assert docs[0].article_id == "b" * 32
    assert 0 < docs[0].score <= 1
    assert docs[0].kw_score > 0  # сработали обе ветки гибрида
    assert docs[0].vec_score > 0


async def test_fresh_article_supersedes_old_one_with_same_question(tmp_path):
    """Новая методичка на старый вопрос: свежая статья идёт первой, а старая
    с тем же вопросом из выдачи уходит — иначе модель получала бы два
    противоречащих текста и смешивала их."""
    old = {
        "article_id": "d" * 32,
        "question": "Какая подпись нужна для работы на портале?",
        "answer": "Только УКЭП из удостоверяющего центра.",
        "category": "ЭП",
        "line": 1,
        "keywords": ["укэп"],
        "updated_at": "2025-01-10T00:00:00",
    }
    new = {
        "article_id": "e" * 32,
        "question": "Какая подпись нужна для работы на портале",
        "answer": "С 2026 года достаточно УНЭП из Госключа.",
        "category": "Регламент 2026",
        "line": 1,
        "keywords": ["унэп"],
        "updated_at": "2026-09-01T00:00:00",
    }
    articles = [old, new, *ARTICLES]
    emb = HashEmbedder()
    store = MemoryStore(tmp_path)
    retriever = Retriever(emb, store, FakeDyn())
    vecs = await emb.embed([f"{a['question']} {a['answer']}" for a in articles])
    await store.reindex([VectorDoc(a["article_id"], v, {}) for a, v in zip(articles, vecs, strict=True)])
    retriever.kw_index.build(articles)
    retriever._payloads = {a["article_id"]: a for a in articles}

    docs = await retriever.search("какая подпись нужна для работы на портале")
    ids = [d.article_id for d in docs]
    assert ids[0] == "e" * 32
    assert "d" * 32 not in ids  # старая версия того же вопроса схлопнута
    assert docs[0].answer.startswith("С 2026")


async def test_duplicate_questions_do_not_eat_candidate_slots(tmp_path):
    """Импорт методичек оставил в базе десятки строк с одним заголовком
    («СОДЕРЖАНИЕ» ×38, «Таблица 1 - Перечень терминов и сокращений» ×22). Все
    копии совпадают с запросом одинаково, и при отсечке кандидатов они занимали
    все слоты, а статья, которая отвечает, до скоринга не доходила вовсе."""
    copies = [
        {
            "article_id": f"{i:032d}",
            "question": "Таблица 1 - Перечень терминов и сокращений",
            "answer": "УПД — универсальный передаточный документ.",
            "category": "Термины",
            "line": 1,
            "keywords": [],
            "updated_at": f"2026-01-{i + 1:02d}T00:00:00",
        }
        for i in range(20)
    ]
    answer = {
        "article_id": "f" * 32,
        "question": "Где можно скачать XML-версию УПД?",
        "answer": "XML-версия УПД доступна на странице исполнения контракта.",
        "category": "Документы",
        "line": 1,
        "keywords": ["упд", "xml"],
    }
    retriever = _loaded(tmp_path, [*copies, answer], **{"retrieval.candidates": 3})

    docs = await retriever.search("как выгрузить упд")

    ids = [d.article_id for d in docs]
    assert "f" * 32 in ids
    # Копии стоят одного слота на всех, а не по слоту каждая
    assert len([i for i in ids if i != "f" * 32]) == 1


def test_fresh_wins_only_ties():
    """Дата решает ничью, а не переставляет статьи с разной уверенностью."""
    from app.services.retrieval import RetrievedDoc, prefer_fresh_on_ties

    strong_old = RetrievedDoc("1", "а", "", "к", 1, score=0.90, updated_at="2024-01-01")
    weak_new = RetrievedDoc("2", "б", "", "к", 1, score=0.70, updated_at="2026-01-01")
    tie_old = RetrievedDoc("3", "в", "", "к", 1, score=0.695, updated_at="2024-01-01")
    tie_new = RetrievedDoc("4", "г", "", "к", 1, score=0.69, updated_at="2026-01-01")
    assert [d.article_id for d in prefer_fresh_on_ties([strong_old, weak_new])] == ["1", "2"]
    assert [d.article_id for d in prefer_fresh_on_ties([tie_old, tie_new])] == ["4", "3"]


async def test_answer_words_alone_do_not_make_confidence(tmp_path):
    """Все слова вопроса нашлись в тексте ответа, но ни одно — в вопросе статьи.

    Раньше такая статья получала уверенность 100 %: доля совпавших слов считалась
    по статье целиком. Совпадение только с текстом ответа — не повод отвечать.
    """
    articles = [
        {
            "article_id": "e" * 32,
            "question": "Справка об опыте исполнения",
            "answer": "Документ подписан электронной подписью: контракты собираются автоматически.",
            "category": "Документы",
            "line": 1,
            "keywords": [],
        },
        *ARTICLES,
    ]
    emb = HashEmbedder()
    retriever = Retriever(emb, MemoryStore(tmp_path), FakeDyn())
    retriever.kw_index.build(articles)
    retriever._payloads = {a["article_id"]: a for a in articles}

    docs = await retriever.search("подписать контракт электронной подписью")

    experience = next(d for d in docs if d.article_id == "e" * 32)
    assert experience.kw_score == 1.0  # все слова вопроса действительно есть в статье
    assert experience.score < SETTINGS["agent.confidence_threshold"].default


QUESTION = "Как зарегистрироваться на портале?"


class _BrokenEmbedder:
    """Сервис эмбеддингов лёг: любой запрос к нему — исключение."""

    semantic = True
    dim = 8
    version = "broken"

    async def embed(self, texts):
        raise RuntimeError("TEI недоступен")


class _ScriptedReranker(TEIEmbedder):
    """TEI без сети: векторы даёт хеш-эмбеддер, логиты реранкера заданы по вопросу
    статьи. None вместо логитов — реранкер промолчал (профиль rerank не поднят)."""

    def __init__(self, logits: dict[str, float] | None) -> None:
        # HTTP-клиент родителя не заводим: сеть в этих тестах не трогается
        self._hash = HashEmbedder()
        self.dim = self._hash.dim
        self.version = "tei@scripted"
        self.logits = logits
        self.seen: list[str] = []

    async def embed(self, texts):
        return await self._hash.embed(texts)

    async def rerank(self, query, texts):
        self.seen = texts
        if self.logits is None:
            return None
        return [next(v for q, v in self.logits.items() if text.startswith(q)) for text in texts]


def _lexical_retriever(tmp_path, embedder, **dyn) -> Retriever:
    retriever = Retriever(embedder, MemoryStore(tmp_path), FakeDyn(**dyn))
    retriever.kw_index.build(ARTICLES)
    retriever._payloads = {a["article_id"]: a for a in ARTICLES}
    return retriever


def _logits(a: float, b: float, c: float) -> dict[str, float]:
    return dict(zip((article["question"] for article in ARTICLES), (a, b, c), strict=True))


async def test_search_falls_back_to_words_when_embedder_is_down(tmp_path):
    """Лёг сервис эмбеддингов — поиск не падает, а отвечает по словам."""
    docs = await _lexical_retriever(tmp_path, _BrokenEmbedder()).search(QUESTION)

    assert docs[0].article_id == "a" * 32
    assert all(d.vec_score == 0.0 for d in docs)


async def test_reranker_orders_but_keeps_confidence(tmp_path):
    """Профиль rerank: порядок задаёт сигмоида логита cross-encoder'а, даже если по
    словам первой была другая статья. А уверенность, которую сравнивают с порогом
    отказа, остаётся прежней: у реранкера своя шкала, и правильная статья на вопрос
    с опечаткой получает у него 0.17 — с порогом 0.40 это был бы отказ."""
    plain = await _lexical_retriever(tmp_path, _ScriptedReranker(None)).search(QUESTION)
    reranker = _ScriptedReranker(_logits(-3.0, 2.5, 0.5))
    retriever = _lexical_retriever(tmp_path, reranker, **{"retrieval.use_reranker": True})

    docs = await retriever.search(QUESTION)

    assert sorted(reranker.seen) == sorted(f"{d.question}\n{d.answer}" for d in docs)
    for d in docs:
        assert d.rerank_score == round(1 / (1 + math.exp(-reranker.logits[d.question])), 4)
    assert docs[0].article_id == "b" * 32
    assert [d.rank_score for d in docs] == sorted((d.rank_score for d in docs), reverse=True)
    assert {d.article_id: d.score for d in docs} == {d.article_id: d.score for d in plain}


async def test_tei_reranker_asks_for_raw_logits():
    """TEI по умолчанию отдаёт вероятности, а сигмоиду поиск применяет сам. Без
    raw_scores вторая сигмоида сжимала любую оценку в 0.50–0.73."""
    import json

    import httpx

    sent: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        sent.update(json.loads(request.content))
        return httpx.Response(200, json=[{"index": 1, "score": 2.0}, {"index": 0, "score": -1.5}])

    tei = TEIEmbedder("http://tei", rerank_url="http://tei-rerank")
    tei._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    assert await tei.rerank("вопрос", ["первая", "вторая"]) == [-1.5, 2.0]
    assert sent["raw_scores"] is True


async def test_reranker_silence_keeps_lexical_order(tmp_path):
    """Реранкер не ответил — выдача и оценки те же, что без него."""
    plain = await _lexical_retriever(tmp_path, _ScriptedReranker(None)).search(QUESTION)
    silent = _ScriptedReranker(None)

    docs = await _lexical_retriever(tmp_path, silent, **{"retrieval.use_reranker": True}).search(QUESTION)

    assert silent.seen, "реранкер должны были спросить"
    assert [(d.article_id, d.score) for d in docs] == [(d.article_id, d.score) for d in plain]


async def test_page_context_still_counts_after_reranker(tmp_path):
    """Реранкер переписывает оценки целиком, но раздел портала, с которого
    спрашивают, по-прежнему влияет на порядок: при почти равных логитах
    первой встаёт статья, созвучная странице."""
    dyn = {"retrieval.use_reranker": True}
    logits = _logits(1.0, 0.9, -5.0)

    without_page = await _lexical_retriever(tmp_path, _ScriptedReranker(logits), **dyn).search(QUESTION)
    with_page = await _lexical_retriever(tmp_path, _ScriptedReranker(logits), **dyn).search(
        QUESTION, context="Электронная подпись: КриптоПро и плагин браузера"
    )

    assert without_page[0].article_id == "a" * 32
    assert with_page[0].article_id == "b" * 32


# --- вид записи: отвечают только ответы ---

_CASE = {
    "article_id": "f" * 32,
    "question": "Портал не видит электронную подпись у ИП",
    "answer": "На момент проверки сертификат установлен, заявка закрыта.",
    "category": "ЭП",
    "line": 2,
    "keywords": [],
    "kind": kbkind.OPERATOR_CASE,
}
_REFERENCE = {
    "article_id": "9" * 32,
    "question": "Электронная подпись — подтема обращений",
    "answer": "",
    "category": "Справочник тем",
    "line": 1,
    "keywords": [],
    "kind": kbkind.REFERENCE,
}


def _loaded(tmp_path, articles, embedder=None, **dyn) -> Retriever:
    retriever = Retriever(embedder or _BrokenEmbedder(), MemoryStore(tmp_path), FakeDyn(**dyn))
    retriever.load(articles)
    return retriever


async def test_default_search_gives_only_answers(tmp_path):
    """Отчёт о чужой заявке — не ответ новому человеку, справочник тем — тоже."""
    retriever = _loaded(tmp_path, [*ARTICLES, _CASE, _REFERENCE])

    docs = await retriever.search("портал не видит электронную подпись")

    assert docs[0].article_id == "b" * 32
    assert {d.kind for d in docs} == {kbkind.ANSWER}
    assert not {_CASE["article_id"], _REFERENCE["article_id"]} & {d.article_id for d in docs}


async def test_search_cases_gives_only_operator_cases(tmp_path):
    retriever = _loaded(tmp_path, [*ARTICLES, _CASE, _REFERENCE])

    cases = await retriever.search_cases("портал не видит электронную подпись")

    assert [d.article_id for d in cases] == [_CASE["article_id"]]
    assert cases[0].kind == kbkind.OPERATOR_CASE


def test_suggestions_skip_cases_and_reference(tmp_path):
    """Подсказки автодополнения — вопросы, на которые бот отвечает, а не сырые обращения."""
    retriever = _loaded(tmp_path, [*ARTICLES, _CASE, _REFERENCE])

    assert sorted(retriever.all_questions()) == sorted(a["question"] for a in ARTICLES)


def test_speller_learns_only_from_answers(tmp_path):
    """Словарь опечаток — из ответов: в обращениях свои опечатки, и «сертефикат»
    из отчёта оператора признавался бы правильным словом."""
    case = {**_CASE, "question": "не устанавливается сертефикат", "answer": "сертефикат переустановлен"}
    retriever = _loaded(tmp_path, [*ARTICLES, case])

    assert not retriever.speller.known("сертефикат")
    assert retriever.speller.known("криптопро")


# --- нормализация запроса ---


class _SpyEmbedder(HashEmbedder):
    """Хеш-эмбеддер, который запоминает, какой текст у него попросили."""

    def __init__(self) -> None:
        super().__init__()
        self.texts: list[str] = []

    async def embed(self, texts):
        self.texts.extend(texts)
        return await super().embed(texts)


TYPO_QUESTION = "как зарегестрироватся на партале"


async def test_typos_are_fixed_before_search(tmp_path):
    spy = _SpyEmbedder()
    retriever = _loaded(tmp_path, ARTICLES, spy)

    docs = await retriever.search(TYPO_QUESTION)

    assert spy.texts == ["как зарегистрироваться на портале"]
    assert docs[0].article_id == "a" * 32
    assert docs[0].title_score == 1.0  # после исправления вопрос совпал с вопросом статьи


async def test_disabled_normalization_keeps_the_query(tmp_path):
    """Выключатель в админке: запрос уходит в поиск ровно как написан."""
    spy = _SpyEmbedder()
    retriever = _loaded(tmp_path, ARTICLES, spy, **{"retrieval.query_normalize": False})

    docs = await retriever.search(TYPO_QUESTION + " и ЭП")
    normalized = await _loaded(tmp_path, ARTICLES, _SpyEmbedder()).search(TYPO_QUESTION + " и ЭП")

    assert spy.texts == [TYPO_QUESTION + " и ЭП"]
    # С исправлением статья о регистрации первая, без него вопрос с опечатками её не узнаёт
    assert normalized[0].article_id == "a" * 32
    assert "a" * 32 not in {d.article_id for d in docs}


async def test_vectors_get_no_expansion(tmp_path):
    """Векторному поиску расшифровка сокращения НЕ дописывается.

    Замер на 324 реальных обращениях СТП: дописанные «стандартная товарная
    единица», «универсальный передаточный документ» тянули вектор запроса к
    статьям-определениям («когда одобрят СТЕ» → «Что такое СТЕ?»), и попаданий
    первым было 10.8 % против 13.6 % без них."""
    spy = _SpyEmbedder()
    await _loaded(tmp_path, ARTICLES, spy).search("не видит ЭП")

    assert spy.texts == ["не видит ЭП"]


# --- сокращения: одно понятие в обе стороны ---

_SIGNATURE_BY_ABBREVIATION = {
    "article_id": "1" * 32,
    "question": "Где получить ЭП для работы на портале",
    "answer": "В аккредитованном удостоверяющем центре.",
    "category": "Подпись",
    "line": 1,
    "keywords": [],
}
_CABINET_IN_FULL = {
    "article_id": "2" * 32,
    "question": "Не открывается личный кабинет",
    "answer": "Очистите кэш браузера и войдите заново.",
    "category": "Вход",
    "line": 1,
    "keywords": [],
}


async def test_abbreviation_finds_full_form_and_back(tmp_path):
    """«ЭП» в вопросе встречает «электронную подпись» статьи, а «личный кабинет»
    в вопросе — «ЛК» статьи. Раньше двухбуквенные сокращения в индекс не
    попадали вовсе, и такие вопросы оставались без ответа."""
    articles = [*ARTICLES, _SIGNATURE_BY_ABBREVIATION, _CABINET_IN_FULL]
    retriever = _loaded(tmp_path, articles)

    by_abbreviation = await retriever.search("портал не видит ЭП")
    in_full = await retriever.search("где получить электронную подпись")
    cabinet = await retriever.search("не открывается ЛК")

    assert by_abbreviation[0].article_id == "b" * 32
    assert in_full[0].article_id == "1" * 32
    assert cabinet[0].article_id == "2" * 32
    assert cabinet[0].title_score > 0


def test_short_questions_are_not_all_the_same_question():
    """«Что означает ЭП?» — вопросительная обвязка плюс двухбуквенное сокращение:
    после отсева токенов не остаётся. С пустым ключом все определения коротких
    сокращений считались копиями друг друга и делили один слот кандидата."""
    from app.services.retrieval import _same_question_key

    assert _same_question_key("Что означает ЭП?") != _same_question_key("Что означает ЛК?")
    # Настоящие копии по-прежнему сходятся: регистр и пунктуация ключ не меняют
    assert _same_question_key("Выгрузка оферт") == _same_question_key("выгрузка оферт?")
    assert _same_question_key("Что означает ЭП?") == _same_question_key("что означает эп")


def test_question_word_for_word_matches_fully(tmp_path):
    """«Что такое ЭП» против «Что означает ЭП?»: после отсева вопросительной
    обвязки с обеих сторон остаётся одно понятие «эп». Оно есть в тысячах статей
    про электронную подпись, idf у него почти нулевой, и взвешенная доля
    обнуляла единственное совпавшее слово — статья-определение не попадала в
    выдачу вовсе."""
    index = KeywordIndex()
    index.build(ARTICLES)

    query, title = set(index_tokens("что такое эп")), set(index_tokens("Что означает ЭП?"))

    assert query == title == {"эп"}
    assert index.title_match(query, title) == 1.0
    # Разные вопросы полного совпадения не получают
    assert index.title_match(query, set(index_tokens("Что означает ЕЭТП?"))) < 1.0


def test_abbreviation_is_one_token():
    """Понятие весит как одно слово, как бы его ни записали."""
    assert index_tokens("УПД") == ["упд"]
    assert index_tokens("ЭП и ЭЦП") == ["эп", "эп"]
    full = index_tokens("универсальный передаточный документ")
    assert full.count("упд") == 1
    assert index_tokens("Регистрация на портале") == tokenize("Регистрация на портале")


def test_download_synonyms_share_a_lexical_token():
    assert "_download" in index_tokens("как выгрузить упд")
    assert "_download" in index_tokens("хочу скачать упд с портала")
    assert "_download" in index_tokens("упд выгрузка")


def test_download_synonym_replaces_the_word_it_stands_for():
    """Ключ синонима не приписывается рядом со словом, а вытесняет его. Иначе
    статья, написавшая то же слово, что и спрашивающий, получает и слово, и ключ —
    вдвое больше перефразировки, — и «9.1.1 Выгрузка оферт» (выгруз 4.28 +
    _download 3.41) обходит «Где можно скачать XML-версию УПД?» (3.41 + упд 1.51)
    на запрос «как выгрузить УПД»."""
    tokens = index_tokens("выгрузить скачать скачивание экспорт упд")
    assert [t for t in tokens if t != "_download"] == ["упд"]

    # Любая формулировка даёт один и тот же набор токенов — конец выдаче «через раз»
    variants = ["как выгрузить упд", "как скачать упд", "экспортировать упд", "выгрузка упд"]
    assert {frozenset(index_tokens(v)) for v in variants} == {frozenset({"упд", "_download"})}


def test_abbreviation_expansion_still_keeps_its_words():
    """Вытеснение — только для синонимов действия: у сокращения полная форма это
    то же слово в другом падеже, и «подпись» без «электронной» должна по-прежнему
    находить статью про ЭП."""
    tokens = index_tokens("электронная подпись не работает")
    assert "электр" in tokens and "подпис" in tokens and "эп" in tokens


def test_download_synonym_matches_title_written_with_a_different_word():
    """Живой баг с прода: запрос «выгрузить УПД» терял статью «Где можно скачать
    XML-версию УПД?» из топ-5 целиком — «выгрузить» и «скачать» не пересекались
    ни одним токеном, пока статья про совсем не связанную с УПД «Выгрузку оферт»
    выигрывала по kw-score одним лишь совпадением слова. Синоним должен поднимать
    совпадение вопроса «выгрузить» с заголовком «скачать», а не только не мешать."""
    title = "Где можно скачать XML-версию УПД?"
    article = {
        "article_id": "d" * 32,
        "question": title,
        "answer": "Скачать XML файл можно на странице исполнения контракта.",
        "category": "Документы",
        "line": 4,
        "keywords": ["упд", "xml"],
    }

    index = KeywordIndex()
    index.build([*ARTICLES, article])
    without_synonyms = index.title_match(set(tokenize("как выгрузить упд")), set(tokenize(title)))
    with_synonyms = index.title_match(set(index_tokens("как выгрузить упд")), set(index_tokens(title)))
    assert with_synonyms > without_synonyms


async def test_abbreviation_does_not_inflate_confidence(tmp_path):
    """«Заказчик не принял наш УПД» и статья «Как принять УПД»: общего у них одно
    сокращение. Расшифровка, дописанная словами и к вопросу, и к статье,
    превращала его в четыре совпавших слова, и на золотом наборе бот уверенно
    отвечал инструкцией для заказчика на вопрос поставщика. Уверенность должна
    быть той же, что без всякой нормализации."""
    accept = {
        "article_id": "3" * 32,
        "question": "Как принять УПД от поставщика",
        "answer": "Откройте карточку контракта и нажмите «Принять».",
        "category": "Исполнение",
        "line": 1,
        "keywords": [],
    }
    question = "заказчик не принял наш УПД"
    normalized = await _loaded(tmp_path, [*ARTICLES, accept]).search(question)
    plain = await _loaded(tmp_path, [*ARTICLES, accept], **{"retrieval.query_normalize": False}).search(
        question
    )

    assert normalized[0].score == plain[0].score
    assert normalized[0].score < SETTINGS["agent.confidence_threshold"].default


def test_concept_weighs_the_same_however_written():
    """«КС» против «Что такое КС?» и против «Что такое котировочная сессия?» —
    одно понятие, и весит оно одинаково. Раньше сторона с сокращением получала
    слова полной формы, и вопрос с «КС» совпадал с полной формой вдвое сильнее,
    чем с тем же сокращением."""
    index = KeywordIndex()
    index.build([*ARTICLES, {**_SIGNATURE_BY_ABBREVIATION, "question": "Что такое КС?"}])
    question = set(index_tokens("в КС указаны не корректные размеры"))

    against_abbreviation = index.title_match(question, set(index_tokens("Что такое КС?")))
    against_full = index.title_match(question, set(index_tokens("Что такое котировочная сессия?")))

    assert against_full == against_abbreviation
    # Полная форма против полной формы — не слабее сокращения против полной формы
    in_full = set(index_tokens("в котировочной сессии указаны не корректные размеры"))
    assert index.title_match(in_full, set(index_tokens("Что такое котировочная сессия?"))) >= against_full


async def test_shared_concept_alone_is_not_an_answer(tmp_path):
    """Одно общее понятие — ещё не ответ: «в КС указаны не корректные размеры»
    уверенно получал «Что такое котировочная сессия?»."""
    docs = await _loaded(tmp_path, ARTICLES).search("в КС указаны не корректные размеры.")

    assert all(d.score < SETTINGS["agent.confidence_threshold"].default for d in docs)


async def test_cases_are_found_without_reranker(tmp_path):
    """Случаи сравнивают с порогом по уверенности — реранкер им только лишняя задержка."""
    retriever = _loaded(tmp_path, [*ARTICLES, _CASE], **{"retrieval.use_reranker": True})
    calls: list[str] = []

    async def spy(query, ordered):
        calls.append(query)
        return False

    retriever._rerank = spy  # type: ignore[method-assign]
    await retriever.search_cases("портал не видит электронную подпись")
    assert calls == []
    await retriever.search("портал не видит электронную подпись")
    assert len(calls) == 1


class _Rows:
    def __init__(self, rows):
        self.rows = rows

    def scalars(self):
        return self

    def all(self):
        return self.rows


class _Session:
    def __init__(self, rows):
        self.rows = rows

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, _statement):
        return _Rows(self.rows)


def _row(article_id: str, question: str):
    return SimpleNamespace(
        id=article_id,
        question=question,
        answer="текст статьи",
        category="к",
        line=1,
        keywords=[],
        audience="all",
        updated_at=datetime.now(UTC),
        origin={},
    )


async def test_stale_refresh_does_not_overwrite_newer_one(tmp_path):
    """Две переиндексации подряд: админ опубликовал импорт и тут же снял статью.
    Снимок, прочитанный раньше, но собранный дольше, не должен перетереть новый —
    иначе снятая статья живёт в поиске до следующего изменения базы."""
    before = [_row("a", "Как отменить оферту"), _row("x", "Снятая статья про выписку")]
    after = before[:1]
    snapshots = iter([before, after])
    retriever = Retriever(_BrokenEmbedder(), MemoryStore(tmp_path), FakeDyn())
    original = Retriever._prepare

    def slow_prepare(articles):
        time.sleep(0.3 if len(articles) == len(before) else 0.01)
        return original(articles)

    retriever._prepare = slow_prepare  # type: ignore[method-assign]
    first = asyncio.create_task(retriever.refresh_from_db(lambda: _Session(next(snapshots))))
    await asyncio.sleep(0.01)
    await asyncio.gather(first, retriever.refresh_from_db(lambda: _Session(next(snapshots))))

    assert sorted(retriever._payloads) == ["a"]
    assert retriever.kw_index.search("выписка снятая", 5) == []
