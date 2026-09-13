"""Гибридный поиск по фрагментам: смысловой (BERTA) + лексический BM25 по леммам, слияние RRF.

Сокращения из терминов самих документов раскрываются в запросе: «что такое УПД»
ищется и как «универсальный передаточный документ». Вопрос-определение получает
первым фрагмент термина. Роль собеседника отсекает инструкции другой стороны.
"""

import asyncio
import difflib
import heapq
import math
import re
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from sqlalchemy import select

from app.models import KBArticle
from app.rag.index import prefixes
from app.rag.passages import KIND_GLOSSARY, KIND_TERM, KIND_TICKET
from app.rag.text import is_known, lemmas, warm, words
from app.services import audience as audience_of
from app.services import kbkind
from app.services.retrieval import RetrievedDoc

if TYPE_CHECKING:
    from app.adapters.embeddings import Embedder
    from app.adapters.vectorstore import VectorStore
    from app.db import SessionFactory
    from app.dynamic_settings import DynamicSettings

KB_CHANNEL = "kb:changed"
DEFAULT_TOP_K = 6
CANDIDATES = 40
RRF_K = 60
DENSE_WEIGHT = 1.0
LEXICAL_WEIGHT = 0.5  # подобран перебором на эталоне (app/rag/evaluate.py --grid)
TITLE_BOOST = 2
PER_SECTION = 2
TERM_BONUS = 1.0
GLOSSARY_BONUS = 0.5
# Решения техподдержки — дополнение к инструкциям, а не замена: их короткие
# формулировки близки к любому короткому вопросу и вытеснили бы разделы методичек.
MAX_TICKETS = 3  # компромисс по эталону: 2 — лучше инструкциям, 6 — реальным вопросам из СТП
MIN_LOWER_ABBR = 3
RERANK_CANDIDATES = 20
RERANK_CHARS = 1200
RERANK_WEIGHT = 0.5
MIN_TYPO_LEN = 5
MAX_TYPO_LEN_DIFF = 2
TYPO_CUTOFF = 0.8
MIN_PHRASE_LEMMAS = 2
MAX_PHRASE_LEMMAS = 4
MIN_AUDIENCE_TITLE_COVERAGE = 0.5
_WORD = re.compile(r"[A-Za-zА-Яа-яЁё0-9]+")
_DEFINITION = re.compile(
    r"^\s*(?:а\s+)?(?:что\s+(?:такое|значит|означает|за)|расшифр\w*|как\s+расшифровывается|определение)\b",
    re.IGNORECASE,
)


class BM25:
    K1 = 1.2
    B = 0.75

    def __init__(self) -> None:
        self.ids: list[str] = []
        self.lengths: list[int] = []
        self.postings: dict[str, list[tuple[int, int]]] = {}
        self.avg = 1.0

    def build(self, docs: Iterable[tuple[str, list[str]]]) -> None:
        for number, (doc_id, tokens) in enumerate(docs):
            self.ids.append(doc_id)
            self.lengths.append(len(tokens))
            for token, tf in Counter(tokens).items():
                self.postings.setdefault(token, []).append((number, tf))
        self.avg = sum(self.lengths) / len(self.lengths) if self.lengths else 1.0

    def search(self, tokens: list[str], limit: int) -> list[tuple[str, float]]:
        n = len(self.ids)
        scores: dict[int, float] = {}
        for token in set(tokens):
            postings = self.postings.get(token)
            if not postings:
                continue
            idf = math.log(1 + (n - len(postings) + 0.5) / (len(postings) + 0.5))
            for number, tf in postings:
                norm = tf + self.K1 * (1 - self.B + self.B * self.lengths[number] / self.avg)
                scores[number] = scores.get(number, 0.0) + idf * tf * (self.K1 + 1) / norm
        best = heapq.nlargest(limit, scores.items(), key=lambda kv: kv[1])
        return [(self.ids[number], score) for number, score in best]

    def idf(self, token: str) -> float:
        n = len(self.ids)
        df = len(self.postings.get(token, ()))
        return math.log(1 + (n - df + 0.5) / (df + 0.5))


