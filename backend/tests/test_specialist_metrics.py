"""Методика оценки специалистов — на придуманных выборках, без базы.

Каждое правило методики здесь проверяется отдельно: сглаживание не даёт одному
отзыву обогнать сорок, индекс честно обходится без недостающих данных, слабое
место называется по измерению и только когда оно хуже, чем у всей службы.
"""

from datetime import UTC, datetime, timedelta

import pytest

from app.services import specialist_metrics as sm
from app.services.specialist_metrics import Case, Review

NOW = datetime(2026, 9, 12, 12, 0, tzinfo=UTC)
SETTINGS = sm.Settings(weights=sm.normalize_weights(None), reply_target_s=120)


def _reviews(specialist: str, ratings, solved=None, clear=None, **extra) -> list[Review]:
    solved = solved if solved is not None else [None] * len(ratings)
    clear = clear if clear is not None else [None] * len(ratings)
    return [
        Review(specialist=specialist, rating=r, solved=s, clear=c, **extra)
        for r, s, c in zip(ratings, solved, clear, strict=True)
    ]


def _scores(reviews, cases=(), abandoned=None, settings=SETTINGS):
    return {
        s["id"]: s
        for s in sm.specialist_scores(
            reviews, list(cases), names={"op1": "Оператор 1"}, abandoned=abandoned or {}, settings=settings
        )
    }


# --- сглаживание и индекс ---


def test_one_perfect_review_does_not_beat_forty_excellent():
    # Служба в среднем на 4 балла: к этому среднему и тянется одинокая пятёрка
    service = _reviews("others", [5, 4, 3] * 13 + [4])
    novice = _reviews("novice", [5])
    veteran = _reviews("veteran", [5] * 32 + [4] * 8)  # средняя 4.8
    scores = _scores(service + novice + veteran)
    assert scores["novice"]["avg_rating"] == 5.0 > scores["veteran"]["avg_rating"]
    assert scores["novice"]["smoothed_rating"] < scores["veteran"]["smoothed_rating"]
    assert scores["novice"]["index"] < scores["veteran"]["index"]


def test_one_bad_review_does_not_bury_a_specialist():
    service = _reviews("others", [5, 4, 5, 5, 4, 5, 4, 5, 5, 4])
    unlucky = _reviews("unlucky", [1])
    scores = _scores(service + unlucky)
    # сырая доля довольных — 0, сглаженная тянется к средней по службе
    assert scores["unlucky"]["csat"] == 0.0
    assert scores["unlucky"]["components"]["csat"] > 0.7


def test_low_data_flag_below_five_reviews():
    scores = _scores(_reviews("a", [5, 5, 5, 5]) + _reviews("b", [5, 5, 5, 5, 5]))
    assert scores["a"]["low_data"] is True
    assert scores["b"]["low_data"] is False


def test_quality_index_uses_weights_and_skips_missing_parts():
    weights = sm.normalize_weights(None)
    full = sm.quality_index({"solved": 1.0, "clear": 1.0, "csat": 1.0, "speed": 1.0}, weights)
    assert full == 100
    # без данных по удовлетворённости и скорости веса делятся между решением и понятностью
    partial = sm.quality_index({"solved": 1.0, "clear": 0.5, "csat": None, "speed": None}, weights)
    assert partial == round(100 * (40 * 1.0 + 30 * 0.5) / 70)
    assert sm.quality_index({"solved": None}, weights) is None


def test_weights_from_settings_are_forgiving():
    assert sm.normalize_weights({"solved": 10, "clear": "много", "csat": -5}) == {
        "solved": 10.0,
        "clear": 30.0,
        "csat": 20.0,
        "speed": 10.0,
    }
    assert sm.normalize_weights({"solved": 0, "clear": 0, "csat": 0, "speed": 0}) == {
        key: float(value) for key, value in sm.DEFAULT_WEIGHTS.items()
    }
    assert sm.normalize_weights("не словарь") == sm.normalize_weights(None)


def test_speed_score_halves_at_double_target():
    assert sm.speed_score(60, 120) == 1.0
    assert sm.speed_score(240, 120) == 0.5
    assert sm.speed_score(None, 120) is None


