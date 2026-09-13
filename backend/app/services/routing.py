"""Маршрутизация обращения: тема по справочнику → линия поддержки или иной адресат.

Моделей две, и обе чистый numpy — инференс на CPU за миллисекунды:

* модель темы — TF-IDF по словам и символьным n-граммам + softmax-регрессия. Учится
  на выгрузке обращений СТП (``python -m app.tools.lines_eval train``): 25 тысяч
  реальных вопросов с темой из справочника. На одной и той же отложенной выборке
  она точнее softmax на эмбеддингах BERTA (macro-F1 по темам 56,5 % против 42,7 %),
  отвечает за 1–2 мс вместо похода в сервис эмбеддингов и в lite-режиме работает
  так же, как на стенде (цифры и методика — в docstring tools/lines_eval.py);
* модель линии на эмбеддингах — прежняя, дообучается фоном на статьях БЗ и
  эскалациях. Нужна, пока модели темы на стенде нет.

Линия выводится из темы таблицей routing.topic_lines, а положения регламента
(блокировка — СКК, обжалование — Арбитражная комиссия, снятие КС — СТП ЕАИСТ)
проверяются правилами раньше модели: это не догадка, а прямая норма.

Артефакты лежат в DATA_DIR, версия — в Redis: инстансы замечают новую версию и
горячо подменяют модели без рестарта.
"""

from __future__ import annotations

import asyncio
import gzip
import json
import logging
import math
import re
import time
import zlib
from dataclasses import asdict, dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np
import numpy.typing as npt

from app.adapters.embeddings import Embedder
from app.services import lines

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from redis.asyncio import Redis

    from app.dynamic_settings import DynamicSettings

log = logging.getLogger(__name__)

VERSION_KEY = "clf:version"
TOPICS_NPZ = "topics.npz"
TOPICS_META = "topics.json"
# Обучающий корпус модели темы (тексты выгрузки и их темы): фоновое дообучение
# берёт его отсюда и добавляет новые статьи БЗ — сама выгрузка на стенде не лежит.
TOPICS_CORPUS = "topics_corpus.json.gz"

Floats = npt.NDArray[np.float32]
Ints = npt.NDArray[np.int64]


@dataclass
class RoutePrediction:
    """Куда идёт обращение. Первые три поля — прежний контракт, остальные добавлены с умолчаниями."""

    line: int
    proba: float
    source: str  # topics | unsure | rules | model | keywords | default
    topic: str = ""  # тема справочника; пусто — модель не уверена или темы не знает
    topic_proba: float = 0.0
    addressee: str = lines.LINE  # LINE — наша линия; иначе skk | arbitration | eaist
    addressee_label: str = ""
    rule: str = ""  # какое правило регламента сработало

    @property
    def external(self) -> bool:
        return self.addressee != lines.LINE


_KEYWORD_RULES: list[tuple[int, list[str]]] = [
    (
        2,
        [
            "эцп",
            "эп ",
            "подпись",
            "ошибк",
            "не работает",
            "не открывается",
            "интеграц",
            "браузер",
            "сертификат",
            "плагин",
            "зависает",
            "техническ",
        ],
    ),
    (
        3,
        [
            "котировочн",
            "сессия",
            "контракт",
            "оферт",
            "44-фз",
            "223-фз",
            "закупк",
            "упд",
            "актирован",
            "тендер",
            "лот",
            "заявк",
        ],
    ),
]


# --- признаки: TF-IDF с хэшированием ------------------------------------------


@dataclass(frozen=True)
class FeatureConfig:
    """Как текст превращается в вектор. Хранится в артефакте: инференс обязан резать так же.

    Хэширование вместо словаря: словарь символьных n-грамм на 25 тысячах
    обращений — сотни тысяч строк JSON, а хэш-пространство — это просто размер
    матрицы весов. Коллизии при таких размерах на точность не влияют.
    """

    word_bits: int = 17  # 2^17 корзин под слова и пары слов
    char_bits: int = 18  # 2^18 корзин под символьные n-граммы
    char_min: int = 3
    char_max: int = 5
    bigrams: bool = True
    chars: bool = True
    char_weight: float = 0.5  # доля символьного блока в норме вектора

    @property
    def dim(self) -> int:
        return (1 << self.word_bits) + (1 << self.char_bits)


