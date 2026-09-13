"""Гибридный поиск по базе знаний: векторный (Qdrant/TEI) + лексический BM25,
слияние Reciprocal Rank Fusion, опциональный cross-encoder реранкер (TEI).

Лексический индекс живёт в памяти каждого инстанса (сотни-тысячи статей — это килобайты)
и перечитывается по событию kb:changed из Redis pub/sub.

Запрос перед поиском нормализуется (services/querytext.py): опечатки
исправляются по словарю самой базы. Сокращение и его полная форма в лексическом
индексе — один токен-понятие (index_tokens) и в статье, и в вопросе: тогда «ЭП»
в вопросе встречает «электронную подпись» в статье и наоборот. Векторному поиску
расшифровки не дописываются: на реальных обращениях они тянули запрос к
статьям-определениям (см. Retriever.query_forms).
"""

import asyncio
import heapq
import math
import re
from array import array
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from sqlalchemy import select

from app.adapters.embeddings import Embedder, TEIEmbedder
from app.adapters.vectorstore import Hit, VectorStore
from app.models import KBArticle
from app.services import audience as audience_of
from app.services import kbkind, phrasings, querytext
from app.services.querytext import (
    Speller,
    align_concepts,
    concept_of,
    concept_spans,
    replacing_concepts,
)

if TYPE_CHECKING:
    from app.db import SessionFactory
    from app.dynamic_settings import DynamicSettings

_WORD_RE = re.compile(r"[а-яёa-z0-9]+")
KB_CHANNEL = "kb:changed"

# вежливое обрамление и связки — не несут смысла для поиска
_STOPWORDS = {
    "здравствуйте",
    "привет",
    "добрый",
    "день",
    "утро",
    "вечер",
    "пожалуйста",
    "подскажите",
    "скажите",
    "помогите",
    "спасибо",
    "заранее",
    "прошу",
    "просьба",
    "могу",
    "разобраться",
    "уважаемые",
    "это",
    "как",
    "что",
    "какой",
    "какие",
    "нужно",
    "можно",
    "надо",
    "хочу",
    "меня",
    "мне",
    "него",
    "все",
    "или",
    "если",
    # Обрамление вопроса о значении: «что такое КС», «что означает УПД», «ЭП это
    # что». Смысла в них нет, а вес есть, и вопрос из одного сокращения они
    # разбавляли вдвое: «что такое кс» против заголовка «Что означает КС?»
    # совпадал одним словом из двух с каждой стороны, и статья-определение
    # проигрывала «Что означает КТС?», которую наверх поднимал вектор — короткие
    # сокращения он почти не различает.
    "такое",
    "такой",
    "такие",
    "значит",
    "означает",
    "означают",
    "расшифруй",
    "расшифруйте",
}


_MIN_TOKEN_LEN = 3  # предлоги и союзы короче трёх букв в поиск не идут
_STEM_LEN = 6  # грубый стемминг — префикс в шесть букв

#: Ответы — то, чем отвечает бот. Случаи для оператора и справочник ищутся
#: только по явной просьбе (см. Retriever.search_cases).
ANSWER_KINDS = frozenset({kbkind.ANSWER})


def tokenize(text: str) -> list[str]:
    """Токены с грубым стеммингом (префикс 6 символов покрывает русские флексии)."""
    words = _WORD_RE.findall(text.lower().replace("ё", "е"))
    return [w[:_STEM_LEN] for w in words if len(w) >= _MIN_TOKEN_LEN and w not in _STOPWORDS]


def index_tokens(text: str) -> list[str]:
    """Токены лексического индекса: как tokenize, но сокращение — это понятие.

    «ЭЦП», «ЭП» и «электронной подписью» дают один токен — ключ понятия, так что
    сокращение в вопросе находит полную форму в статье и наоборот, а двухбуквенные
    «ЭП», «КС», «ЛК» вообще впервые попадают в индекс. Слова расшифровки при этом
    остаются: «подпись» без «электронной» по-прежнему находит статью про
    электронную подпись, а при сравнении с заголовком ключ рядом с ними не
    считается второй раз (querytext.align_concepts).

    Синонимы действия (querytext.replacing_concepts) — наоборот: их слова ключ
    вытесняет. Иначе статья, написавшая то же слово, что и спрашивающий, берёт и
    ключ, и своё редкое слово, а перефразировка — один ключ, и буквальное
    совпадение весит вдвое. Так «9.1.1 Выгрузка оферт» (выгруз 4.28 + _download
    3.41 = 7.69) обходила «Где можно скачать XML-версию УПД?» (3.41 + упд 1.51 =
    4.92) на запрос «как выгрузить УПД». После вытеснения все формулировки —
    выгрузить, скачать, экспортировать — дают один и тот же топ.

    Расшифровку словами НЕ дописываем: тогда одно понятие «УПД» весило бы как
    четыре слова, и вопрос «заказчик не принял наш УПД» уверенно получал статью
    «Как принять УПД» за одно только сокращение — на золотом наборе это была
    уверенная ошибка.
    """
    words = _WORD_RE.findall(text.lower().replace("ё", "е"))
    spans = concept_spans(words)
    replacing = replacing_concepts()
    replaced: set[int] = set()
    kept: set[int] = set()
    for key, start, length in spans:
        # Спаны вкладываются друг в друга («усиленная квалифицированная
        # электронная подпись» — сразу УКЭП, КЭП и ЭП). Слово, которое вдобавок
        # входит в понятие-непоглотитель, остаётся: там его роль прежняя.
        (replaced if key in replacing else kept).update(range(start, start + length))
    replaced -= kept
    tokens: list[str] = []
    for i, word in enumerate(words):
        if i in replaced:
            continue
        concept = concept_of(word)
        if concept:
            tokens.append(concept)
        elif len(word) >= _MIN_TOKEN_LEN and word not in _STOPWORDS:
            tokens.append(word[:_STEM_LEN])
    tokens.extend(key for key, _, _ in spans)
    return tokens