def _record(row: KBArticle) -> dict[str, Any]:
    origin = row.origin or {}
    return {
        "article_id": row.id,
        "question": row.question,
        "answer": row.answer,
        "category": row.category,
        "line": row.line,
        "audience": audience_of.normalize(row.audience),
        "updated_at": row.updated_at.isoformat() if row.updated_at else "",
        "kind": origin.get("kind", ""),
        "examples": origin.get("examples") or [],
        "questions": origin.get("questions") or [],
        "abbr": origin.get("abbr", ""),
        "definition": origin.get("definition", ""),
    }


class _Snapshot:
    def __init__(self, records: list[dict[str, Any]]) -> None:
        self.records = {r["article_id"]: r for r in records}
        self.bm25 = BM25()
        self.bm25.build(
            (
                r["article_id"],
                lemmas(
                    " ".join(
                        [r["question"]] * TITLE_BOOST
                        + [r["category"], r["answer"], *r["examples"], *r["questions"]]
                    )
                ),
            )
            for r in records
        )
        # Вопросы фрагмента леммами — для уверенности без смыслового эмбеддера (lite-режим)
        self.titles = {
            r["article_id"]: [frozenset(lemmas(q)) for q in (r["question"], *r["examples"], *r["questions"])]
            for r in records
        }
        self.terms: dict[str, tuple[str, str]] = {}
        for r in records:
            if r["kind"] == KIND_TERM and r["abbr"]:
                self.terms.setdefault(r["abbr"].upper(), (r["definition"], r["article_id"]))
        # Словарь базы для исправления опечаток: слово → сколько раз встречается
        self.vocab: Counter[str] = Counter(
            w
            for r in records
            for w in words(" ".join([r["question"], r["answer"], *r["examples"], *r["questions"]]))
            if w.isalpha()
        )
        self.by_first: dict[str, list[str]] = {}
        for word in self.vocab:
            if len(word) >= MIN_TYPO_LEN:
                self.by_first.setdefault(word[0], []).append(word)
        # Таблицы терминов документов: на вопрос-определение поднимается та, где есть предмет вопроса
        self.glossary = {
            r["article_id"]: frozenset(lemmas(r["answer"])) for r in records if r["kind"] == KIND_GLOSSARY
        }
        # Полное название термина → сокращение: «котировочная сессия» узнаётся как КС
        self.phrases: list[tuple[frozenset[str], str]] = []
        for abbr, (definition, _) in self.terms.items():
            phrase = frozenset(lemmas(definition))
            if MIN_PHRASE_LEMMAS <= len(phrase) <= MAX_PHRASE_LEMMAS:
                self.phrases.append((phrase, abbr))


def _title_match(snap: _Snapshot, asked: frozenset[str], article_id: str) -> float:
    """Насколько вопрос фрагмента (или один из его вопросов) — это вопрос собеседника.

    idf-взвешенная F1 в обе стороны: полнота без точности «покрывает» короткий вопрос
    любым длинным заголовком, точность без полноты — длинный заголовок одним общим словом.
    """
    best = 0.0
    for title in snap.titles.get(article_id, ()):
        if not asked or not title:
            continue
        if asked == title:
            return 1.0
        common = sum(snap.bm25.idf(t) for t in asked & title)
        if not common:
            continue
        recall = common / sum(snap.bm25.idf(t) for t in asked)
        precision = common / sum(snap.bm25.idf(t) for t in title)
        best = max(best, 2 * recall * precision / (recall + precision))
    return best


@dataclass
class _Scores:
    """Оценки кандидатов: слияние выдач (порядок) и смысловая близость (уверенность)."""

    fused: dict[str, float] = field(default_factory=dict)
    similarity: dict[str, float] = field(default_factory=dict)


def _same_case(original: str, fixed: str) -> str:
    if fixed == original:
        return original
    if original[:1].isupper():
        return fixed[:1].upper() + fixed[1:]
    return fixed