_TOKEN = re.compile(r"[0-9a-zа-я]+")
_URL = re.compile(r"https?://\S+|www\.\S+")
_URL_WORD = re.compile(r"[a-z]{3,}")
# Длинные числа — номера контрактов, ИНН и даты: у каждого обращения свои, темы не несут.
# Короткие оставляем: «0210», «1003» — коды ошибок РДИК, они тему как раз выдают.
_MAX_DIGITS = 4
# Смесь букв и цифр длиннее — хэш, GUID или ключ сертификата
_MAX_MIXED = 12


def _url_words(link: re.Match[str]) -> str:
    """От ссылки остаются слова пути: «…/auction/102441» говорит «закупка», домен — ничего."""
    path = link.group(0).split("/", 3)[-1]
    return " " + " ".join(_URL_WORD.findall(path)) + " "


def tokenize(text: str) -> list[str]:
    low = _URL.sub(_url_words, text.casefold().replace("ё", "е"))
    out: list[str] = []
    for token in _TOKEN.findall(low):
        if len(token) < 2:  # noqa: PLR2004 — однобуквенные предлоги и союзы
            continue
        if token.isdigit():
            if len(token) <= _MAX_DIGITS:
                out.append(token)
        elif not (len(token) > _MAX_MIXED and any(c.isdigit() for c in token)):
            out.append(token)
    return out


def _bucket(feature: str, bits: int) -> int:
    return zlib.crc32(feature.encode("utf-8")) & ((1 << bits) - 1)


@lru_cache(maxsize=200_000)
def _char_ids(token: str, lo: int, hi: int, bits: int) -> tuple[int, ...]:
    """Символьные n-граммы слова с границами: « подпис», «одпи», «ись »…

    Кэш по слову: словарь обращений — десятки тысяч слов на миллионы употреблений,
    и без кэша обучение тратило бы время на одни и те же crc32.
    """
    padded = f" {token} "
    return tuple(
        _bucket(padded[i : i + n], bits) for n in range(lo, hi + 1) for i in range(len(padded) - n + 1)
    )


def _counts(ids: Sequence[int], offset: int = 0) -> dict[int, int]:
    counts: dict[int, int] = {}
    for i in ids:
        counts[i + offset] = counts.get(i + offset, 0) + 1
    return counts


def raw_features(text: str, config: FeatureConfig) -> tuple[dict[int, int], dict[int, int]]:
    """(слова и пары слов, символьные n-граммы): номер корзины → сколько раз встретилась."""
    tokens = tokenize(text)
    word_ids = [_bucket(f"w {t}", config.word_bits) for t in tokens]
    if config.bigrams:
        pairs = zip(tokens, tokens[1:], strict=False)
        word_ids += [_bucket(f"b {a} {b}", config.word_bits) for a, b in pairs]
    chars: dict[int, int] = {}
    if config.chars:
        offset = 1 << config.word_bits
        char_ids = [
            i for t in tokens for i in _char_ids(t, config.char_min, config.char_max, config.char_bits)
        ]
        chars = _counts(char_ids, offset)
    return _counts(word_ids), chars


def _block(counts: dict[int, int], idf: Floats, weight: float) -> tuple[list[int], list[float]]:
    """Сублинейный tf × idf, норма блока — sqrt(weight): блоки складываются в единичный вектор."""
    if not counts or weight <= 0:
        return [], []
    ids = list(counts)
    values = [(1.0 + math.log(counts[i])) * float(idf[i]) for i in ids]
    norm = math.sqrt(sum(v * v for v in values)) or 1.0
    scale = math.sqrt(weight) / norm
    return ids, [v * scale for v in values]