def test_skipped_questions_count_neither_yes_nor_no():
    answers = sm.tally([True, None, False, None, True])
    assert (answers.yes, answers.no, answers.total) == (2, 1, 3)
    scores = _scores(_reviews("a", [5, 5, 4], solved=[True, None, None], clear=[None, None, None]))
    assert scores["a"]["solved_rate"] == 1.0 and scores["a"]["solved_total"] == 1
    assert scores["a"]["clear_rate"] is None and scores["a"]["components"]["clear"] is None


# --- две части индекса: результат работы и качество донесения ---


def test_index_splits_into_result_and_clarity():
    # Решает всё, а объясняет плохо: результат высокий, понятность низкая
    service = _reviews("others", [5] * 10, solved=[True] * 10, clear=[True] * 10)
    muddled = _reviews("op1", [4] * 6, solved=[True] * 6, clear=[False] * 6)
    op = _scores(service + muddled)["op1"]
    assert op["result_index"] > 80 > 60 > op["clarity_index"]
    assert op["clarity_index"] == round(100 * op["components"]["clear"])
    assert op["clarity_index"] < op["index"] < op["result_index"]


def test_part_index_stays_visible_when_its_weight_is_zero():
    weights = sm.normalize_weights({"clear": 0})
    assert sm.part_index({"clear": 0.4}, sm.CLARITY_PARTS, weights) == 40
    result = sm.part_index({"solved": 1.0, "csat": 0.5, "speed": None}, sm.RESULT_PARTS, weights)
    assert result == round(100 * (40 * 1.0 + 20 * 0.5) / 60)


def test_unclear_thumb_lowers_clarity_other_thumbs_do_not():
    reviews = _reviews(sm.AI_ID, [5, 5, 5], clear=[True, True, True])

    def clarity(**thumbs):
        case = Case("a", sm.AI_ID, NOW, ai_replies=(sm.ANSWER,), **thumbs)
        return _scores(reviews, [case])[sm.AI_ID]

    plain = clarity()
    # 👎 «Ошибка» — про результат: понятность не трогает
    assert clarity(thumbs_down=3)["components"]["clear"] == plain["components"]["clear"]
    unclear = clarity(thumbs_down=3, thumbs_unclear=3)
    assert unclear["components"]["clear"] < plain["components"]["clear"]
    assert unclear["thumbs_unclear"] == 3


def test_thumb_up_counts_as_understood():
    reviews = _reviews(sm.AI_ID, [3, 3, 3, 3], clear=[False, False, True, True])
    alone = _scores(reviews, [Case("a", sm.AI_ID, NOW)])[sm.AI_ID]
    liked = _scores(reviews, [Case("a", sm.AI_ID, NOW, thumbs_up=4)])[sm.AI_ID]
    assert liked["components"]["clear"] > alone["components"]["clear"]
    # доля в таблице — по ответам на вопрос, пальцы её не подменяют
    assert liked["clear_rate"] == alone["clear_rate"] == 0.5


# --- слабые места ---


def _service(n=20):
    return _reviews("others", [5] * n, solved=[True] * n, clear=[True] * n)


def test_weak_spot_names_the_dimension_not_the_person():
    weak = _reviews("op1", [4, 4, 5, 3, 4], solved=[True] * 5, clear=[False, False, True, False, True])
    scores = _scores(_service() + weak)
    spots = {s["dimension"]: s for s in scores["op1"]["weak_spots"]}
    assert set(spots) == {"clear"}
    assert spots["clear"]["text"] == "объясняет непонятно: «непонятно» в 3 из 5 отзывов"
    assert spots["clear"]["advice"]


def test_no_weak_spot_when_the_whole_service_is_the_same():
    # у всех «непонятно» в 40% — это беда службы, а не одного специалиста
    everyone = _reviews("others", [4] * 10, clear=[False] * 4 + [True] * 6)
    same = _reviews("op1", [4] * 5, clear=[False, False, True, True, True])
    assert _scores(everyone + same)["op1"]["weak_spots"] == []


def test_weak_spot_needs_three_answers():
    scores = _scores(_service() + _reviews("op1", [2, 2], solved=[False, False]))
    assert scores["op1"]["weak_spots"] == []


def test_ranking_puts_weakest_first():
    good = _reviews("good", [5] * 6, solved=[True] * 6, clear=[True] * 6)
    bad = _reviews("bad", [2] * 6, solved=[False] * 6, clear=[False] * 6)
    ranked = sm.specialist_scores(good + bad, [], names={}, abandoned={}, settings=SETTINGS)
    assert [s["id"] for s in ranked] == ["bad", "good"]
    assert ranked[0]["index"] < ranked[1]["index"]


