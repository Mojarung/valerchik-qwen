"""Методика оценки специалистов и качества донесения информации — чистые функции.

Что меряем. После обращения человек ставит оценку 1–5 и отвечает на два
вопроса: «Проблема решена?» (работа специалиста) и «Понятно объяснили?»
(качество донесения информации). Из этого и из журнала диалогов считаем по
каждому специалисту — оператору и ИИ-ассистенту как отдельному специалисту:

* удовлетворённость (CSAT) — доля оценок 4–5;
* долю «решено» (решение с первого обращения по словам самого человека) и долю
  «понятно»;
* скорость: для оператора — медиана времени от перевода на него до первого
  ответа, для ИИ — медиана времени ответа;
* интегральный индекс 0–100 со взвешенными составляющими (по умолчанию
  решение 40, понятность 30, удовлетворённость 20, скорость 10): решённая
  проблема важнее всего, понятность — вторая половина задания, звёзды
  дублируют оба ответа и потому весят меньше, скорость — гигиена.

Две части индекса показываются и отдельно: «результат работы» (решение,
звёзды, скорость) и «качество донесения» (понятность). Руководителю нужно
видеть, что именно чинить: знания специалиста или то, как он объясняет.
Понятность у ИИ-ассистента меряется ещё и пальцами под ответами: 👍 — человек
понял и ответ пригодился, 👎 с причиной «Непонятно» — не понял. Остальные 👎
(«ошибка», «не на мой вопрос») — про результат, а не про понятность.

Почему сглаживание. Доли и средние считаются по Байесу: к отзывам специалиста
мысленно добавляется PRIOR_WEIGHT «средних по службе» отзывов. Иначе один
отзыв «5» ставил новичка выше коллеги с сорока отзывами по 4.8, а один отзыв
«1» — хоронил. При пяти и меньше отзывах индекс помечается «мало данных».

Модуль не ходит в базу: на вход — выборки, на выход — словари для отчёта.
Поэтому каждое правило проверяется тестом на придуманных данных.
"""

from __future__ import annotations

import re
import statistics
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from app.services.wordforms import STOPWORDS, plural, stem

AI_ID = "ai"
AI_NAME = "ИИ-ассистент"

# Пятибалльная шкала: 4–5 — доволен (так считают CSAT), 1–3 — нет
POSITIVE_MIN_RATING = 4
NEGATIVE_MAX_RATING = 3

# Сколько «средних» отзывов подмешивается к каждому специалисту. Пять — это
# порядок числа отзывов у оператора за смену: один случайный отзыв весит
# шестую часть, двадцать своих — уже четыре пятых.
PRIOR_WEIGHT = 5
# Меньше пяти отзывов — выводы делать рано: индекс виден, но с пометкой
MIN_REVIEWS = 5

# Веса индекса: решение важнее всего, понятность — вторая половина задания,
# звёзды дублируют оба ответа, скорость — гигиена, а не цель.
DEFAULT_WEIGHTS: dict[str, int] = {"solved": 40, "clear": 30, "csat": 20, "speed": 10}
# Части индекса. Звёзды — к результату: их ставят за обращение целиком
RESULT_PARTS = ("solved", "csat", "speed")
CLARITY_PARTS = ("clear",)
# Причина 👎 под ответом, которая говорит о понятности, а не о правильности
UNCLEAR_REASON = "unclear"
# Норма первого ответа оператора; вдвое дольше — половина баллов за скорость
DEFAULT_REPLY_TARGET_S = 120

# Повторное обращение: тот же посетитель с тем же вопросом в течение двух суток
REPEAT_WINDOW = timedelta(hours=48)
# Вопросы «тот же», если у них больше половины общих значимых слов
REPEAT_SIMILARITY = 0.5

# Слабое место специалиста называем, когда ответов по измерению хотя бы три,
# «нет» не меньше трети и хуже, чем в среднем по службе, на 15 п. п.: иначе
# в отстающие попадал бы каждый, кому не повезло с одним отзывом.
SPOT_MIN_ANSWERS = 3
SPOT_MIN_SHARE = 1 / 3
SPOT_MARGIN = 0.15
# Медленным считаем того, кто отвечает вдвое дольше нормы
SLOW_FACTOR = 2
# Брошенное один раз обращение — случайность, два — привычка
ABANDONED_SPOT = 2
# ИИ «часто не находит ответа», если отказов пятая часть ответов и больше
REFUSAL_SPOT_SHARE = 0.2
REFUSAL_MIN_REPLIES = 5