RawFeatures = tuple[dict[int, int], dict[int, int]]


def _weigh(raw: RawFeatures, config: FeatureConfig, idf: Floats) -> tuple[Ints, Floats]:
    words, chars = raw
    char_weight = config.char_weight if config.chars else 0.0
    wi, wv = _block(words, idf, 1.0 - char_weight)
    ci, cv = _block(chars, idf, char_weight)
    return np.array(wi + ci, dtype=np.int64), np.array(wv + cv, dtype=np.float32)


def vectorize(text: str, config: FeatureConfig, idf: Floats) -> tuple[Ints, Floats]:
    return _weigh(raw_features(text, config), config, idf)


@dataclass
class Csr:
    """Разреженная матрица «документы × признаки» без scipy: три массива CSR."""

    indptr: Ints
    indices: Ints
    data: Floats
    n_features: int

    @property
    def rows(self) -> int:
        return len(self.indptr) - 1


def inverse_frequencies(raws: Sequence[RawFeatures], dim: int) -> Floats:
    """IDF по обучающим текстам: log((1+N)/(1+df)) + 1, как в классическом TF-IDF."""
    df = np.zeros(dim, dtype=np.float64)
    for words, chars in raws:
        df[list(words) + list(chars)] += 1
    return (np.log((1.0 + len(raws)) / (1.0 + df)) + 1.0).astype(np.float32)


def to_csr(texts: Sequence[str], config: FeatureConfig, idf: Floats) -> Csr:
    return raw_to_csr([raw_features(text, config) for text in texts], config, idf)


def raw_to_csr(raws: Sequence[RawFeatures], config: FeatureConfig, idf: Floats) -> Csr:
    """Признаки считаются один раз: и на IDF, и на матрицу — разбор текста дороже всего обучения."""
    indptr = [0]
    chunks_i: list[Ints] = []
    chunks_v: list[Floats] = []
    for raw in raws:
        idx, val = _weigh(raw, config, idf)
        chunks_i.append(idx)
        chunks_v.append(val)
        indptr.append(indptr[-1] + len(idx))
    return Csr(
        indptr=np.array(indptr, dtype=np.int64),
        indices=np.concatenate(chunks_i) if chunks_i else np.zeros(0, dtype=np.int64),
        data=np.concatenate(chunks_v) if chunks_v else np.zeros(0, dtype=np.float32),
        n_features=config.dim,
    )


# --- softmax-регрессия ----------------------------------------------------------


@dataclass(frozen=True)
class TrainParams:
    """Параметры обучения модели темы. Подобраны на валидации внутри обучающей части выгрузки."""

    # Шести эпох хватает: на выгрузке 12 эпох давали ту же точность (±0,5 п.п.) вдвое дольше
    epochs: int = 6
    lr: float = 0.02
    l2: float = 1e-6
    batch: int = 256
    # Вес класса ~ (1/частота)^power: 0 — как есть, 1 — полный баланс. Половина
    # поднимает редкие темы («Карточка», «Блокировка») и не ломает крупные.
    class_power: float = 0.5
    seed: int = 42


def softmax(logits: Floats) -> Floats:
    z = logits - logits.max(axis=-1, keepdims=True)
    e = np.exp(z)
    out: Floats = (e / e.sum(axis=-1, keepdims=True)).astype(np.float32)
    return out


def class_weights(y: Ints, k: int, power: float) -> Floats:
    counts = np.bincount(y, minlength=k).astype(np.float64)
    weights = (counts.sum() / np.maximum(counts, 1.0) / k) ** power
    return weights.astype(np.float32)