@dataclass
class RetrievedDoc:
    article_id: str
    question: str
    answer: str
    category: str
    line: int
    score: float = 0.0  # итоговая уверенность 0..1 (max из компонент / реранкер)
    vec_score: float = 0.0
    kw_score: float = 0.0
    title_score: float = 0.0  # совпадение с вопросом статьи, idf-взвешенная F1 0..1
    audience: str = audience_of.ANY  # кому адресована статья: all | supplier | customer
    # Когда статью обновляли, ISO-строка. Решает спор двух статей с одним
    # вопросом: свежая методичка важнее старой.
    updated_at: str = ""
    # Вероятность cross-encoder'а (профиль rerank), None — реранкер не спрашивали.
    # Живёт отдельно от score: у реранкера своя шкала, и с порогом отказа, который
    # подобран под гибридную уверенность, её сравнивать нельзя.
    rerank_score: float | None = None
    # Чем запись служит поиску: ответ, случай для оператора, справочник (kbkind)
    kind: str = kbkind.ANSWER

    @property
    def rank_score(self) -> float:
        """Оценка, по которой выдача упорядочена: реранкер, а без него — уверенность."""
        return self.score if self.rerank_score is None else self.rerank_score


#: Разница оценок, которую поиск не считает значимой: 0.02 — это шум округления
#: и порядка слов, а не разница в смысле. Внутри неё спор решает дата.
FRESHNESS_TIE = 0.02


def _same_question_key(question: str) -> str:
    """Ключ «это тот же вопрос»: слова вопроса без обрамления и склонений.

    Вопрос, от которого после отсева не остаётся ни одного токена, — это не
    «пустой вопрос», а короткий: «Что означает ЭП?» состоит из вопросительной
    обвязки и двухбуквенного сокращения. С пустым ключом все такие статьи
    оказывались копиями друг друга: слот кандидата доставался одной, а
    определения ЭП, ЛК и ПП в выдачу не попадали вовсе.
    """
    words = _WORD_RE.findall(question.lower().replace("ё", "е"))
    return " ".join(tokenize(question)) or " ".join(words)


def newest_per_question(docs: Iterable[RetrievedDoc]) -> list[RetrievedDoc]:
    """Из статей с одинаковым вопросом оставляет самую свежую."""
    best: dict[str, RetrievedDoc] = {}
    for doc in docs:
        key = _same_question_key(doc.question)
        current = best.get(key)
        if current is None or doc.updated_at > current.updated_at:
            best[key] = doc
    return list(best.values())


def prefer_fresh_on_ties(ordered: list[RetrievedDoc]) -> list[RetrievedDoc]:
    """Соседи с почти одинаковой уверенностью меняются местами в пользу более свежей.

    Один проход по соседям, а не полная пересортировка по дате: дата вправе
    решать только ничью, а не переставлять статьи с разной уверенностью.
    """
    result = list(ordered)
    for i in range(len(result) - 1):
        a, b = result[i], result[i + 1]
        if abs(a.score - b.score) <= FRESHNESS_TIE and b.updated_at > a.updated_at:
            result[i], result[i + 1] = b, a
    return result