class RagRetriever:
    def __init__(self, embedder: Embedder, store: VectorStore, dyn: DynamicSettings) -> None:
        self.embedder = embedder
        self.dense_weight, self.lexical_weight, self.rrf_k = DENSE_WEIGHT, LEXICAL_WEIGHT, RRF_K
        self.max_tickets = MAX_TICKETS
        self.store = store
        self.dyn = dyn
        self._snap = _Snapshot([])
        self._generation = 0

    async def refresh_from_db(self, session_factory: SessionFactory) -> int:
        self._generation += 1
        generation = self._generation
        async with session_factory() as session:
            rows = (
                (await session.execute(select(KBArticle).where(KBArticle.status == "active"))).scalars().all()
            )
        # Случаи для оператора и справочники прежнего импорта (kbkind) ответом не бывают:
        # «на момент проверки документ отображается» — отписка по чужой заявке, а не инструкция.
        # Виды конвейера RAG (раздел, термин, решение СТП) kbkind считает ответами.
        records = [_record(r) for r in rows if kbkind.answers_user(kbkind.of(r.origin))]
        snap = await asyncio.to_thread(self._build, records)
        if generation == self._generation:
            self._snap = snap
        return len(records)

    @staticmethod
    def _build(records: list[dict[str, Any]]) -> _Snapshot:
        warm()
        return _Snapshot(records)

    def load(self, articles: list[dict[str, Any]]) -> None:
        records = [
            {
                "kind": "",
                "examples": [],
                "questions": [],
                "abbr": "",
                "definition": "",
                "line": 1,
                "updated_at": "",
                **a,
                "audience": audience_of.normalize(a.get("audience")),
            }
            for a in articles
        ]
        self._snap = _Snapshot(records)

    def all_questions(self) -> list[str]:
        return [r["question"] for r in self._snap.records.values() if r["kind"] != KIND_TERM]

    def correct(self, query: str) -> str:
        """Опечатки — по словарю самой базы: «зарегенстриваться» → «зарегистрироваться».

        Правится только слово, которого нет ни в базе, ни в словаре языка: правильные
        слова, сокращения (ЭДО, УПД) и числа не трогаются. Замена — самое частое в базе
        из близких по написанию слов той же первой буквы.
        """
        snap = self._snap

        def fix(match: re.Match[str]) -> str:
            word = match.group(0)
            low = word.lower().replace("ё", "е")
            if len(low) < MIN_TYPO_LEN or not low.isalpha() or word.isupper() or low in snap.vocab:
                return word
            if is_known(low):
                return word
            pool = [w for w in snap.by_first.get(low[0], ()) if abs(len(w) - len(low)) <= MAX_TYPO_LEN_DIFF]
            close = difflib.get_close_matches(low, pool, n=3, cutoff=TYPO_CUTOFF)
            return max(close, key=lambda w: snap.vocab[w]) if close else word

        return _WORD.sub(fix, query)

    def correct_display(self, text: str) -> str:
        """Та же правка, что у поиска, но с регистром исходного слова: этот текст увидит
        человек в чате. correct отдаёт слово базы в нижнем регистре — поиску всё равно,
        а в пузыре «Регестрация» → «регистрация» выглядело бы ещё одной ошибкой."""
        return _WORD.sub(lambda m: _same_case(m.group(0), self.correct(m.group(0))), text)

    def expand(self, query: str) -> list[tuple[str, str, str]]:
        """Сокращения из запроса, которые определены в терминах документов: (сокращение, расшифровка, id)."""
        found = []
        for word in dict.fromkeys(_WORD.findall(query)):
            key = word.upper().replace("Ё", "Е")
            if key in self._snap.terms and (word.isupper() or len(word) >= MIN_LOWER_ABBR):
                definition, article_id = self._snap.terms[key]
                found.append((key, definition, article_id))
        asked = set(lemmas(query))
        known = {abbr for abbr, _, _ in found}
        for phrase, abbr in self._snap.phrases:
            if abbr not in known and phrase <= asked:
                definition, article_id = self._snap.terms[abbr]
                found.append((abbr, definition, article_id))
                known.add(abbr)
        return found

    async def _dense(self, text: str) -> list[tuple[str, float]]:
        try:
            query_prefix, _ = await prefixes(self.embedder)
            [vector] = await self.embedder.embed([query_prefix + text])
            hits = await self.store.search(vector, CANDIDATES)
        except Exception:  # noqa: BLE001 — без эмбеддера ищем только по словам
            return []
        return [(hit.id, hit.score) for hit in hits]

    async def search(
        self,
        query: str,
        top_k: int | None = None,
        context: str = "",
        audience: str = "",
        kinds: frozenset[str] | None = None,
    ) -> list[RetrievedDoc]:
        snap = self._snap
        allowed = audience_of.allowed(audience)
        query = self.correct(query)
        terms = self.expand(query)
        expansion = "; ".join(f"{abbr} — {definition}" for abbr, definition, _ in terms)
        dense = await self._dense(f"{query} ({expansion})" if expansion else query)
        lexical = lemmas(" ".join([query, *(d for _, d, _ in terms)]))
        sparse = snap.bm25.search(lexical, CANDIDATES) if lexical else []
        scores = _Scores(fused=self._fuse(snap, allowed, dense, sparse), similarity=dict(dense))
        lite = not getattr(self.embedder, "semantic", True)
        if lite:
            # Хэш-эмбеддер lite-режима сравнивает триграммы, а не смысл: его косинус с
            # вопросом, совпавшим слово в слово, — 0.57, и порог ответа не проходил никто,
            # а порядок его выдачи — шум. Уверенность и порядок там — совпадение вопроса
            # с вопросом фрагмента по леммам; слияние выдач решает только ничьи.
            asked = frozenset(lemmas(query))
            scores.similarity = {a: _title_match(snap, asked, a) for a in scores.fused}
        definition = bool(_DEFINITION.search(query))
        if definition:
            self._boost_definition(query, terms, scores, allowed)
        await self._rerank(query, snap, scores.fused, definition=definition)
        order = sorted(scores.fused, key=lambda a: -scores.fused[a])
        if lite:
            order.sort(key=lambda a: -scores.similarity.get(a, 0.0))
        docs = self._select(snap, order, scores, top_k or DEFAULT_TOP_K, definition=definition)
        return self._prefer_audience(docs, audience, query)

    @staticmethod
    def _prefer_audience(docs: list[RetrievedDoc], audience: str, query: str) -> list[RetrievedDoc]:
        """Поднять адресную статью, только если её заголовок точнее отвечает запросу."""
        role = audience_of.normalize(audience)
        if not audience_of.is_known(role) or not docs or docs[0].audience == role:
            return docs
        addressed = next((doc for doc in docs if doc.audience == role), None)
        if addressed is None:
            return docs
        query_words = set(lemmas(query))
        if not query_words:
            return docs

        def coverage(doc: RetrievedDoc) -> float:
            return len(query_words & set(lemmas(doc.question))) / len(query_words)

        if coverage(addressed) < MIN_AUDIENCE_TITLE_COVERAGE or coverage(addressed) <= coverage(docs[0]):
            return docs
        return [addressed, *(doc for doc in docs if doc is not addressed)]

    def _fuse(
        self,
        snap: _Snapshot,
        allowed: frozenset[str],
        dense: list[tuple[str, float]],
        sparse: list[tuple[str, float]],
    ) -> dict[str, float]:
        """RRF смысловой и словарной выдач. Ранг — по выдаче, уже отфильтрованной по роли:
        инструкция другой стороны сделки не занимает место и не сдвигает подходящие."""
        fused: dict[str, float] = {}
        for weight, hits in ((self.dense_weight, dense), (self.lexical_weight, sparse)):
            rank = 0
            for article_id, _ in hits:
                record = snap.records.get(article_id)
                if record is None or record["audience"] not in allowed:
                    continue
                fused[article_id] = fused.get(article_id, 0.0) + weight / (self.rrf_k + rank + 1)
                rank += 1
        return fused

    def _boost_definition(
        self, query: str, terms: list[tuple[str, str, str]], scores: _Scores, allowed: frozenset[str]
    ) -> None:
        """Вопрос «что такое X»: первым — термин X, за ним таблица терминов, где X определён."""
        snap = self._snap
        for _, _, article_id in terms:
            if snap.records[article_id]["audience"] in allowed:
                scores.fused[article_id] = scores.fused.get(article_id, 0.0) + TERM_BONUS
                scores.similarity[article_id] = max(scores.similarity.get(article_id, 0.0), 1.0)
        subject = set(lemmas(_DEFINITION.sub("", query, count=1)))
        for article_id, defined in snap.glossary.items():
            if subject and subject <= defined and snap.records[article_id]["audience"] in allowed:
                scores.fused[article_id] = scores.fused.get(article_id, 0.0) + GLOSSARY_BONUS

    async def _rerank(
        self, query: str, snap: _Snapshot, fused: dict[str, float], *, definition: bool
    ) -> None:
        """Cross-encoder переставляет лучших кандидатов слияния (профиль rerank)."""
        rerank = getattr(self.embedder, "rerank", None)
        if rerank is None or not self.dyn.get("retrieval.use_reranker"):
            return
        pool = [
            article_id
            for article_id in sorted(fused, key=lambda a: -fused[a])
            if definition or snap.records[article_id]["kind"] != KIND_GLOSSARY
        ][:RERANK_CANDIDATES]
        texts = [f"{snap.records[a]['question']}\n{snap.records[a]['answer'][:RERANK_CHARS]}" for a in pool]
        scores = await rerank(query, texts) if pool else None
        for article_id, logit in zip(pool, scores or [], strict=False):
            fused[article_id] += RERANK_WEIGHT / (1 + math.exp(-logit))

    def _select(
        self,
        snap: _Snapshot,
        order: list[str],
        scores: _Scores,
        top_k: int,
        *,
        definition: bool,
    ) -> list[RetrievedDoc]:
        """Лучшие в порядке order: не больше PER_SECTION из раздела, MAX_TICKETS решений СТП,
        один термин на сокращение; таблица терминов — только на вопрос-определение.
        Фрагмент, найденный одними словами, получает уверенность худшего из смысловых."""
        fused, similarity = scores.fused, scores.similarity
        floor = min(similarity.values(), default=0.0)
        chosen: list[RetrievedDoc] = []
        spare: list[RetrievedDoc] = []
        per_section: Counter[str] = Counter()
        seen_terms: set[str] = set()
        tickets = 0
        for article_id in order:
            record = snap.records[article_id]
            kind = record["kind"]
            if kind == KIND_GLOSSARY and not definition:
                continue
            if per_section[record["category"]] >= PER_SECTION or (
                kind == KIND_TERM and record["question"] in seen_terms
            ):
                continue
            doc = self._doc(record, similarity.get(article_id, floor), fused[article_id])
            if kind == KIND_TICKET and tickets >= self.max_tickets:
                spare.append(doc)
                continue
            per_section[record["category"]] += 1
            tickets += kind == KIND_TICKET
            if kind == KIND_TERM:
                seen_terms.add(record["question"])
            chosen.append(doc)
            if len(chosen) >= top_k:
                return chosen
        return [*chosen, *spare][:top_k]

    @staticmethod
    def _doc(record: dict[str, Any], similarity: float, fused: float) -> RetrievedDoc:
        return RetrievedDoc(
            article_id=record["article_id"],
            question=record["question"],
            answer=record["answer"],
            category=record["category"],
            line=int(record.get("line") or 1),
            score=round(similarity, 4),
            vec_score=round(similarity, 4),
            kw_score=round(fused, 5),
            audience=record["audience"],
            updated_at=record.get("updated_at", ""),
            kind=kbkind.ANSWER,
        )

    async def search_cases(self, query: str, top_k: int = 3, audience: str = "") -> list[RetrievedDoc]:
        return []