def _gather(x: Csr, rows: Ints) -> tuple[Ints, Floats, Ints, Ints]:
    """Строки CSR подряд: (признаки, значения, номер строки на каждый элемент, начала строк)."""
    starts, ends = x.indptr[rows], x.indptr[rows + 1]
    lens = ends - starts
    offsets = np.concatenate(([0], np.cumsum(lens)[:-1])).astype(np.int64)
    pos = np.repeat(starts - offsets, lens) + np.arange(int(lens.sum()), dtype=np.int64)
    row_of = np.repeat(np.arange(len(rows), dtype=np.int64), lens)
    return x.indices[pos], x.data[pos], row_of, offsets


def sparse_logits(x: Csr, w: Floats, b: Floats, rows: Ints) -> Floats:
    idx, val, _, offsets = _gather(x, rows)
    contrib = w[idx] * val[:, None]
    out: Floats = (np.add.reduceat(contrib, offsets, axis=0) + b).astype(np.float32)
    return out


def fit_sparse_softmax(x: Csr, y: Ints, k: int, params: TrainParams) -> tuple[Floats, Floats]:
    """Мультиномиальная логрегрессия: мини-батчи, «ленивый» Adam по затронутым строкам весов.

    Полный градиент по матрице 400 тысяч × k на каждом шаге — лишняя работа:
    батч трогает несколько десятков тысяч признаков, их и обновляем.
    """
    rng = np.random.default_rng(params.seed)
    w = np.zeros((x.n_features, k), dtype=np.float32)
    b = np.zeros(k, dtype=np.float32)
    m, v = np.zeros_like(w), np.zeros_like(w)
    mb, vb = np.zeros_like(b), np.zeros_like(b)
    sample_w = class_weights(y, k, params.class_power)[y]
    onehot = np.eye(k, dtype=np.float32)[y]
    beta1, beta2, eps = 0.9, 0.999, 1e-8
    usable = np.flatnonzero(np.diff(x.indptr) > 0)  # пустой текст не учит ничему
    step = 0
    for _ in range(params.epochs):
        order = rng.permutation(usable)
        for start in range(0, len(order), params.batch):
            rows = order[start : start + params.batch]
            idx, val, row_of, offsets = _gather(x, rows)
            logits = np.add.reduceat(w[idx] * val[:, None], offsets, axis=0) + b
            weights = sample_w[rows]
            grad = (softmax(logits) - onehot[rows]) * (weights / weights.sum())[:, None]
            uniq, inv = np.unique(idx, return_inverse=True)
            gw = np.stack(
                [np.bincount(inv, weights=val * grad[row_of, c], minlength=len(uniq)) for c in range(k)],
                axis=1,
            ).astype(np.float32)
            gw += params.l2 * w[uniq]
            step += 1
            fix1, fix2 = 1 - beta1**step, 1 - beta2**step
            m[uniq] = beta1 * m[uniq] + (1 - beta1) * gw
            v[uniq] = beta2 * v[uniq] + (1 - beta2) * gw * gw
            w[uniq] -= params.lr * (m[uniq] / fix1) / (np.sqrt(v[uniq] / fix2) + eps)
            gb = grad.sum(axis=0)
            mb = beta1 * mb + (1 - beta1) * gb
            vb = beta2 * vb + (1 - beta2) * gb * gb
            b -= params.lr * (mb / fix1) / (np.sqrt(vb / fix2) + eps)
    return w, b


def fit_dense_softmax(
    x: Floats, y: Ints, k: int, epochs: int = 300, lr: float = 0.5
) -> tuple[Floats, Floats]:
    """Полный градиентный спуск по плотным векторам — прежний способ учить модель линии."""
    n, dim = x.shape
    target = np.zeros((n, k), dtype=np.float32)
    target[np.arange(n), y] = 1.0
    rng = np.random.default_rng(42)
    w = rng.normal(0, 0.01, size=(dim, k)).astype(np.float32)
    b = np.zeros(k, dtype=np.float32)
    for _ in range(epochs):
        grad = (softmax(x @ w + b) - target) / n
        w -= lr * (x.T @ grad + 1e-4 * w)
        b -= lr * grad.sum(axis=0)
    return w, b