class KeywordIndex:
    """BM25 поверх статей в памяти.

    Индекс обратный: токен → статьи, где он есть, и сколько раз. Поиск обходит
    только статьи с общими словами: полный перебор на базе в десяток тысяч
    записей стоил под 50 мс на запрос, а ищем мы несколько раз на реплику.
    Номера и частоты хранятся в array — миллионы пар кортежами заняли бы сотню
    мегабайт в каждом инстансе.
    """

    K1 = 1.5
    B = 0.75

    def __init__(self) -> None:
        self._ids: list[str] = []
        self._lengths: list[int] = []
        self._postings: dict[str, tuple[array[int], array[int]]] = {}
        self._avg_len = 1.0

    def build(self, articles: list[dict[str, Any]]) -> None:
        ids: list[str] = []
        lengths: list[int] = []
        postings: dict[str, tuple[array[int], array[int]]] = {}
        for number, a in enumerate(articles):
            keywords = list(a.get("keywords", []))
            # Реальные формулировки вопроса — тоже слова статьи: «УПД не уходит
            # заказчику» находит ответ, в вопросе которого «не подписывается
            # УПД». Те, что уже есть в вопросе и ключевых словах, второй раз не
            # пишем — повтор строки лишь удвоил бы её вес.
            said = phrasings.extra(a.get("phrasings", []), [a["question"], *keywords])
            # Сокращения — понятиями (index_tokens): статья, где подпись названа
            # полностью, иначе не встретила бы вопрос с «ЭП», а «ЭП» короче трёх
            # букв и без этого в индекс не попадает вовсе.
            tokens = index_tokens(" ".join([a["question"], a["answer"], *keywords, *said]))
            ids.append(a["article_id"])
            lengths.append(len(tokens))
            for token, tf in Counter(tokens).items():
                numbers, freqs = postings.setdefault(token, (array("I"), array("I")))
                numbers.append(number)
                freqs.append(tf)
        self._ids, self._lengths, self._postings = ids, lengths, postings
        self._avg_len = (sum(lengths) / len(lengths)) if lengths else 1.0

    def _idf(self, token: str, n: int) -> float:
        postings = self._postings.get(token)
        df = len(postings[0]) if postings else 0
        return math.log(1 + (n - df + 0.5) / (df + 0.5))

    def weighted_overlap(self, query_tokens: set[str], doc_tokens: set[str]) -> float:
        """Доля совпавших слов, взвешенная по idf: 0..1.

        Сырая доля здесь бесполезна: в заголовке «Котировочные сессии — Портал
        поставщиков» слова «портал» и «поставщиков» встречаются почти в каждой
        статье базы и одинаково «совпадают» со всем подряд, размывая долю до шума.
        Взвешивание по idf оставляет от заголовка только различающую часть.
        """
        if not query_tokens or not self._ids:
            return 0.0
        n = len(self._ids)
        total = sum(self._idf(t, n) for t in query_tokens)
        if total <= 0:
            return 0.0
        return sum(self._idf(t, n) for t in query_tokens & doc_tokens) / total

    def title_match(self, query_tokens: set[str], title_tokens: set[str]) -> float:
        """Насколько вопрос статьи — это вопрос собеседника: idf-взвешенная F1 в обе стороны.

        Полнота — какая доля смысла вопроса есть в заголовке статьи, точность — какая
        доля заголовка покрыта вопросом. Одна без другой обманывает: короткий вопрос
        «покрыт» любым длинным заголовком, а длинный заголовок «совпадает» с вопросом
        по одному общему слову.
        """
        # «ЭП» и «электронная подпись» — одно понятие, и считается оно один раз
        query_tokens, title_tokens = align_concepts(query_tokens, title_tokens)
        # Вопрос слово в слово — совпадение полное, какими бы частыми ни были эти
        # слова. Без этого «что такое ЭП» не находило «Что означает ЭП?»: понятие
        # «эп» есть в тысячах статей про электронную подпись, его idf почти ноль,
        # и взвешенная доля обнуляла единственное совпавшее слово.
        if query_tokens and query_tokens == title_tokens:
            return 1.0
        recall = self.weighted_overlap(query_tokens, title_tokens)
        precision = self.weighted_overlap(title_tokens, query_tokens)
        return 2 * recall * precision / (recall + precision) if recall + precision else 0.0

    def search(self, query: str, top_k: int, *, concepts: bool = True) -> list[tuple[str, float, float]]:
        """-> [(article_id, bm25_score, idf-взвешенный overlap 0..1)].

        Overlap взвешен по idf: совпадение редких слов даёт уверенность,
        совпадение «как/через/портал» — почти нет. concepts=False — запрос
        режется просто на слова, без понятий (выключенная нормализация).
        """
        q_tokens = set(index_tokens(query) if concepts else tokenize(query))
        if not q_tokens or not self._ids:
            return []
        n = len(self._ids)
        total_weight = sum(self._idf(t, n) for t in q_tokens) or 1.0
        scores: dict[int, float] = {}
        hits: dict[int, float] = {}
        for t in q_tokens:
            postings = self._postings.get(t)
            if postings is None:
                continue
            idf = self._idf(t, n)
            for number, tf in zip(*postings, strict=True):
                denom = tf + self.K1 * (1 - self.B + self.B * self._lengths[number] / self._avg_len)
                scores[number] = scores.get(number, 0.0) + idf * tf * (self.K1 + 1) / denom
                hits[number] = hits.get(number, 0.0) + idf
        # При равной оценке — порядок статей в базе, как было при полном переборе
        best = heapq.nsmallest(top_k, scores, key=lambda number: (-scores[number], number))
        return [(self._ids[i], scores[i], hits[i] / total_weight) for i in best]


