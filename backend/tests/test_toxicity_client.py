"""Клиент нейросети-детектора: сырые логиты TEI -> вероятности, сбой -> None."""

import json
import logging
import math

import httpx
import pytest

from app.adapters.toxicity import ToxicityClient


def _client(handler) -> ToxicityClient:
    return ToxicityClient("http://tox", transport=httpx.MockTransport(handler))


@pytest.mark.asyncio
async def test_scores_are_sigmoid_of_raw_logits():
    sent = {}

    def handler(request: httpx.Request) -> httpx.Response:
        sent.update(json.loads(request.content))
        return httpx.Response(
            200,
            json=[{"label": "insult", "score": 2.43}, {"label": "non-toxic", "score": -2.18}],
        )

    scores = await _client(handler).scores("ты тупой бот")

    # Модель мультиметочная: без raw_scores TEI делил бы единицу между метками
    assert sent == {"inputs": "ты тупой бот", "raw_scores": True, "truncate": True}
    assert scores is not None
    assert math.isclose(scores["insult"], 1 / (1 + math.exp(-2.43)))
    assert scores["non-toxic"] < 0.2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "handler",
    [
        lambda request: httpx.Response(503, text="loading"),
        lambda request: httpx.Response(200, json={"error": "unexpected"}),
    ],
)
async def test_failure_returns_none(handler):
    assert await _client(handler).scores("текст") is None


@pytest.mark.asyncio
async def test_unreachable_service_returns_none_and_recovers(caplog):
    """Лежащий сервис — None без исключения; лог — только на смене состояния."""
    caplog.set_level(logging.INFO, logger="app.adapters.toxicity")
    up = False

    def handler(request: httpx.Request) -> httpx.Response:
        if not up:
            raise httpx.ConnectError("connection refused")
        return httpx.Response(200, json=[{"label": "insult", "score": -5.0}])

    client = _client(handler)
    assert await client.scores("раз") is None
    assert await client.scores("два") is None
    assert caplog.text.count("недоступен") == 1

    up = True
    assert (await client.scores("три") or {}).get("insult", 1.0) < 0.01
    assert "снова отвечает" in caplog.text