# Тема проблемная при тех же правилах, что и слабое место специалиста
TOPIC_MIN_REVIEWS = 3
# Статья проблемная, когда после ответов по ней недовольны хотя бы в двух
# диалогах и заметно чаще, чем в среднем по статьям: один диалог — частный случай
ARTICLE_MIN_BAD = 2

# Рост негатива: неделя к неделе, в каждой хотя бы пять отзывов, прирост
# доли оценок 1–3 не меньше 10 п. п.
TREND_DAYS = 7
TREND_MIN_REVIEWS = 5
TREND_MARGIN = 0.10

# Нейтральные опоры, когда отзывов нет вовсе: середина шкалы
_NEUTRAL_SHARE = 0.5
_NEUTRAL_RATING = 3.0

# Виды ответа ИИ по meta сообщения
ANSWER = "answer"
REFUSAL = "refusal"
CLARIFY = "clarify"
OFFER = "offer"
ESCALATION = "escalation"
_CLARIFY_KINDS = frozenset({"clarify", "clarify_audience"})
_OFFER_KINDS = frozenset({"operator_offer", "offer_declined"})

_ROUND = 3
# Слова вопроса для сравнения: как в wordforms — короче трёх букв смысла не несут
_WORD = re.compile(r"[а-яёa-z0-9]+")
_MIN_WORD = 3


@dataclass(frozen=True, slots=True)
class Review:
    """Отзыв после обращения."""

    specialist: str  # AI_ID или id оператора
    rating: int
    solved: bool | None = None
    clear: bool | None = None
    comment: str = ""
    sentiment: float | None = None
    topic: str = ""
    created_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class Case:
    """Обращение глазами методики: кто вёл, когда что происходило, как отвечал ИИ."""

    session_id: str
    specialist: str  # AI_ID, если оператора не было
    created_at: datetime
    escalated_at: datetime | None = None
    closed_at: datetime | None = None
    first_reply_at: datetime | None = None  # первая реплика оператора
    visitor: str | None = None  # id вошедшего пользователя; None — аноним
    question: str = ""  # первая реплика человека
    line: int | None = None
    ai_replies: tuple[str, ...] = ()  # вид каждого ответа ИИ
    ai_latencies_ms: tuple[int, ...] = ()
    thumbs_up: int = 0
    thumbs_down: int = 0
    thumbs_unclear: int = 0  # из thumbs_down — с причиной «Непонятно»


@dataclass(frozen=True, slots=True)
class ArticleCase:
    """Диалог, где ИИ ответил по статье базы знаний, и что человек сказал об ответе.

    Звёзды и два вопроса относятся к статье, только если диалог вёл сам ИИ:
    после перевода на оператора оценка — об операторе, а не о статье.
    """

    article: str  # заголовок статьи
    topic: str = ""
    rating: int | None = None
    solved: bool | None = None
    clear: bool | None = None
    up: int = 0  # 👍 под ответами по этой статье
    down: int = 0
    down_unclear: int = 0  # из down — с причиной «Непонятно»


@dataclass(frozen=True, slots=True)
class YesNo:
    yes: int = 0
    no: int = 0

    @property
    def total(self) -> int:
        return self.yes + self.no


@dataclass(frozen=True, slots=True)
class Baseline:
    """Средние по службе: к ним тянется сглаживание и с ними сравниваются слабые места."""

    avg_rating: float
    csat: float
    solved: float
    clear: float
    low_share: float
    unsolved_share: float
    unclear_share: float


# --- примитивы ---


def share(hits: float, total: int) -> float | None:
    return round(hits / total, _ROUND) if total else None


def smoothed(hits: float, total: int, prior: float, weight: int = PRIOR_WEIGHT) -> float:
    """Байесовское среднее: (сумма + weight·prior) / (число + weight)."""
    return (hits + weight * prior) / (total + weight)