#: Доля совпадения с вопросом статьи в лексической уверенности; остальное —
#: совпадение со статьёй целиком. Совпадение только с текстом ответа поэтому не
#: поднимается выше четверти — с запасом ниже порога ответа. Подобрано на золотом
#: наборе (docs/experiments/2026-09-11-confidence.md): уверенных ошибок 6 → 0,
#: точность первого места 0.70 → 0.75 при прежнем пороге.
TITLE_SHARE = 0.75
#: Во сколько раз больше кандидатов берём из каждой выдачи, чем нужно: часть
#: выдачи отсеется по роли собеседника и виду записи, а место в слиянии RRF
#: должно достаться тем, кто фильтр прошёл.
OVERFETCH = 2

# Код ошибки портала: «РДИК_0182», «РДИК_ИК_0011». Инструкции по ошибкам написаны по
# одному шаблону и близки по словам, поэтому и векторы, и лексика охотно ставят
# первой статью про соседний код: на «ошибка РДИК 0182» бот отвечал про РДИК_0210.
# Номер кода решает, какая инструкция нужна, — его и сверяем отдельно.
_CANONICAL_CODE = re.compile(r"(?<![0-9A-ZА-ЯЁ_])([A-ZА-ЯЁ]{2,8}(?:_[A-ZА-ЯЁ]{1,6})*)_(\d{3,5})(?![0-9])")
_CODE_SEPARATORS = re.compile(r"[\s_-]+")
#: Уверенность статьи с тем же кодом, что в вопросе: код совпал до цифры — это
#: сильнее похожести слов и выше порога отказа с запасом.
CODE_MATCH_SCORE = 0.8
#: Во сколько раз падает уверенность статьи про другой код того же семейства:
#: инструкция к РДИК_0210 на вопрос про РДИК_0182 — не ответ, а ловушка.
CODE_CONFLICT_FACTOR = 0.5


def vector_confidence(hit: Hit, margin: float) -> float:
    """Векторная уверенность статьи с поправкой на формулировку.

    Формулировка — короткое обращение, и с коротким вопросом она близка сильнее,
    чем основная точка статьи, где к вопросу приписаны ключевые слова и ответ:
    тексты одного рода. На 40 вопросах не по теме это подняло долю ложных ответов
    с 0.10 до 0.25 — «сколько стоит доставка пиццы» близко к чьему-то «сколько
    стоит доставка» на 0.48 при пороге 0.40. Поэтому близость формулировки
    засчитывается за вычетом margin, а если основная точка статьи ближе этого
    остатка — берётся она. Порядок кандидатов в слиянии RRF скидка не трогает:
    там формулировка по-прежнему поднимает свою статью.
    """
    if not hit.phrasing:
        return hit.score
    return max(hit.score - margin, hit.main_score or 0.0)


def _upper(text: str) -> str:
    return text.upper().replace("Ё", "Е")


def canonical_codes(text: str) -> frozenset[str]:
    """Коды ошибок, написанные как на портале: «РДИК_0182», «РДИК_ИК_0011»."""
    return frozenset(f"{prefix}_{number}" for prefix, number in _CANONICAL_CODE.findall(_upper(text)))


class CodeIndex:
    """Коды ошибок портала в статьях базы знаний.

    В статье код ищется только в каноническом написании — через подчёркивание, как
    его показывает портал. Человек в вопросе пишет как видит или как запомнил:
    «РДИК 0182», «рдик-0182», «РДИК ИК 0011». Такие формы узнаются только для
    префиксов, которые есть в базе: иначе «УПД 2024» стал бы кодом ошибки и
    понизил бы каждую статью, где упомянут другой год.
    """

    def __init__(self, by_article: dict[str, frozenset[str]] | None = None) -> None:
        self.by_article: dict[str, frozenset[str]] = by_article or {}
        by_code: dict[str, set[str]] = {}
        for article_id, codes in self.by_article.items():
            for code in codes:
                by_code.setdefault(code, set()).add(article_id)
        self.by_code = by_code
        prefixes = {code.rsplit("_", 1)[0] for code in by_code}
        # Компактная форма префикса → каноническая: «РДИКИК» и «РДИК ИК» — это «РДИК_ИК»
        self._canonical = {prefix.replace("_", ""): prefix for prefix in prefixes}
        alternation = "|".join(
            r"[\s_-]?".join(re.escape(part) for part in prefix.split("_"))
            for prefix in sorted(prefixes, key=len, reverse=True)
        )
        self._loose = (
            re.compile(rf"(?<![0-9A-ZА-ЯЁ])({alternation})[\s_-]?(\d{{3,5}})(?![0-9])") if prefixes else None
        )

    @classmethod
    def build(cls, articles: Iterable[dict[str, Any]]) -> CodeIndex:
        found: dict[str, frozenset[str]] = {}
        for a in articles:
            codes = canonical_codes(" ".join([a["question"], a["answer"], *a.get("keywords", [])]))
            if codes:
                found[a["article_id"]] = codes
        return cls(found)

    def family(self, code: str) -> str:
        return code.rsplit("_", 1)[0]

    def asked(self, query: str) -> frozenset[str]:
        """Коды из вопроса человека в каноническом виде."""
        upper = _upper(query)
        codes = set(canonical_codes(upper))
        if self._loose is not None:
            for prefix, number in self._loose.findall(upper):
                canonical = self._canonical.get(_CODE_SEPARATORS.sub("", prefix))
                if canonical:
                    codes.add(f"{canonical}_{number}")
        return frozenset(codes)