@dataclass
class TopicModel:
    """Модель темы: веса softmax над хэшированным TF-IDF и всё, что нужно для инференса."""

    labels: list[str]
    config: FeatureConfig
    idf: Floats
    w: Floats
    b: Floats
    meta: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def fit(
        cls,
        texts: Sequence[str],
        labels: Sequence[str],
        params: TrainParams | None = None,
        config: FeatureConfig | None = None,
    ) -> TopicModel:
        params, config = params or TrainParams(), config or FeatureConfig()
        classes = sorted(set(labels))
        y = np.array([classes.index(label) for label in labels], dtype=np.int64)
        raws = [raw_features(text, config) for text in texts]
        idf = inverse_frequencies(raws, config.dim)
        w, b = fit_sparse_softmax(raw_to_csr(raws, config, idf), y, len(classes), params)
        return cls(labels=classes, config=config, idf=idf, w=w, b=b, meta={"params": asdict(params)})

    def proba(self, text: str) -> Floats:
        idx, val = vectorize(text, self.config, self.idf)
        if not len(idx):
            return np.full(len(self.labels), 1.0 / len(self.labels), dtype=np.float32)
        return softmax(val @ self.w[idx] + self.b)

    def proba_many(self, texts: Sequence[str]) -> Floats:
        x = to_csr(texts, self.config, self.idf)
        rows = np.flatnonzero(np.diff(x.indptr) > 0)
        out = np.full((len(texts), len(self.labels)), 1.0 / len(self.labels), dtype=np.float32)
        if len(rows):
            out[rows] = softmax(sparse_logits(x, self.w, self.b, rows))
        return out

    def save(self, data_dir: Path, extra: dict[str, Any] | None = None) -> dict[str, Any]:
        data_dir.mkdir(parents=True, exist_ok=True)
        # Веса в float16: вдвое меньше файл, а на вероятностях тем разница в пятом знаке
        np.savez_compressed(
            data_dir / TOPICS_NPZ, w=self.w.astype(np.float16), b=self.b, idf=self.idf.astype(np.float16)
        )
        meta = {
            **self.meta,
            **(extra or {}),
            "kind": "tfidf-softmax",
            "labels": self.labels,
            "config": asdict(self.config),
            "trained_at": time.time(),
        }
        (data_dir / TOPICS_META).write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
        self.meta = meta
        return meta

    @classmethod
    def load(cls, data_dir: Path) -> TopicModel | None:
        npz_path, meta_path = data_dir / TOPICS_NPZ, data_dir / TOPICS_META
        if not npz_path.exists() or not meta_path.exists():
            return None
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            with np.load(npz_path) as data:
                return cls(
                    labels=[str(label) for label in meta["labels"]],
                    config=FeatureConfig(**meta["config"]),
                    idf=data["idf"].astype(np.float32),
                    w=data["w"].astype(np.float32),
                    b=data["b"].astype(np.float32),
                    meta=meta,
                )
        except (OSError, ValueError, KeyError, TypeError) as exc:
            log.warning("Модель темы в %s не читается (%s) — работаем без неё", data_dir, exc)
            return None


def save_corpus(data_dir: Path, samples: Sequence[tuple[str, str]]) -> Path:
    data_dir.mkdir(parents=True, exist_ok=True)
    path = data_dir / TOPICS_CORPUS
    with gzip.open(path, "wt", encoding="utf-8") as fh:
        json.dump([[text, topic] for text, topic in samples], fh, ensure_ascii=False)
    return path


def load_corpus(data_dir: Path) -> list[tuple[str, str]]:
    path = data_dir / TOPICS_CORPUS
    if not path.exists():
        return []
    try:
        with gzip.open(path, "rt", encoding="utf-8") as fh:
            return [(str(text), str(topic)) for text, topic in json.load(fh)]
    except (OSError, ValueError, TypeError) as exc:
        log.warning("Корпус модели темы %s не читается: %s", path, exc)
        return []


