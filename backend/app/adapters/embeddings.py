"""Эмбеддинги: TEI (text-embeddings-inference, локальная опенсорс-модель) или хэш-фолбэк для lite/CI.

Хэш-эмбеддер — детерминированный лексический вектор (символьные триграммы), без ML-зависимостей:
семантики не даёт, но обеспечивает работоспособный поиск на демо-данных и в тестах.
"""

import hashlib
import math
import re
from typing import Protocol

import httpx

_WORD_RE = re.compile(r"[а-яёa-z0-9]+")


class Embedder(Protocol):
    dim: int
    version: str
    semantic: bool  # True — настоящая семантика (TEI); False — лексический фолбэк

    async def embed(self, texts: list[str]) -> list[list[float]]: ...


class HashEmbedder:
    """256-мерный вектор из хэшированных символьных триграмм и слов (L2-нормированный)."""

    semantic = False

    def __init__(self, dim: int = 256) -> None:
        self.dim = dim
        self.version = f"hash-{dim}"

    def _embed_one(self, text: str) -> list[float]:
        vec = [0.0] * self.dim
        words = _WORD_RE.findall(text.lower().replace("ё", "е"))
        features: list[str] = []
        for w in words:
            features.append(f"w:{w}")
            padded = f"^{w}$"
            features.extend(padded[i : i + 3] for i in range(len(padded) - 2))
        for f in features:
            h = int.from_bytes(hashlib.blake2b(f.encode(), digest_size=8).digest(), "big")
            vec[h % self.dim] += 1.0 if f.startswith("w:") else 0.5
        norm = math.sqrt(sum(v * v for v in vec)) or 1.0
        return [v / norm for v in vec]

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._embed_one(t) for t in texts]

    async def model_id(self) -> str:
        return self.version


class TEIEmbedder:
    """Клиент HuggingFace text-embeddings-inference (/embed). Модель задаётся на стороне сервиса."""

    semantic = True

    def __init__(self, base_url: str, rerank_url: str = "", dim: int = 1024, batch_size: int = 4) -> None:
        self.base_url = base_url.rstrip("/")
        self.rerank_url = rerank_url.rstrip("/")
        self.dim = dim
        self.version = f"tei@{base_url}"
        self.batch_size = batch_size
        # CPU TEI (особенно под эмуляцией amd64 на Apple Silicon) может считать
        # один батч дольше минуты. Сам backend модели принимает максимум четыре
        # запроса за раз, поэтому не перегружаем его пачкой из 16 и даём
        # достаточно времени для полной переиндексации большой базы знаний.
        self._client = httpx.AsyncClient(base_url=self.base_url, timeout=300.0)
        self._model_id: str | None = None

    async def model_id(self) -> str:
        """Какая модель загружена в TEI (/info): от неё зависят префиксы запроса и документа."""
        if self._model_id is None:
            try:
                resp = await self._client.get("/info")
                resp.raise_for_status()
                self._model_id = str(resp.json().get("model_id", ""))
            except httpx.HTTPError, ValueError:
                return ""
        return self._model_id

    async def embed(self, texts: list[str]) -> list[list[float]]:
        out: list[list[float]] = []
        for i in range(0, len(texts), self.batch_size):
            batch = texts[i : i + self.batch_size]
            resp = await self._client.post("/embed", json={"inputs": batch, "truncate": True})
            resp.raise_for_status()
            vectors = resp.json()
            out.extend(vectors)
        if out and len(out[0]) != self.dim:
            self.dim = len(out[0])
        return out

    async def rerank(self, query: str, texts: list[str]) -> list[float] | None:
        """Логиты cross-encoder реранкера (отдельный TEI-сервис, профиль rerank). Иначе None.

        Просим именно сырые логиты: без raw_scores TEI отдаёт их уже пропущенными
        через сигмоиду, а вызывающий код применяет сигмоиду сам. Вторая сигмоида
        сжимала любую оценку в полосу 0.50–0.73 — выше порога отказа, так что при
        включённом реранкере бот переставал говорить «не знаю» вовсе.
        """
        if not self.rerank_url:
            return None
        try:
            resp = await self._client.post(
                f"{self.rerank_url}/rerank",
                json={"query": query, "texts": texts, "raw_scores": True, "truncate": True},
            )
            resp.raise_for_status()
            data = resp.json()  # [{"index": i, "score": s}]
            scores = [0.0] * len(texts)
            for item in data:
                scores[item["index"]] = item["score"]
            return scores
        except httpx.HTTPError, KeyError, ValueError:
            return None

    async def aclose(self) -> None:
        await self._client.aclose()


def create_embedder(tei_url: str, rerank_url: str = "") -> Embedder:
    return TEIEmbedder(tei_url, rerank_url) if tei_url else HashEmbedder()