class Retriever:
    def __init__(self, embedder: Embedder, store: VectorStore, dyn: DynamicSettings) -> None:
        self.embedder = embedder
        self.store = store
        self.dyn = dyn
        self.kw_index = KeywordIndex()
        self.speller = Speller()
        self.codes = CodeIndex()
        self._payloads: dict[str, dict[str, Any]] = {}
        # Номер последней начатой переиндексации (см. refresh_from_db)
        self._generation = 0

    async def refresh_from_db(self, session_factory: SessionFactory) -> int:
        """Перечитать активные статьи и пересобрать лексический индекс.

        Две переиндексации могут пересечься: админ опубликовал импорт и тут же
        снял статью. Сборка идёт в потоке секунды, и снимок, прочитанный раньше,
        но собранный дольше, перетёр бы новый — снятая статья жила бы в поиске
        до следующего изменения базы. Поэтому подменяет индекс только последняя
        начатая переиндексация; устаревшая молча выбрасывает свою сборку.
        """
        self._generation += 1
        generation = self._generation
        async with session_factory() as session:
            rows = (
                (await session.execute(select(KBArticle).where(KBArticle.status == "active"))).scalars().all()
            )
        articles: list[dict[str, Any]] = [
            {
                "article_id": r.id,
                "question": r.question,
                "answer": r.answer,
                "category": r.category,
                "line": r.line,
                "keywords": r.keywords or [],
                "phrasings": phrasings.of(r.origin),
                "audience": audience_of.normalize(r.audience),
                "updated_at": r.updated_at.isoformat() if r.updated_at else "",
                "kind": kbkind.of(r.origin),
            }
            for r in rows
        ]
        index, speller, codes = await asyncio.to_thread(self._prepare, articles)
        if generation != self._generation:
            return len(articles)  # пока собирали, началась более свежая переиндексация
        # Подмена — на цикле событий и без await между строками: поиск видит либо
        # старую базу целиком, либо новую, но не индекс новой с карточками старой.
        self.kw_index, self.speller, self.codes = index, speller, codes
        self._payloads = {a["article_id"]: a for a in articles}
        return len(articles)

    def load(self, articles: list[dict[str, Any]]) -> None:
        """Загрузить статьи (словари как в refresh_from_db) без базы данных."""
        self.kw_index, self.speller, self.codes = self._prepare(articles)
        self._payloads = {a["article_id"]: a for a in articles}

    @staticmethod
    def _prepare(articles: list[dict[str, Any]]) -> tuple[KeywordIndex, Speller, CodeIndex]:
        """Лексический индекс и словарь опечаток — новые объекты, без общего состояния.

        На тысячах статей это секунды работы, поэтому refresh_from_db собирает их
        в отдельном потоке, и цикл событий всё это время отвечает посетителям.
        """
        index = KeywordIndex()
        index.build(articles)
        speller = Speller()
        # Словарь для опечаток — из ответов и их вопросов, но не из ключевых слов:
        # там живые формулировки обращений вместе с их собственными опечатками, и
        # «зарегестрироватся» из них признавалось бы правильным словом.
        speller.build(
            f"{a['question']} {a['answer']} {a['category']}"
            for a in articles
            if a.get("kind", kbkind.ANSWER) == kbkind.ANSWER
        )
        # Морфология грузится здесь же: на старте, а не на первом вопросе
        # посетителя со словом, которого нет в базе.
        querytext.warm()
        return index, speller, CodeIndex.build(articles)

    def all_questions(self) -> list[str]:
        return [
            p["question"] for p in self._payloads.values() if p.get("kind", kbkind.ANSWER) == kbkind.ANSWER
        ]

    def query_forms(self, query: str) -> tuple[str, str]:
        """(для лексики, для векторов и реранкера).

        Обоим — текст с исправленными опечатками. Сокращения лексический индекс
        сводит к понятиям сам, а дописанные словами расшифровки удвоили бы вес
        понятия (см. index_tokens); векторам они тоже вредят — ниже замер.
        Выключенная нормализация оставляет запрос как есть в обоих случаях.
        """
        if not self._normalizing():
            return query, query
        corrected = self.speller.correct(query)
        # Векторам — тоже без дописанных расшифровок. Замер на 324 реальных
        # обращениях СТП: расшифровки тянули вектор запроса к статьям-определениям
        # («когда одобрят СТЕ» → «Что такое СТЕ?»), и попаданий первым было 10.8 %
        # против 13.6 % без них. BERTA понимает «УПД» и «ЭЦП» из контекста сама.
        return corrected, corrected

    def _normalizing(self) -> bool:
        """Включена ли нормализация запроса (retrieval.query_normalize).

        Выключатель снимает и понятия: запрос режется просто на слова, как до
        них, а сокращение не сводится с полной формой. Иначе «выключенная»
        нормализация продолжала бы менять уверенность ответов.
        """
        return bool(self.dyn.get("retrieval.query_normalize"))

    def _doc_from_payload(self, article_id: str, payload: dict[str, Any] | None) -> RetrievedDoc:
        p = self._payloads.get(article_id) or payload or {}
        return RetrievedDoc(
            article_id=article_id,
            question=p.get("question", ""),
            answer=p.get("answer", ""),
            category=p.get("category", ""),
            line=int(p.get("line", 1)),
            audience=audience_of.normalize(p.get("audience")),
            updated_at=str(p.get("updated_at") or ""),
            kind=str(p.get("kind") or kbkind.ANSWER),
        )

    def _fits(
        self,
        article_id: str,
        payload: dict[str, Any] | None,
        allowed: frozenset[str],
        kinds: frozenset[str],
    ) -> bool:
        """Подходит ли статья роли собеседника и виду выдачи.

        Роль и вид берём из лексического индекса (он читается из базы и всегда
        свеж), а из payload векторного хранилища — только если статьи в индексе
        нет: точки Qdrant, записанные до появления роли, её не содержат и без
        этого запаса считались бы «для всех».
        """
        p = self._payloads.get(article_id)
        if p is None:
            # Статьи нет среди активных: её сняли с публикации или заменили новой
            # версией документа, а точка в векторном хранилище осталась. Отвечать
            # ею нельзя — на стенде переимпорт выгрузки оставил в индексе тысячу
            # архивных ответов. Точке верим, только пока лексический индекс пуст.
            if self._payloads:
                return False
            p = payload or {}
        if str(p.get("kind") or kbkind.ANSWER) not in kinds:
            return False
        return audience_of.normalize(p.get("audience")) in allowed

    def _context_factors(self, article_ids: Iterable[str], context: str) -> dict[str, float]:
        """Множители «статья созвучна разделу портала, с которого спрашивают».

        Множитель, а не слагаемое, — принципиально. Надбавку приходится применять
        на двух разных шкалах: к RRF-скору (первое место около 0.016) при отборе
        кандидатов и к итоговой оценке при сортировке. С включённым реранкером
        порядок задаёт его вероятность — отрезок от нуля до единицы. Одно и то же
        слагаемое на этих шкалах означает совершенно разное: подобранное для RRF,
        после реранкера оно исчезает в шуме. Проверка на живом стенде это и
        показала — вопрос «как отменить» со страницы оферт получал ответ про
        котировочную сессию.

        Сравниваем с вопросом, категорией и ключевыми словами статьи, но не с
        текстом ответа: ответ длинный и пересекается почти с любым заголовком,
        так что сигнал в нём тонет.
        """
        ctx_tokens = set(index_tokens(context))
        weight = float(self.dyn.get("retrieval.page_context_boost"))
        if not ctx_tokens or weight <= 0:
            return {}
        factors: dict[str, float] = {}
        for article_id in article_ids:
            payload = self._payloads.get(article_id)
            if not payload:
                continue
            doc_tokens = set(
                index_tokens(
                    " ".join(
                        [
                            payload["question"],
                            payload["category"],
                            " ".join(payload.get("keywords") or []),
                        ]
                    )
                )
            )
            overlap = self.kw_index.weighted_overlap(ctx_tokens, doc_tokens)
            if overlap:
                factors[article_id] = 1.0 + weight * overlap
        return factors

    async def search(
        self,
        query: str,
        top_k: int | None = None,
        context: str = "",
        audience: str = "",
        kinds: frozenset[str] = ANSWER_KINDS,
    ) -> list[RetrievedDoc]:
        """Гибридный поиск. `audience` — роль собеседника: статьи другой стороны
        сделки отбрасываются ещё до слияния, чтобы не занимать места в выдаче.
        Неизвестная роль (пусто или all) ничего не фильтрует. `kinds` — какие
        записи искать: по умолчанию только ответы человеку. Реранкер — только
        для ответов: их порядок выбирает, чем отвечать; прочие записи сравнивают
        с порогом по уверенности, а её реранкер не меняет."""
        top_k = top_k or int(self.dyn.get("retrieval.top_k"))
        lexical, semantic = self.query_forms(query)
        allowed = audience_of.allowed(audience)
        docs, fused = await self._fuse(lexical, semantic, allowed, kinds)
        # Код ошибки из вопроса — по исходному тексту: исправление опечаток не
        # вправе «чинить» номер, а статья с этим кодом должна попасть в выдачу,
        # даже если слов вопроса в ней почти нет.
        asked = self.codes.asked(query)
        if asked:
            self._inject_code_matches(asked, docs, fused, allowed, kinds)
        factors = self._context_factors(fused, context) if context else {}
        self._assign_scores(lexical, docs.values())
        if asked:
            self._weigh_error_codes(docs, asked)

        # Порядок задаёт отдельный ключ, а НЕ d.score: показываемую уверенность
        # контекст страницы менять не вправе — её сравнивают с порогом отказа, и
        # тогда раздел портала мог бы протолкнуть слабое совпадение выше порога.
        # Уверенно ответить не туда хуже, чем честно отказаться.
        def rank_key(doc: RetrievedDoc) -> float:
            return -doc.score * factors.get(doc.article_id, 1.0)

        # Один вопрос — одна статья, и она свежая. Новая методичка на старый
        # вопрос заводит вторую статью с тем же вопросом; показывать обе значит
        # отдать модели два противоречащих текста и позволить ей смешать их.
        # Старая версия из выдачи уходит, а её место занимает следующая по силе.
        candidates_ordered = sorted(
            newest_per_question(docs.values()),
            key=lambda d: -fused[d.article_id] * factors.get(d.article_id, 1.0),
        )[:top_k]
        ordered = prefer_fresh_on_ties(sorted(candidates_ordered, key=rank_key))
        if kbkind.ANSWER in kinds and await self._rerank(semantic, ordered):
            # Реранкер решает порядок, но не уверенность. Его вероятности живут на
            # своей шкале: правильная статья на вопрос с опечаткой получает у
            # cross-encoder'а 0.17, и с порогом отказа 0.40 бот отказывал бы на
            # верных вопросах. Поэтому score остаётся гибридным и сравнивается с
            # порогом как без реранкера, а переставляет выдачу rerank_score — снова
            # с учётом раздела портала, иначе контекст страницы переставал бы
            # влиять на порядок ровно там, где включён профиль rerank.
            ordered.sort(key=lambda d: -d.rank_score * factors.get(d.article_id, 1.0))
        return ordered

    def _inject_code_matches(
        self,
        asked: frozenset[str],
        docs: dict[str, RetrievedDoc],
        fused: dict[str, float],
        allowed: frozenset[str],
        kinds: frozenset[str],
    ) -> None:
        """Статьи с кодом из вопроса — в кандидаты, даже если слияние их не подняло.

        Место в слиянии — вровень с лидером: иначе статью отрезало бы ограничение
        выдачи (top_k) раньше, чем уверенность успела бы её поднять.
        """
        lead = max(fused.values(), default=0.0)
        for code in asked:
            for article_id in self.codes.by_code.get(code, ()):
                if not self._fits(article_id, None, allowed, kinds):
                    continue
                docs.setdefault(article_id, self._doc_from_payload(article_id, None))
                fused[article_id] = max(fused.get(article_id, 0.0), lead)

    def _weigh_error_codes(self, docs: dict[str, RetrievedDoc], asked: frozenset[str]) -> None:
        """Тот же код — уверенность не ниже CODE_MATCH_SCORE; другой код того же
        семейства — уверенность падает в CODE_CONFLICT_FACTOR раз. Статьи без кодов
        не трогаем: они не про ошибку, и о том, годятся ли они, код ничего не говорит."""
        families = {self.codes.family(code) for code in asked}
        for article_id, doc in docs.items():
            have = self.codes.by_article.get(article_id, frozenset())
            if have & asked:
                doc.score = max(doc.score, CODE_MATCH_SCORE)
            elif any(self.codes.family(code) in families for code in have):
                doc.score = round(doc.score * CODE_CONFLICT_FACTOR, 4)

    async def search_cases(self, query: str, top_k: int = 3, audience: str = "") -> list[RetrievedDoc]:
        """Похожие обращения, которые решал оператор (kbkind.OPERATOR_CASE).

        Отвечать ими нельзя — это отчёты о действиях по чужим заявкам. Но если
        вопрос ближе к ним, чем к любой инструкции, значит, его решает человек, и
        видно, на какой линии такие решали раньше.

        Без реранкера (см. search): случаи сравнивают с порогом по уверенности,
        а она от реранкера не зависит. Он переставил бы только порядок — ценой
        ещё ~0.4 с на каждое сообщение, где ищутся случаи.
        """
        return await self.search(
            query, top_k=top_k, audience=audience, kinds=frozenset({kbkind.OPERATOR_CASE})
        )

    async def _fuse(
        self, lexical: str, semantic: str, allowed: frozenset[str], kinds: frozenset[str] = ANSWER_KINDS
    ) -> tuple[dict[str, RetrievedDoc], dict[str, float]]:
        """Векторная и словарная выдачи, слитые по RRF: документы и их общий скор.

        Слот кандидата занимает одна статья на вопрос — самая свежая, как её
        выбирает newest_per_question. Импорт методичек оставил в базе 646 лишних
        строк с общими заголовками — «СОДЕРЖАНИЕ» ×38, «Таблица 1 - Перечень
        терминов и сокращений» ×22, — и все двадцать две копии одинаково хорошо
        совпадали с запросом, вытесняя за отсечку статью, которая отвечает.
        Схлопывать их после слияния (как раньше делал только newest_per_question)
        поздно: до него доходит лишь то, что пролезло в отсечку.
        """
        candidates = int(self.dyn.get("retrieval.candidates"))
        rrf_k = int(self.dyn.get("retrieval.rrf_k"))
        vec_hits = []
        try:
            [qvec] = await self.embedder.embed([semantic])
            vec_hits = await self.store.search(qvec, candidates * OVERFETCH)
        except Exception:  # noqa: BLE001 — деградируем до лексического поиска
            pass
        kw_hits = self.kw_index.search(lexical, candidates * OVERFETCH, concepts=self._normalizing())

        # Ранг считаем по отфильтрованному списку: статья не для этой роли не должна
        # ни попадать в выдачу, ни сдвигать вниз подходящие
        vec_kept = [hit for hit in vec_hits if self._fits(hit.id, hit.payload, allowed, kinds)]
        kw_kept = [(aid, overlap) for aid, _bm25, overlap in kw_hits if self._fits(aid, None, allowed, kinds)]
        found = [self._doc_from_payload(hit.id, hit.payload) for hit in vec_kept]
        found += [self._doc_from_payload(aid, None) for aid, _overlap in kw_kept]
        by_id = {doc.article_id: doc for doc in found}
        # Одна статья на вопрос — та же самая свежая, что осталась бы и после слияния
        slots = {doc.article_id for doc in newest_per_question(found)}

        docs: dict[str, RetrievedDoc] = {}
        fused: dict[str, float] = {}
        margin = float(self.dyn.get("retrieval.phrasing_margin"))
        rank = 0
        for hit in vec_kept:
            if rank >= candidates or hit.id not in slots:
                continue
            d = docs.setdefault(hit.id, by_id[hit.id])
            d.vec_score = max(d.vec_score, vector_confidence(hit, margin))
            fused[hit.id] = fused.get(hit.id, 0.0) + 1.0 / (rrf_k + rank + 1)
            rank += 1
        rank = 0
        for aid, overlap in kw_kept:
            if rank >= candidates or aid not in slots:
                continue
            d = docs.setdefault(aid, by_id[aid])
            d.kw_score = max(d.kw_score, overlap)
            fused[aid] = fused.get(aid, 0.0) + 1.0 / (rrf_k + rank + 1)
            rank += 1
        return docs, fused

    def _assign_scores(self, query: str, docs: Iterable[RetrievedDoc]) -> None:
        """Показываемая уверенность: её сравнивают с порогом отказа."""
        semantic = getattr(self.embedder, "semantic", True)
        # Без нормализации — просто слова: понятий нет ни в вопросе, ни в заголовке
        tokens = index_tokens if self._normalizing() else tokenize
        q_tokens = set(tokens(query))
        for d in docs:
            d.title_score = self.kw_index.title_match(q_tokens, set(tokens(d.question)))
            # Раньше здесь стояла доля слов вопроса, найденных где угодно в статье: все
            # слова «как подписать контракт электронной подписью» нашлись в тексте про
            # справку об опыте, и бот отвечал ею с уверенностью 100 %.
            lexical = TITLE_SHARE * d.title_score + (1 - TITLE_SHARE) * d.kw_score
            # Лексический эмбеддер шумит на триграммах и в уверенность не входит;
            # настоящая семантика (TEI) — входит наравне с лексикой.
            d.score = round(max(d.vec_score, lexical) if semantic else lexical, 4)

    async def _rerank(self, query: str, ordered: list[RetrievedDoc]) -> bool:
        """Вероятности cross-encoder'а для порядка выдачи (профиль rerank). False —
        реранкер выключен или промолчал, и порядок остался прежним."""
        if not (
            self.dyn.get("retrieval.use_reranker") and isinstance(self.embedder, TEIEmbedder) and ordered
        ):
            return False
        scores = await self.embedder.rerank(query, [f"{d.question}\n{d.answer}" for d in ordered])
        if not scores:
            return False
        for d, s in zip(ordered, scores, strict=True):
            d.rerank_score = round(1 / (1 + math.exp(-s)), 4)  # сигмоида логита cross-encoder'а
        return True
