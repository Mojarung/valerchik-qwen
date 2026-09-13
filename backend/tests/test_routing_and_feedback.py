"""Классификатор линий (обучение, hot-swap, фолбэк) и аналитика отзывов (юнит-уровень)."""

import numpy as np
from fakeredis import aioredis as fake_aioredis

from app.adapters.embeddings import HashEmbedder
from app.services.feedback import _greedy_clusters, sentiment_score
from app.services.routing import LineClassifier

SAMPLES = [
    ("как зарегистрироваться на портале через госуслуги", 1),
    ("не приходит письмо подтверждения почты", 1),
    ("как добавить сотрудника в личный кабинет", 1),
    ("восстановить доступ к кабинету", 1),
    ("не работает электронная подпись криптопро", 2),
    ("ошибка 500 при подаче заявки", 2),
    ("портал не видит сертификат эцп", 2),
    ("зависает страница подписания", 2),
    ("что такое котировочная сессия аукцион", 3),
    ("как подписать контракт по 44-фз", 3),
    ("заказчик отклонил упд что делать", 3),
    ("как разместить оферту в витрине", 3),
]


async def test_classifier_train_predict_and_hot_swap(tmp_path):
    cache = fake_aioredis.FakeRedis(decode_responses=True)
    clf = LineClassifier(HashEmbedder(), tmp_path, cache)

    # до обучения — фолбэк на ключевые слова
    pred = await clf.predict("ошибка эцп не работает")
    assert pred.source in ("keywords", "default")

    meta = await clf.train(SAMPLES)
    assert meta["accuracy"] >= 0.9

    pred = await clf.predict("у меня не открывается плагин подписи, ошибка")
    assert pred.line == 2 and pred.source == "model"
    pred = await clf.predict("вопрос по котировочной сессии и контракту")
    assert pred.line == 3

    # hot-swap: второй инстанс видит новую версию через кэш без рестарта
    clf2 = LineClassifier(HashEmbedder(), tmp_path, cache)
    pred2 = await clf2.predict("не работает подпись")
    assert pred2.source == "model"


async def test_classifier_ignores_foreign_embedder_artifact(tmp_path):
    cache = fake_aioredis.FakeRedis(decode_responses=True)
    clf = LineClassifier(HashEmbedder(dim=128), tmp_path, cache)
    await clf.train(SAMPLES)
    other = LineClassifier(HashEmbedder(dim=64), tmp_path, cache)  # другой эмбеддер
    pred = await other.predict("ошибка эцп")
    assert pred.source in ("keywords", "default")  # несовместимый артефакт не загружается


def test_sentiment_lexicon():
    assert sentiment_score("Спасибо, очень быстро помогли, всё отлично!") > 0
    assert sentiment_score("Ужасно, долго, ничего не решили, бесполезно") < 0
    assert sentiment_score("Нейтральный текст про контракт") == 0.0


def test_greedy_clusters_group_similar_complaints():
    texts = [
        "очень долго ждал оператора",
        "долго ждать оператора пришлось",
        "ждал оператора почти час, долго",
        "не работает загрузка файлов",
        "файлы не загружаются в заявку",
    ]
    emb = HashEmbedder()
    import asyncio

    vectors = np.array(asyncio.run(emb.embed(texts)), dtype=np.float32)
    clusters = _greedy_clusters(vectors, texts, threshold=0.3)
    assert len(clusters) <= 3
    assert clusters[0]["size"] >= 2
    assert clusters[0]["keywords"]