# --- из вероятностей тем — в линию ------------------------------------------------


@dataclass(frozen=True)
class RouteSettings:
    """Настройки маршрутизации одним снимком: из админки или умолчания, если её нет."""

    targets: dict[str, lines.Target]
    addressees: Mapping[str, Any]
    line_specs: Mapping[str, Any]  # routing.lines: номер линии → название и описание
    min_confidence: float
    use_topics: bool
    use_rules: bool
    use_outage: bool
    # Ниже этой уверенности в линии — первая линия без темы (см. route_from_proba)
    line_min_confidence: float = 0.0

    @classmethod
    def from_dyn(cls, dyn: DynamicSettings | None) -> RouteSettings:
        from app.dynamic_settings import SETTINGS

        def get(key: str) -> Any:
            return dyn.get(key) if dyn is not None else SETTINGS[key].default

        addressees = get("routing.addressees") or {}
        return cls(
            targets=lines.topic_targets(get("routing.topic_lines") or {}, addressees),
            addressees=addressees,
            line_specs=get("routing.lines") or {},
            min_confidence=float(get("routing.topic_min_confidence")),
            use_topics=bool(get("routing.topic_model_enabled")),
            use_rules=bool(get("routing.addressee_rules_enabled")),
            use_outage=bool(get("routing.outage_rule_enabled")),
            line_min_confidence=float(get("routing.line_min_confidence")),
        )

    def target(self, topic: str) -> lines.Target:
        return self.targets.get(topic) or lines.Target(line=1)

    def label_of(self, target: lines.Target) -> str:
        if target.addressee != lines.LINE:
            return lines.addressee_label(self.addressees, target.addressee)
        return lines.line_label(self.line_specs, target.line)


def route_from_proba(probs: Floats, labels: Sequence[str], settings: RouteSettings) -> RoutePrediction:
    """Вероятности тем → линия (или внешний адресат) с суммарной вероятностью.

    Складываем вероятности тем одной линии: две темы технической линии по 0,3
    — это уверенные 0,6 за линию, а не два сомнительных ответа. Тему называем,
    только если она сама уверенная: лучше «линия 2» без темы, чем чужая тема.
    """
    mass: dict[str, float] = {}
    for label, p in zip(labels, probs.tolist(), strict=True):
        key = lines.target_key(settings.target(label))
        mass[key] = mass.get(key, 0.0) + p
    best = max(mass, key=lambda key: mass[key])
    # Внешнему адресату — только уверенно: сказать «идите в СКК» по догадке хуже,
    # чем поставить обращение на нашу линию, где его перенаправит человек.
    if not best.startswith("line:") and mass[best] < settings.min_confidence:
        best = max((key for key in mass if key.startswith("line:")), key=lambda key: mass[key], default=best)
    if mass[best] < settings.line_min_confidence:
        return _unsure(probs, mass[best], settings)
    members = [i for i, label in enumerate(labels) if lines.target_key(settings.target(label)) == best]
    top = max(members, key=lambda i: float(probs[i]))
    target = settings.target(labels[top])
    topic_proba = float(probs[top])
    return RoutePrediction(
        line=target.line,
        proba=round(mass[best], 4),
        source="topics",
        topic=labels[top] if topic_proba >= settings.min_confidence else "",
        topic_proba=round(topic_proba, 4),
        addressee=target.addressee,
        addressee_label=settings.label_of(target),
    )


def _unsure(probs: Floats, confidence: float, settings: RouteSettings) -> RoutePrediction:
    """Модель не уверена ни в одной линии — первая линия, общие консультации, без темы.

    Вопрос не про портал («какая завтра погода») модель всё равно раскладывает по
    темам, и argmax выдавал «линия 3 — Закупки и контракты» с уверенностью 0,43.
    Человек видит, кому адресовано обращение, и чужая линия по догадке хуже
    честной первой: консультант разберётся и переведёт, если нужно. В proba —
    уверенность отвергнутой догадки: по ней в трассе видно, почему линия первая.
    """
    general = lines.Target(line=_GENERAL_LINE)
    return RoutePrediction(
        line=_GENERAL_LINE,
        proba=round(confidence, 4),
        source="unsure",
        topic_proba=round(float(probs.max()), 4),
        addressee_label=settings.label_of(general),
    )