def tally(values: Iterable[bool | None]) -> YesNo:
    """Ответы «да/нет»; пропущенный вопрос не считается ни тем, ни другим."""
    yes = no = 0
    for value in values:
        if value is True:
            yes += 1
        elif value is False:
            no += 1
    return YesNo(yes, no)


def median(values: Iterable[float]) -> float | None:
    items = list(values)
    return round(statistics.median(items), 1) if items else None


def speed_score(median_s: float | None, target_s: float) -> float | None:
    """1 — в пределах нормы, вдвое дольше — 0.5, вчетверо — 0.25."""
    if median_s is None or target_s <= 0:
        return None
    return min(1.0, target_s / max(median_s, 1e-9))


def normalize_weights(raw: Mapping[str, Any] | None) -> dict[str, float]:
    """Веса из настройки: известные ключи, неотрицательные числа, остальное — по умолчанию.

    Настройку правят руками в админке, и опечатка в JSON не должна ронять отчёт:
    плохое значение молча заменяется умолчанием, все нули — набором по умолчанию.
    """
    source = raw if isinstance(raw, Mapping) else {}
    weights: dict[str, float] = {}
    for key, default in DEFAULT_WEIGHTS.items():
        value = source.get(key, default)
        valid = isinstance(value, int | float) and not isinstance(value, bool) and value >= 0
        weights[key] = float(value) if valid else float(default)
    if not sum(weights.values()):
        return {key: float(value) for key, value in DEFAULT_WEIGHTS.items()}
    return weights


def quality_index(components: Mapping[str, float | None], weights: Mapping[str, float]) -> int | None:
    """Индекс 0–100: взвешенное среднее тех составляющих, по которым есть данные.

    Вес недостающей составляющей делится между остальными пропорционально: у
    ИИ-ассистента нет «времени до первого ответа оператора», у старых отзывов —
    ответа на «решено?». Нулём такую дыру заполнять нельзя — это наказание за
    отсутствие данных, а не за работу.
    """
    available = {key: value for key, value in components.items() if value is not None and weights.get(key)}
    total = sum(weights[key] for key in available)
    if not total:
        return None
    return round(100 * sum(weights[key] * value for key, value in available.items()) / total)


def part_index(
    components: Mapping[str, float | None], keys: Sequence[str], weights: Mapping[str, float]
) -> int | None:
    """Индекс одной части — результата или донесения — с теми же весами внутри части.

    Если администратор обнулил вес всей части в общем индексе, часть всё равно
    видна отдельно: веса внутри неё тогда равные.
    """
    subset = {key: components.get(key) for key in keys}
    if not any(weights.get(key) for key in keys):
        weights = dict.fromkeys(keys, 1.0)
    return quality_index(subset, weights)


def reply_kind(meta: Mapping[str, Any]) -> str | None:
    """Вид ответа ИИ по meta сообщения; None — служебная реплика, а не ответ."""
    if "answered" not in meta:
        return None
    if meta.get("answered"):
        return ANSWER
    if meta.get("escalated"):
        return ESCALATION
    kind = meta.get("kind")
    if kind in _CLARIFY_KINDS:
        return CLARIFY
    if kind in _OFFER_KINDS:
        return OFFER
    # Не ответил, не перевёл и не переспросил — «в базе знаний ответа нет»
    return REFUSAL


def baseline(reviews: Sequence[Review]) -> Baseline:
    ratings = [r.rating for r in reviews]
    solved = tally(r.solved for r in reviews)
    clear = tally(r.clear for r in reviews)
    low = sum(1 for x in ratings if x <= NEGATIVE_MAX_RATING)
    return Baseline(
        avg_rating=statistics.fmean(ratings) if ratings else _NEUTRAL_RATING,
        csat=(len(ratings) - low) / len(ratings) if ratings else _NEUTRAL_SHARE,
        solved=solved.yes / solved.total if solved.total else _NEUTRAL_SHARE,
        clear=clear.yes / clear.total if clear.total else _NEUTRAL_SHARE,
        low_share=low / len(ratings) if ratings else 0.0,
        unsolved_share=solved.no / solved.total if solved.total else 0.0,
        unclear_share=clear.no / clear.total if clear.total else 0.0,
    )