# --- оператор: скорость, длительность, брошенные ---


def _op_case(session_id, wait_s, duration_s, **extra):
    escalated = NOW - timedelta(hours=1)
    return Case(
        session_id=session_id,
        specialist="op1",
        created_at=escalated - timedelta(seconds=30),
        escalated_at=escalated,
        first_reply_at=escalated + timedelta(seconds=wait_s),
        closed_at=escalated + timedelta(seconds=duration_s),
        line=2,
        **extra,
    )


def test_operator_speed_duration_and_abandoned():
    cases = [_op_case("s1", 60, 600), _op_case("s2", 900, 1200), _op_case("s3", 1200, 1800)]
    scores = _scores(_reviews("op1", [4, 4, 4]), cases, abandoned={"op1": 2})
    op = scores["op1"]
    assert op["name"] == "Оператор 1" and op["kind"] == "operator"
    assert op["median_first_reply_s"] == 900
    assert op["median_duration_s"] == 1200
    assert op["cases"] == 3 and op["lines"] == {"2": 3}
    assert op["components"]["speed"] == round(120 / 900, 3)
    dimensions = {s["dimension"]: s["text"] for s in op["weak_spots"]}
    assert dimensions["speed"] == "отвечает медленно: первый ответ в среднем через 15 мин (норма — 2 мин)"
    assert dimensions["abandoned"] == "бросает обращения: 2 обращения вернулись в очередь без ответа"


def test_ai_is_a_specialist_with_its_own_metrics():
    cases = [
        Case("a1", sm.AI_ID, NOW, ai_replies=(sm.ANSWER,), ai_latencies_ms=(1200,), thumbs_up=1),
        Case("a2", sm.AI_ID, NOW, ai_replies=(sm.REFUSAL,), ai_latencies_ms=(800,), thumbs_down=1),
        Case("a3", sm.AI_ID, NOW, ai_replies=(sm.ANSWER, sm.ANSWER), ai_latencies_ms=(900, 1100)),
        # переведён на оператора — не автоответ, хотя ИИ сначала ответил
        Case("o1", "op1", NOW, escalated_at=NOW, ai_replies=(sm.ANSWER, sm.ESCALATION)),
    ]
    ai = _scores(_reviews(sm.AI_ID, [5, 4, 2]), cases)[sm.AI_ID]
    assert ai["name"] == sm.AI_NAME and ai["kind"] == "ai"
    assert ai["cases"] == 4
    assert ai["auto_resolved_rate"] == 0.5
    assert ai["refusal_rate"] == round(1 / 6, 3)
    assert (ai["thumbs_up"], ai["thumbs_down"], ai["thumbs_up_share"]) == (1, 1, 0.5)
    assert ai["median_first_reply_s"] == 1.0
    assert ai["components"]["speed"] == 1.0


def test_ai_refusal_spot():
    cases = [Case(f"a{i}", sm.AI_ID, NOW, ai_replies=(sm.REFUSAL if i < 3 else sm.ANSWER,)) for i in range(8)]
    spots = _scores(_reviews(sm.AI_ID, [4]), cases)[sm.AI_ID]["weak_spots"]
    assert [s["text"] for s in spots] == ["не находит ответа в базе знаний: 3 из 8 ответов"]


@pytest.mark.parametrize(
    ("meta", "kind"),
    [
        ({"answered": True}, sm.ANSWER),
        ({"answered": False, "escalated": True}, sm.ESCALATION),
        ({"answered": False, "kind": "clarify"}, sm.CLARIFY),
        ({"answered": False, "kind": "operator_offer"}, sm.OFFER),
        ({"answered": False, "escalated": False}, sm.REFUSAL),
        ({"kind": "operator_joined"}, None),
    ],
)
def test_reply_kind(meta, kind):
    assert sm.reply_kind(meta) == kind


# --- повторные обращения ---


