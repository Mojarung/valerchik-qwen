"""Нейросетевой детектор оскорблений: cointegrated/rubert-tiny-toxicity в отдельном TEI.

Правила guardrails ловят то, что можно перечислить, — корни мата и списки слов.
Оскорбление без единого «плохого» слова («вы там совсем с головой не дружите?»)
им не по силам, а модель на 45 МБ отвечает за миллисекунды на CPU.

Модель мультиметочная (сигмоида по каждой метке), поэтому у TEI просим сырые
логиты: без raw_scores он считает softmax по всем меткам сразу, и оценки
делят единицу между собой, хотя реплика бывает и оскорблением, и угрозой.

Модель — второе мнение, а не обязательная ступень: недоступна — сообщение идёт
по правилам, как шло до неё. Чат не должен ломаться из-за упавшего классификатора.
"""

import logging
import math

import httpx

from app.config import Settings

log = logging.getLogger(__name__)

# Ответ нужен до того, как агент начнёт искать: дольше ждать нельзя — это
# задержка каждой реплики. Сама модель отвечает за 5–20 мс.
TIMEOUT_SECONDS = 2.0


def _sigmoid(logit: float) -> float:
    return 1.0 / (1.0 + math.exp(-logit))


class ToxicityClient:
    def __init__(self, base_url: str, *, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"), timeout=TIMEOUT_SECONDS, transport=transport
        )
        # В лог — только смена состояния: лежащий сервис иначе давал бы строку на каждую реплику.
        self._down = False

    async def scores(self, text: str) -> dict[str, float] | None:
        """Вероятность по каждой метке модели; None — сервис недоступен или ответил не то."""
        try:
            resp = await self._client.post(
                "/predict", json={"inputs": text, "raw_scores": True, "truncate": True}
            )
            resp.raise_for_status()
            result = {item["label"]: _sigmoid(float(item["score"])) for item in resp.json()}
        except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
            if not self._down:
                log.warning("Детектор оскорблений недоступен, проверяют только правила: %s", exc)
            self._down = True
            return None
        if self._down:
            log.info("Детектор оскорблений снова отвечает")
        self._down = False
        return result

    async def aclose(self) -> None:
        await self._client.aclose()


def build_toxicity_client(settings: Settings) -> ToxicityClient | None:
    """Пусто в TOXICITY_URL (lite, тесты) — нейросеть не спрашиваем вовсе."""
    return ToxicityClient(settings.toxicity_url) if settings.toxicity_url else None