# --- повторные обращения ---


def _question_stems(text: str) -> frozenset[str]:
    words = _WORD.findall(text.lower().replace("ё", "е"))
    return frozenset(stem(w) for w in words if len(w) >= _MIN_WORD and w not in STOPWORDS)


def _same_question(first: frozenset[str], second: frozenset[str]) -> bool:
    union = first | second
    return bool(first and second) and len(first & second) / len(union) > REPEAT_SIMILARITY


def repeat_flags(cases: Sequence[Case], window: timedelta = REPEAT_WINDOW) -> dict[str, bool]:
    """session_id → вернулся ли тот же посетитель с тем же вопросом в течение окна.

    Только для обращений с известным посетителем. Анонимный виджет не отличает
    «тот же человек спросил снова» от «другой человек спросил о том же», и
    выдумывать повтор по одному тексту вопроса мы не станем: обращение без
    посетителя в долю повторов не входит вовсе.
    """
    by_visitor: dict[str, list[Case]] = defaultdict(list)
    for case in cases:
        if case.visitor:
            by_visitor[case.visitor].append(case)
    flags: dict[str, bool] = {}
    for items in by_visitor.values():
        items.sort(key=lambda c: c.created_at)
        stems = [_question_stems(c.question) for c in items]
        for index, case in enumerate(items):
            start = case.closed_at or case.created_at
            flags[case.session_id] = any(
                later.created_at - start <= window and _same_question(stems[index], stems[index + offset])
                for offset, later in enumerate(items[index + 1 :], start=1)
            )
    return flags


# --- слабые места ---

_OUT_OF = ("отзыва", "отзывов", "отзывов")


def _spot(dimension: str, bad: int, total: int, service_bad: float) -> dict[str, Any] | None:
    """Слабое место по одному измерению или None, если оно не хуже, чем у всех."""
    if total < SPOT_MIN_ANSWERS:
        return None
    bad_share = bad / total
    if bad_share < max(SPOT_MIN_SHARE, service_bad + SPOT_MARGIN):
        return None
    text, advice = _SPOT_TEXT[dimension]
    return {
        "dimension": dimension,
        "bad": bad,
        "total": total,
        "text": text.format(bad=bad, total=total, out_of=plural(total, _OUT_OF)),
        "advice": advice,
    }


_SPOT_TEXT: dict[str, tuple[str, str]] = {
    "solved": (
        "не решает проблему: «не решено» в {bad} из {total} {out_of}",
        "разобрать нерешённые обращения и дополнить базу знаний по этим вопросам",
    ),
    "clear": (
        "объясняет непонятно: «непонятно» в {bad} из {total} {out_of}",
        "дать готовые шаблоны ответов и объяснять по шагам, без служебных слов",
    ),
    "csat": (
        "низкие оценки: 1–3 балла в {bad} из {total} {out_of}",
        "разобрать диалоги с низкими оценками вместе с наставником",
    ),
}


def _speed_spot(median_s: float | None, target_s: float) -> dict[str, Any] | None:
    if median_s is None or median_s <= SLOW_FACTOR * target_s:
        return None
    return {
        "dimension": "speed",
        "bad": round(median_s),
        "total": round(target_s),
        "text": f"отвечает медленно: первый ответ в среднем через {duration_text(median_s)} "
        f"(норма — {duration_text(target_s)})",
        "advice": "проверить загрузку смены и распределение очереди",
    }


def _abandoned_spot(abandoned: int) -> dict[str, Any] | None:
    if abandoned < ABANDONED_SPOT:
        return None
    word = plural(abandoned, ("обращение вернулось", "обращения вернулись", "обращений вернулись"))
    return {
        "dimension": "abandoned",
        "bad": abandoned,
        "total": abandoned,
        "text": f"бросает обращения: {abandoned} {word} в очередь без ответа",
        "advice": "выяснить, почему диалоги остаются без ответа, и не давать больше, чем успевает",
    }