def test_repeat_needs_same_visitor_same_question_within_48h():
    def case(sid, visitor, hours, question):
        return Case(sid, sm.AI_ID, NOW + timedelta(hours=hours), visitor=visitor, question=question)

    cases = [
        case("first", "u1", 0, "Как подписать контракт электронной подписью?"),
        case("again", "u1", 20, "Не могу подписать контракт электронной подписью"),
        case("other", "u1", 21, "Где посмотреть оферты поставщика"),
        case("late", "u2", 0, "Как подписать контракт?"),
        case("late2", "u2", 50, "Как подписать контракт?"),
        case("anon", None, 1, "Как подписать контракт электронной подписью?"),
    ]
    flags = sm.repeat_flags(cases)
    assert flags["first"] is True
    assert flags["again"] is False and flags["other"] is False
    assert flags["late"] is False
    assert "anon" not in flags  # анонима не угадываем


def test_repeat_rate_is_reported_with_its_coverage():
    cases = [
        Case(
            "s1",
            sm.AI_ID,
            NOW,
            visitor="u1",
            question="Как подписать контракт подписью?",
            ai_replies=(sm.ANSWER,),
        ),
        Case(
            "s2",
            sm.AI_ID,
            NOW + timedelta(hours=1),
            visitor="u1",
            question="Подписать контракт подписью как?",
        ),
        Case("s3", sm.AI_ID, NOW, question="Анонимный вопрос про оферты"),
    ]
    ai = _scores(_reviews(sm.AI_ID, [3]), cases)[sm.AI_ID]
    assert ai["repeat_checked"] == 2
    assert ai["repeat_rate"] == 0.5


# --- системные разрезы ---


def test_topic_breakdown_flags_the_problem_topic():
    good = _reviews("ai", [5] * 10, solved=[True] * 10, clear=[True] * 10, topic="Регистрация")
    bad = _reviews("ai", [3, 2, 4, 2], solved=[False, False, True, False], clear=[True] * 4, topic="Подпись")
    rows = sm.topic_breakdown(good + bad)
    assert rows[0]["topic"] == "Подпись" and rows[0]["flagged"] is True
    assert (rows[0]["unsolved"], rows[0]["solved_total"]) == (3, 4)
    assert rows[1]["flagged"] is False


def test_negative_trend_week_over_week():
    def dated(rating, days_ago):
        return Review("ai", rating, created_at=NOW - timedelta(days=days_ago))

    previous = [dated(5, 10)] * 9 + [dated(2, 10)]
    current = [dated(5, 2)] * 6 + [dated(2, 2)] * 4
    trend = sm.negative_trend(previous + current, NOW)
    assert trend["previous"]["negative_share"] == 0.1
    assert trend["current"]["negative_share"] == 0.4
    assert trend["growing"] is True
    # мало отзывов в текущей неделе — не тревожимся
    assert sm.negative_trend([*previous, dated(1, 1)], NOW)["growing"] is False


def _article(title, **extra):
    return sm.ArticleCase(article=title, topic="Электронная подпись", **extra)


def test_article_breakdown_flags_the_article_people_leave_unhappy():
    good = [_article("Как войти в кабинет", rating=5, solved=True, clear=True) for _ in range(8)]
    bad = [
        _article("Как подписать контракт", rating=2, solved=False, clear=True),
        _article("Как подписать контракт", down=1, down_unclear=1),
        _article("Как подписать контракт", rating=5, solved=True, clear=True),
    ]
    rows = sm.article_breakdown(good + bad)
    top = rows[0]
    assert top["article"] == "Как подписать контракт" and top["flagged"] is True
    assert (top["cases"], top["bad"], top["unsolved"], top["unclear"]) == (3, 2, 1, 1)
    assert top["topic"] == "Электронная подпись"
    assert rows[1]["flagged"] is False


def test_one_unhappy_dialog_is_not_a_problem_article():
    # 👎 и две звезды в одном диалоге — одно недовольство, а не два
    cases = [_article("Статья", rating=2, solved=False, down=1)] + [_article("Другая", rating=5)] * 5
    rows = sm.article_breakdown(cases)
    assert rows[0]["article"] == "Статья" and rows[0]["bad"] == 1
    assert rows[0]["flagged"] is False


def test_article_nobody_rated_is_skipped():
    assert sm.article_breakdown([_article("Молчали")]) == []


@pytest.mark.parametrize(
    ("seconds", "text"), [(45, "45 с"), (180, "3 мин"), (4800, "1 ч 20 мин"), (7200, "2 ч")]
)
def test_duration_text(seconds, text):
    assert sm.duration_text(seconds) == text