def apply_rules(pred: RoutePrediction, text: str, audience: str, settings: RouteSettings) -> RoutePrediction:
    """Положения регламента поверх модели: адресат по норме, сбой — на техническую линию."""
    if settings.use_rules:
        addressee, rule = lines.addressee_by_rules(text, audience)
        if rule and addressee != lines.LINE:
            target = lines.parse_target(addressee, settings.addressees)
            return RoutePrediction(
                line=target.line,
                proba=1.0,
                source="rules",
                topic=pred.topic,
                topic_proba=pred.topic_proba,
                addressee=target.addressee,
                addressee_label=settings.label_of(target),
                rule=rule,
            )
        if rule:  # региональный заказчик просит снять КС — это наша линия закупок
            target = settings.target(lines.PROCUREMENT)
            return RoutePrediction(
                line=target.line,
                proba=1.0,
                source="rules",
                topic=lines.PROCUREMENT,
                topic_proba=1.0,
                addressee=lines.LINE,
                addressee_label=settings.label_of(target),
                rule=rule,
            )
    if settings.use_outage and not pred.external and lines.looks_like_outage(text):
        technical = lines.Target(line=_TECHNICAL_LINE)
        return RoutePrediction(
            line=_TECHNICAL_LINE,
            proba=max(pred.proba, 0.5),
            source=pred.source,
            topic=pred.topic,
            topic_proba=pred.topic_proba,
            addressee_label=settings.label_of(technical),
            rule="outage",
        )
    return pred


# Сбой («портал не работает», «ошибка 500») ведёт техническая линия — регламент
# отдаёт СТП «устранение сбоев (инцидентов)», и в выгрузке это вторая линия.
_TECHNICAL_LINE = 2
# Первая линия — общие консультации: сюда и тема не из справочника, и неуверенная догадка
_GENERAL_LINE = 1