def _refusal_spot(refusals: int, replies: int) -> dict[str, Any] | None:
    if replies < REFUSAL_MIN_REPLIES or refusals / replies < REFUSAL_SPOT_SHARE:
        return None
    return {
        "dimension": "refusal",
        "bad": refusals,
        "total": replies,
        "text": f"не находит ответа в базе знаний: {refusals} из {replies} "
        f"{plural(replies, ('ответа', 'ответов', 'ответов'))}",
        "advice": "дописать статьи по вопросам без ответа (раздел «Пробелы базы знаний»)",
    }


_MINUTE = 60


def duration_text(seconds: float) -> str:
    """«45 с», «3 мин», «1 ч 20 мин» — читается без калькулятора."""
    whole = round(seconds)
    if whole < _MINUTE:
        return f"{whole} с"
    minutes = round(whole / _MINUTE)
    if minutes < _MINUTE:
        return f"{minutes} мин"
    hours, rest = divmod(minutes, _MINUTE)
    return f"{hours} ч {rest} мин" if rest else f"{hours} ч"


# --- оценка специалистов ---


@dataclass(frozen=True, slots=True)
class Settings:
    """Параметры методики из админки."""

    weights: Mapping[str, float]
    reply_target_s: float = DEFAULT_REPLY_TARGET_S


def _ai_part(cases: Sequence[Case]) -> dict[str, Any]:
    """То, что есть только у ИИ: автоответы, отказы «нет в базе», пальцы под ответами."""
    talked = [c for c in cases if c.ai_replies]
    auto = sum(1 for c in talked if c.escalated_at is None and ANSWER in c.ai_replies)
    replies = [kind for c in cases for kind in c.ai_replies]
    refusals = replies.count(REFUSAL)
    up = sum(c.thumbs_up for c in cases)
    down = sum(c.thumbs_down for c in cases)
    latency = median(ms / 1000 for c in cases for ms in c.ai_latencies_ms)
    return {
        "cases": len(talked),
        "auto_resolved_rate": share(auto, len(talked)),
        "refusal_rate": share(refusals, len(replies)),
        "thumbs_up": up,
        "thumbs_down": down,
        "thumbs_unclear": sum(c.thumbs_unclear for c in cases),
        "thumbs_up_share": share(up, up + down),
        "median_first_reply_s": latency,
        "median_duration_s": None,
        "abandoned": 0,
        "_spots": [_refusal_spot(refusals, len(replies))],
    }


def _operator_part(cases: Sequence[Case], abandoned: int, target_s: float) -> dict[str, Any]:
    """То, что есть только у оператора: скорость, длительность, брошенные обращения."""
    # Отсчёт — от перевода на оператора: столько человек и ждал живого ответа
    waits = [
        max(0.0, (c.first_reply_at - (c.escalated_at or c.created_at)).total_seconds())
        for c in cases
        if c.first_reply_at
    ]
    durations = [
        max(0.0, (c.closed_at - (c.escalated_at or c.created_at)).total_seconds())
        for c in cases
        if c.closed_at
    ]
    wait = median(waits)
    return {
        "cases": len(cases),
        "auto_resolved_rate": None,
        "refusal_rate": None,
        "thumbs_up": 0,
        "thumbs_down": 0,
        "thumbs_unclear": 0,
        "thumbs_up_share": None,
        "median_first_reply_s": wait,
        "median_duration_s": median(durations),
        "abandoned": abandoned,
        "_spots": [_speed_spot(wait, target_s), _abandoned_spot(abandoned)],
    }


