"""Раздел портала как контекст вопроса: чистка присланного и влияние на выдачу."""

import pytest

from app.services import pagectx
from app.services.retrieval import Retriever


class _Dyn:
    def __init__(self, values: dict) -> None:
        self._values = values

    def get(self, key: str, default=None):
        return self._values.get(key, default)


def test_query_string_is_dropped():
    """Портал кладёт в query номера заявок и ИНН — чужие ПДн, и хранить их нельзя."""
    page = pagectx.clean({"path": "/offers/12345?inn=7701234567&doc=99", "title": "Оферты"})
    assert page["path"] == "/offers/12345"


def test_control_characters_and_length_are_cut():
    page = pagectx.clean({"path": "/x" * 500, "title": "Оферты\x07\x00портал"})
    assert len(page["path"]) <= pagectx.MAX_PATH
    assert "\x07" not in page["title"] and "\x00" not in page["title"]


def test_garbage_gives_no_context():
    assert pagectx.clean(None) == {}
    assert pagectx.clean("не словарь") == {}
    assert pagectx.clean({"path": "", "title": ""}) == {}
    assert pagectx.as_text({}) == ""


_ARTICLES = [
    {
        "article_id": "offer",
        "question": "Как отменить оферту",
        "answer": "текст",
        "category": "Оферты",
        "line": 3,
        "keywords": [],
    },
    {
        "article_id": "session",
        "question": "Как отменить котировочную сессию",
        "answer": "текст",
        "category": "Котировочные сессии",
        "line": 3,
        "keywords": [],
    },
    # Шум: слова «портал поставщиков» есть у всех, и различать статьи они не должны
    {
        "article_id": "other",
        "question": "Как войти на портал поставщиков",
        "answer": "текст",
        "category": "Личный кабинет",
        "line": 1,
        "keywords": [],
    },
]


def _retriever(boost: float) -> Retriever:
    """Ретривер без внешних зависимостей: проверяем только ранжирование."""
    retriever = Retriever(embedder=None, store=None, dyn=_Dyn({"retrieval.page_context_boost": boost}))
    retriever._payloads = {a["article_id"]: a for a in _ARTICLES}
    retriever.kw_index.build(_ARTICLES)
    return retriever


def test_context_promotes_article_of_the_current_section():
    """«Как отменить» со страницы оферт — это про оферту, а не про сессию."""
    factors = _retriever(0.5)._context_factors(["offer", "session"], "Оферты — Портал поставщиков /offers")
    assert factors.get("offer", 1.0) > factors.get("session", 1.0)


def test_boost_is_a_multiplier_so_it_works_on_both_scales():
    """Слагаемое, подобранное для RRF-скора (~0.016), после реранкера — шкала от
    нуля до единицы — исчезало бы в шуме. Живой стенд это и показал."""
    factors = _retriever(0.5)._context_factors(["offer"], "Оферты")
    boost = factors["offer"]
    assert boost > 1.0
    rrf, reranked = 0.0150, 0.72
    assert (rrf * boost - rrf) / rrf == pytest.approx((reranked * boost - reranked) / reranked)


def test_zero_weight_disables_the_boost():
    assert _retriever(0.0)._context_factors(["offer", "session"], "Оферты") == {}


def test_common_words_in_the_title_do_not_dilute_the_signal():
    """«Котировочные сессии — Портал поставщиков»: различать должны первые два слова.

    Без взвешивания по idf «портал» и «поставщиков» совпадали бы почти с любой
    статьёй и размывали долю до шума — надбавка получалась одинаковой для всех и
    ничего не решала. Ровно это и показал живой стенд.
    """
    factors = _retriever(0.5)._context_factors(
        ["offer", "session", "other"], "Котировочные сессии — Портал поставщиков"
    )

    assert factors["session"] > factors.get("offer", 1.0)
    assert factors["session"] > factors.get("other", 1.0)


async def test_context_does_not_change_confidence(client):
    """Ключевая граница: контекст двигает порядок, но не уверенность.

    Уверенность сравнивается с порогом отказа. Если бы страница её поднимала,
    бот начал бы уверенно отвечать не туда — а это хуже честного отказа.
    """
    retriever = client.app.state.rt.retriever
    plain = {d.article_id: d.score for d in await retriever.search("как отменить")}
    with_ctx = {
        d.article_id: d.score
        for d in await retriever.search("как отменить", context="Оферты — Портал поставщиков")
    }
    common = set(plain) & set(with_ctx)
    assert common, "выдача не должна опустеть от контекста"
    assert all(plain[a] == with_ctx[a] for a in common)


def test_page_context_reaches_the_dialog(client):
    sid = client.post("/api/chat/sessions").json()["session_id"]
    client.post(
        f"/api/chat/sessions/{sid}/messages",
        json={
            "text": "Как отменить?",
            "page": {"path": "/offers/12345?inn=7701234567", "title": "Оферты"},
        },
    )
    messages = client.get(f"/api/chat/sessions/{sid}/messages").json()
    user = next(m for m in messages if m["role"] == "user")
    assert user["meta"]["page"] == {"path": "/offers/12345", "title": "Оферты"}


def test_message_without_page_still_works(client):
    """Полноэкранный чат и старые виджеты контекста не шлют — поведение прежнее."""
    sid = client.post("/api/chat/sessions").json()["session_id"]
    resp = client.post(
        f"/api/chat/sessions/{sid}/messages", json={"text": "Как зарегистрироваться на портале?"}
    )
    assert resp.status_code == 200
    messages = client.get(f"/api/chat/sessions/{sid}/messages").json()
    user = next(m for m in messages if m["role"] == "user")
    assert "page" not in (user["meta"] or {})