class LineClassifier:
    def __init__(
        self, embedder: Embedder, data_dir: Path, cache: Redis, dyn: DynamicSettings | None = None
    ) -> None:
        self.embedder = embedder
        self.data_dir = data_dir
        self.cache = cache
        # Без настроек — таблица тем и адресатов по умолчанию; с ними — как в админке
        self.dyn = dyn
        self._w: np.ndarray | None = None
        self._b: np.ndarray | None = None
        self._labels: list[int] = []
        self._topics: TopicModel | None = None
        self._loaded_version = -1

    # --- обучение ---

    async def train(
        self, samples: list[tuple[str, int]], epochs: int = 300, lr: float = 0.5
    ) -> dict[str, Any]:
        """Модель линии на эмбеддингах: статьи БЗ и эскалации (фоновая задача)."""
        texts = [t for t, _ in samples]
        labels = sorted({line for _, line in samples})
        y_idx = np.array([labels.index(line) for _, line in samples], dtype=np.int64)
        x = np.array(await self.embedder.embed(texts), dtype=np.float32)
        w, b = fit_dense_softmax(x, y_idx, len(labels), epochs=epochs, lr=lr)
        pred = (x @ w + b).argmax(axis=1)
        accuracy = float((pred == y_idx).mean())

        self.data_dir.mkdir(parents=True, exist_ok=True)
        np.savez_compressed(self.data_dir / "classifier.npz", w=w, b=b)
        meta = {
            "labels": labels,
            "accuracy": round(accuracy, 4),
            "samples": len(texts),
            "embedder": self.embedder.version,
            "trained_at": time.time(),
        }
        (self.data_dir / "classifier.json").write_text(json.dumps(meta), encoding="utf-8")
        await self.cache.incr(VERSION_KEY)
        self._loaded_version = -1  # заставить перечитать
        log.info("Классификатор обучен: acc=%.3f, n=%d", accuracy, len(texts))
        return meta

    async def train_topics(
        self,
        texts: Sequence[str],
        topics: Sequence[str],
        params: TrainParams | None = None,
        extra: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Модель темы: обучить, положить в DATA_DIR и объявить новую версию всем инстансам.

        Обучение на всей выгрузке — около минуты CPU, поэтому в отдельном потоке:
        цикл событий воркера в это время отвечает на остальные задачи.
        """
        model = await asyncio.to_thread(TopicModel.fit, texts, topics, params)
        meta = model.save(self.data_dir, {"samples": len(texts), **(extra or {})})
        await self.cache.incr(VERSION_KEY)
        self._loaded_version = -1
        log.info("Модель темы обучена: %d обращений, %d тем", len(texts), len(model.labels))
        return meta

    # --- инференс ---

    def _load_artifact(self) -> bool:
        npz_path = self.data_dir / "classifier.npz"
        meta_path = self.data_dir / "classifier.json"
        if not npz_path.exists() or not meta_path.exists():
            return False
        data = np.load(npz_path)
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        if meta.get("embedder") != self.embedder.version:
            return False  # артефакт от другого эмбеддера — несовместим
        self._w, self._b = data["w"], data["b"]
        self._labels = [int(x) for x in meta["labels"]]
        return True

    async def _refresh(self) -> None:
        """Горячая подмена: новая версия в Redis — перечитать оба артефакта с диска."""
        version = int(await self.cache.get(VERSION_KEY) or 0)
        if version == self._loaded_version:
            return
        self._loaded_version = version
        if not self._load_artifact():
            self._w = None
        self._topics = TopicModel.load(self.data_dir)

    async def predict(
        self, text: str, *, audience: str = "", dyn: DynamicSettings | None = None
    ) -> RoutePrediction:
        """Линия и адресат обращения. `audience` — роль собеседника, нужна правилу о снятии КС."""
        await self._refresh()
        settings = RouteSettings.from_dyn(dyn or self.dyn)
        if self._topics is not None and settings.use_topics:
            pred = route_from_proba(self._topics.proba(text), self._topics.labels, settings)
        else:
            pred = await self._predict_line(text, settings)
        return apply_rules(pred, text, audience, settings)

    async def _predict_line(self, text: str, settings: RouteSettings) -> RoutePrediction:
        """Без модели темы: прежняя модель линии на эмбеддингах, затем ключевые слова."""
        if self._w is not None and self._b is not None:
            [vec] = await self.embedder.embed([text])
            probs = softmax(np.array(vec, dtype=np.float32) @ self._w + self._b)
            idx = int(probs.argmax())
            line = self._labels[idx]
            label = lines.line_label(settings.line_specs, line)
            return RoutePrediction(line=line, proba=float(probs[idx]), source="model", addressee_label=label)
        lower = text.lower()
        for line, keywords in _KEYWORD_RULES:
            if any(k in lower for k in keywords):
                label = lines.line_label(settings.line_specs, line)
                return RoutePrediction(line=line, proba=0.5, source="keywords", addressee_label=label)
        label = lines.line_label(settings.line_specs, 1)
        return RoutePrediction(line=1, proba=0.34, source="default", addressee_label=label)

    def meta(self) -> dict[str, Any] | None:
        meta_path = self.data_dir / "classifier.json"
        if meta_path.exists():
            meta: dict[str, Any] = json.loads(meta_path.read_text(encoding="utf-8"))
            return meta
        return None

    def topics_meta(self) -> dict[str, Any] | None:
        """Паспорт модели темы: на чём училась и с какой точностью (для админки и отчёта)."""
        meta_path = self.data_dir / TOPICS_META
        if meta_path.exists():
            meta: dict[str, Any] = json.loads(meta_path.read_text(encoding="utf-8"))
            return meta
        return None