def _review_part(reviews: Sequence[Review], base: Baseline) -> dict[str, Any]:
    ratings = [r.rating for r in reviews]
    count = len(ratings)
    low = sum(1 for x in ratings if x <= NEGATIVE_MAX_RATING)
    solved = tally(r.solved for r in reviews)
    clear = tally(r.clear for r in reviews)
    sentiments = [r.sentiment for r in reviews if r.sentiment is not None]
    return {
        "count": count,
        "avg_rating": round(statistics.fmean(ratings), 2),
        "smoothed_rating": round(smoothed(sum(ratings), count, base.avg_rating), 2),
        "csat": share(count - low, count),
        "negative_share": share(low, count),
        "solved_rate": share(solved.yes, solved.total),
        "solved_total": solved.total,
        "clear_rate": share(clear.yes, clear.total),
        "clear_total": clear.total,
        "avg_sentiment": round(statistics.fmean(sentiments), 3) if sentiments else None,
        "sample_negative": next(
            (r.comment for r in reviews if r.rating <= NEGATIVE_MAX_RATING and r.comment), None
        ),
        "components": {
            # Каждая составляющая — сглаженная доля: так индекс не скачет от одного отзыва
            "solved": round(smoothed(solved.yes, solved.total, base.solved), _ROUND)
            if solved.total
            else None,
            "csat": round(smoothed(count - low, count, base.csat), _ROUND),
        },
        # Понятность досчитывается вместе с пальцами под ответами — в specialist_scores
        "_clear": clear,
        "_spots": [
            _spot("solved", solved.no, solved.total, base.unsolved_share),
            _spot("clear", clear.no, clear.total, base.unclear_share),
            _spot("csat", low, count, base.low_share),
        ],
    }


def specialist_scores(
    reviews: Sequence[Review],
    cases: Sequence[Case],
    *,
    names: Mapping[str, str],
    abandoned: Mapping[str, int],
    settings: Settings,
) -> list[dict[str, Any]]:
    """Карточка каждого специалиста, у которого есть отзывы; слабые — первыми.

    В рейтинг попадают только те, кого оценивали: без отзывов индекс свёлся бы
    к одной скорости, а это оценка не работы, а расписания.
    """
    base = baseline(reviews)
    flags = repeat_flags(cases)
    by_specialist: dict[str, list[Review]] = defaultdict(list)
    for review in reviews:
        by_specialist[review.specialist].append(review)

    scores = []
    for sid, own in by_specialist.items():
        is_ai = sid == AI_ID
        own_cases = [c for c in cases if c.specialist == sid]
        part = (
            _ai_part(cases)
            if is_ai
            else _operator_part(own_cases, abandoned.get(sid, 0), settings.reply_target_s)
        )
        rated = _review_part(own, base)
        # Сигналы понятности: ответы на «Понятно объяснили?», 👍 под ответами и
        # 👎 «Непонятно». У оператора пальцев нет — остаются одни ответы
        heard = rated.pop("_clear")
        heard = YesNo(heard.yes + part["thumbs_up"], heard.no + part["thumbs_unclear"])
        components = {
            **rated.pop("components"),
            "clear": smoothed(heard.yes, heard.total, base.clear) if heard.total else None,
            "speed": speed_score(part["median_first_reply_s"], settings.reply_target_s),
        }
        checked = [flags[c.session_id] for c in own_cases if c.session_id in flags]
        spots = [s for s in rated.pop("_spots") + part.pop("_spots") if s]
        scores.append(
            {
                "id": sid,
                "name": AI_NAME if is_ai else names.get(sid, sid),
                "kind": "ai" if is_ai else "operator",
                **rated,
                **part,
                "lines": dict(Counter(str(c.line) for c in own_cases if c.line)),
                "repeat_rate": share(sum(checked), len(checked)),
                "repeat_checked": len(checked),
                "components": {
                    key: round(value, _ROUND) if value is not None else None
                    for key, value in components.items()
                },
                "index": quality_index(components, settings.weights),
                "result_index": part_index(components, RESULT_PARTS, settings.weights),
                "clarity_index": part_index(components, CLARITY_PARTS, settings.weights),
                "low_data": rated["count"] < MIN_REVIEWS,
                "weak_spots": spots,
            }
        )
    # Слабые первыми; при равном индексе — у кого больше отзывов, тот и надёжнее внизу
    scores.sort(key=lambda s: (s["index"] if s["index"] is not None else 101, -s["count"]))
    return scores


# --- системные разрезы ---


def topic_breakdown(reviews: Sequence[Review], base: Baseline | None = None) -> list[dict[str, Any]]:
    """Тема обращения × доля нерешённых и непонятных; проблемные — первыми."""
    base = base or baseline(reviews)
    by_topic: dict[str, list[Review]] = defaultdict(list)
    for review in reviews:
        by_topic[review.topic or "Без темы"].append(review)
    rows: list[dict[str, Any]] = []
    for topic, items in by_topic.items():
        solved = tally(r.solved for r in items)
        clear = tally(r.clear for r in items)
        low = sum(1 for r in items if r.rating <= NEGATIVE_MAX_RATING)
        unsolved = _spot("solved", solved.no, solved.total, base.unsolved_share)
        unclear = _spot("clear", clear.no, clear.total, base.unclear_share)
        rows.append(
            {
                "topic": topic,
                "count": len(items),
                "unsolved": solved.no,
                "solved_total": solved.total,
                "unsolved_share": share(solved.no, solved.total),
                "unclear": clear.no,
                "clear_total": clear.total,
                "unclear_share": share(clear.no, clear.total),
                "negative_share": share(low, len(items)),
                "flagged": len(items) >= TOPIC_MIN_REVIEWS and bool(unsolved or unclear),
            }
        )
    rows.sort(
        key=lambda r: (
            not r["flagged"],
            -((r["unsolved_share"] or 0) + (r["unclear_share"] or 0)),
            -r["count"],
        )
    )
    return rows


def _window(items: Sequence[Review]) -> dict[str, Any]:
    solved = tally(r.solved for r in items)
    clear = tally(r.clear for r in items)
    low = sum(1 for r in items if r.rating <= NEGATIVE_MAX_RATING)
    return {
        "count": len(items),
        "negative": low,
        "negative_share": share(low, len(items)),
        "unsolved_share": share(solved.no, solved.total),
        "unclear_share": share(clear.no, clear.total),
    }


def negative_trend(reviews: Sequence[Review], now: datetime, days: int = TREND_DAYS) -> dict[str, Any]:
    """Негатив за последние `days` дней против таких же дней до них."""
    span = timedelta(days=days)
    dated = [r for r in reviews if r.created_at is not None]
    current = [r for r in dated if now - span <= r.created_at <= now]  # type: ignore[operator]
    previous = [r for r in dated if now - 2 * span <= r.created_at < now - span]  # type: ignore[operator]
    cur, prev = _window(current), _window(previous)
    growing = (
        cur["count"] >= TREND_MIN_REVIEWS
        and prev["count"] >= TREND_MIN_REVIEWS
        and (cur["negative_share"] or 0) - (prev["negative_share"] or 0) >= TREND_MARGIN
    )
    return {"days": days, "current": cur, "previous": prev, "growing": growing}


def _article_verdict(case: ArticleCase) -> tuple[bool, bool, bool] | None:
    """(недоволен, «не помогло», «непонятно») или None — человек ничего не сказал."""
    if case.rating is None and not case.up and not case.down:
        return None
    unclear = case.clear is False or case.down_unclear > 0
    unsolved = case.solved is False or case.down > case.down_unclear
    low = case.rating is not None and case.rating <= NEGATIVE_MAX_RATING
    return unclear or unsolved or low, unsolved, unclear


def article_breakdown(cases: Sequence[ArticleCase]) -> list[dict[str, Any]]:
    """Статьи базы знаний × недовольство после ответов по ним; проблемные — первыми.

    Единица — диалог, а не голос: человек, поставивший 👎 и потом две звезды,
    недоволен один раз. Иначе один сердитый читатель делал статью проблемной.
    """
    rows: dict[str, dict[str, Any]] = {}
    for case in cases:
        verdict = _article_verdict(case)
        if verdict is None:
            continue
        bad, unsolved, unclear = verdict
        row = rows.setdefault(
            case.article,
            {"article": case.article, "topic": case.topic, "cases": 0, "bad": 0, "unsolved": 0, "unclear": 0},
        )
        row["cases"] += 1
        row["bad"] += bad
        row["unsolved"] += unsolved
        row["unclear"] += unclear
    judged = sum(r["cases"] for r in rows.values())
    service_bad = sum(r["bad"] for r in rows.values()) / judged if judged else 0.0
    for row in rows.values():
        row["bad_share"] = share(row["bad"], row["cases"])
        row["flagged"] = row["bad"] >= ARTICLE_MIN_BAD and row["bad"] / row["cases"] >= max(
            SPOT_MIN_SHARE, service_bad + SPOT_MARGIN
        )
    return sorted(rows.values(), key=lambda r: (not r["flagged"], -r["bad"], -(r["bad_share"] or 0)))
