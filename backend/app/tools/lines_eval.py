"""Тема и линия обращения на выгрузке СТП: честная оценка и обучение одной командой.

Оценка — отчёт report.json и report.md для слайда::

    uv run python -m app.tools.lines_eval eval --export "Выгрузка СТП за 2026.xlsx" --out отчёт \\
        [--tei-url http://127.0.0.1:58080 --emb-cache emb.npz]

Обучение финальной модели на всей выгрузке (артефакт кладётся в DATA_DIR, работающие
инстансы подхватывают его сами по версии в Redis)::

    docker compose cp "Выгрузка СТП за 2026.xlsx" api:/tmp/export.xlsx
    docker compose exec api python -m app.tools.lines_eval train --export /tmp/export.xlsx

Порог уверенности в линии (routing.line_min_confidence) — замер на той же отложенной
части и на вопросах не про портал, отчёт thresholds.md::

    uv run python -m app.tools.lines_eval thresholds --export "Выгрузка СТП за 2026.xlsx" --out отчёт

Методика. Текст обращения — «Описание» без префикса «Подтема запроса: …/» (подтему
выбирает сам заявитель из того же справочника, в чате её нет) и без отчёта портала
после «Решение:». Метка — тема, приведённая к справочнику (lines.normalize_topic);
шум («ДОП», «М», INTEGRTP-…) восстанавливается по подтеме или отбрасывается. Одинаковые
тексты целиком попадают либо в обучение, либо в проверку: иначе модель «узнаёт»
вопрос, который видела, и точность завышена. Отложенная выборка — 20 %,
стратифицированно по теме, seed 42. Эмбеддинги BERTA считать на всей выгрузке долго
(CPU-сервис, ~1,4 текста в секунду), поэтому три подхода честно сравниваются на одной
подвыборке, а модель TF-IDF дополнительно — на всей отложенной части.

Результат на выгрузке-2026 (8 тем, 24 753 обращения). Подвыборка 2 501 / 1 000:
(а) softmax на BERTA — тема 56,6 %, macro-F1 42,7 %, линия 73,2 %, 0,3–0,5 с на вопрос
(вектор у TEI на CPU); (б) TF-IDF — 62,2 % / 56,5 % / 76,0 %, 1,3 мс; (в) смесь — на
валидации лучшей оказалась доля TF-IDF 1,0, то есть та же (б). Модель (б) на всей
выгрузке: тема 66,1 %, macro-F1 62,3 %, линия 78,9 %, 1,9 мс (95 % — 6,4 мс).

Порог уверенности в линии на той же отложенной части (точность — с правилами и
сбоями): без порога 77,2 %, при 0,46 — 76,8 % (ниже порога 3,0 % обращений), вопросы
не по теме на первой линии — 67,5 % вместо 37,5 % (модель на всей выгрузке). При 0,5
их 80 %, но точность уже −1,0 п.п. «Какая завтра погода в нижнем новгороде» у модели
на всей выгрузке — 0,434 за линию 3, как и на стенде.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import re
import sys
import time
from collections import Counter
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np

from app.services import lines
from app.services.routing import (
    VERSION_KEY,
    Floats,
    RouteSettings,
    TopicModel,
    TrainParams,
    apply_rules,
    fit_dense_softmax,
    route_from_proba,
    save_corpus,
    softmax,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

#: Тема с меньшим числом обращений в модель не идёт: на десятке примеров её не
#: выучить, а в отчёте она дала бы случайный F1. В выгрузке-2026 это только
#: «Уполномоченный орган» — 11 обращений.
MIN_TOPIC = 30
TEST_SHARE = 0.2
SEED = 42
EMB_BATCH = 32  # локальный TEI делят с другими замерами — батчи небольшие, поток один
EMB_TRAIN = 2500
EMB_TEST = 1000
LATENCY_SAMPLES = 300
ERROR_EXAMPLES = 15
MAX_LATENCY_MS = 20.0
ALPHAS = tuple(i / 10 for i in range(11))
# Подтема, которой нет в справочнике, закрепляется за темой, если так в выгрузке
# записано хотя бы 5 обращений и 80 % из них — под одной темой.
LEARN_MIN = 5
LEARN_SHARE = 0.8


def _say(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


# --- данные ----------------------------------------------------------------------


@dataclass(frozen=True)
class Appeal:
    text: str
    topic: str
    incident: bool


@dataclass
class Dataset:
    appeals: list[Appeal]
    stats: dict[str, Any] = field(default_factory=dict)

    @property
    def labels(self) -> list[str]:
        return sorted({a.topic for a in self.appeals})


def _cell(row: Sequence[object], at: int) -> str:
    return str(row[at] or "") if 0 <= at < len(row) else ""


def read_rows(path: Path) -> list[tuple[str, str, str]]:
    """(тема, описание, влияние) из первого листа выгрузки; шапку ищем по названиям колонок."""
    import openpyxl

    book = openpyxl.load_workbook(path, read_only=True, data_only=True)
    try:
        rows = book.worksheets[0].iter_rows(values_only=True)
        for row in rows:
            header = [str(c or "").strip() for c in row]
            if "Тема" in header and "Описание" in header:
                break
        else:
            raise SystemExit(f"{path}: нет колонок «Тема» и «Описание»")
        topic, text = header.index("Тема"), header.index("Описание")
        impact = header.index("Влияние") if "Влияние" in header else -1
        return [(_cell(r, topic), _cell(r, text), _cell(r, impact)) for r in rows]
    finally:
        book.close()


Parsed = tuple[str, str, str, bool]  # тема как есть, подтема, текст, инцидент


def learn_subtopics(parsed: Sequence[Parsed]) -> dict[str, str]:
    """Подтемы-варианты написания («Исполнение контракта (не электронное исполнение)») → тема."""
    owners: dict[str, Counter[str]] = {}
    for raw, subtopic, _, _ in parsed:
        topic = lines.normalize_topic(raw)
        key = lines.topic_key(subtopic)
        if topic and key and key not in lines.SUBTOPIC_TOPIC:
            owners.setdefault(key, Counter())[topic] += 1
    learned: dict[str, str] = {}
    for key, counts in owners.items():
        topic, n = counts.most_common(1)[0]
        if n >= LEARN_MIN and n / sum(counts.values()) >= LEARN_SHARE:
            learned[key] = topic
    return learned


def build_dataset(rows: Sequence[tuple[str, str, str]], min_topic: int = MIN_TOPIC) -> Dataset:
    parsed: list[Parsed] = []
    for raw, description, impact in rows:
        subtopic, text = lines.appeal_text(description)
        parsed.append((raw, subtopic, text, lines.is_incident(impact)))
    learned = learn_subtopics(parsed)
    dropped: Counter[str] = Counter()
    appeals: list[Appeal] = []
    repaired = 0
    for raw, subtopic, text, incident in parsed:
        topic = lines.normalize_topic(raw, subtopic, learned)
        if not text or topic is None:
            dropped["пустой текст" if not text else "тема не восстанавливается"] += 1
            continue
        repaired += lines.topic_key(raw) != lines.topic_key(topic)
        appeals.append(Appeal(text=text, topic=topic, incident=incident))
    counts = Counter(a.topic for a in appeals)
    rare = {topic for topic, n in counts.items() if n < min_topic}
    for topic in rare:
        dropped[f"мало примеров: {topic}"] += counts[topic]
    kept = [a for a in appeals if a.topic not in rare]
    stats = {
        "rows": len(rows),
        "kept": len(kept),
        "raw_topics": len({lines.topic_key(raw) for raw, _, _ in rows}),
        "repaired_topic": repaired,
        "dropped": dict(dropped),
        "incidents": sum(a.incident for a in kept),
        "topics": dict(Counter(a.topic for a in kept).most_common()),
        "learned_subtopics": len(learned),
    }
    return Dataset(appeals=kept, stats=stats)


_TEXT_KEY = re.compile(r"[^0-9a-zа-я]+")


def _text_key(text: str) -> str:
    return _TEXT_KEY.sub(" ", text.casefold().replace("ё", "е")).strip()


def split(
    appeals: Sequence[Appeal], share: float = TEST_SHARE, seed: int = SEED
) -> tuple[list[int], list[int]]:
    """Отложенная выборка: стратифицированно по теме, одинаковые тексты — целиком по одну сторону."""
    groups: dict[str, list[int]] = {}
    for i, appeal in enumerate(appeals):
        groups.setdefault(_text_key(appeal.text), []).append(i)
    by_topic: dict[str, list[str]] = {}
    for key, members in groups.items():
        topic = Counter(appeals[i].topic for i in members).most_common(1)[0][0]
        by_topic.setdefault(topic, []).append(key)
    rng = np.random.default_rng(seed)
    test: list[int] = []
    for topic in sorted(by_topic):
        keys = by_topic[topic]
        for j in rng.permutation(len(keys))[: round(len(keys) * share)]:
            test.extend(groups[keys[j]])
    held = set(test)
    return [i for i in range(len(appeals)) if i not in held], sorted(test)


def subsample(indices: Sequence[int], appeals: Sequence[Appeal], size: int, seed: int = SEED) -> list[int]:
    """Стратифицированная подвыборка: доли тем как во всей выборке, у каждой темы — хотя бы 5."""
    if size >= len(indices):
        return list(indices)
    by_topic: dict[str, list[int]] = {}
    for i in indices:
        by_topic.setdefault(appeals[i].topic, []).append(i)
    rng = np.random.default_rng(seed)
    picked: list[int] = []
    for topic in sorted(by_topic):
        members = by_topic[topic]
        take = min(len(members), max(5, round(size * len(members) / len(indices))))
        picked.extend(members[j] for j in rng.permutation(len(members))[:take])
    return sorted(picked)


# --- метрики -----------------------------------------------------------------------


def classification_report(gold: Sequence[str], pred: Sequence[str], labels: Sequence[str]) -> dict[str, Any]:
    """Точность, macro-F1 (по классам, что есть в проверке), P/R/F1 по классам и матрица ошибок."""
    at = {label: i for i, label in enumerate(labels)}
    confusion = np.zeros((len(labels), len(labels)), dtype=np.int64)
    for g, p in zip(gold, pred, strict=True):
        confusion[at[g], at[p]] += 1
    per_class: dict[str, dict[str, float]] = {}
    for i, label in enumerate(labels):
        tp = int(confusion[i, i])
        support = int(confusion[i].sum())
        predicted = int(confusion[:, i].sum())
        precision = tp / predicted if predicted else 0.0
        recall = tp / support if support else 0.0
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        per_class[label] = {
            "precision": round(precision, 4),
            "recall": round(recall, 4),
            "f1": round(f1, 4),
            "support": support,
        }
    present = [c for c in per_class.values() if c["support"]]
    return {
        "accuracy": round(float(np.trace(confusion)) / max(1, len(gold)), 4),
        "macro_f1": round(sum(c["f1"] for c in present) / max(1, len(present)), 4),
        "per_class": per_class,
        "labels": list(labels),
        "confusion": confusion.tolist(),
    }


def gold_target(appeal: Appeal, settings: RouteSettings) -> str:
    """Правильный адресат: линия темы, а инцидент «услуга недоступна» — техническая линия."""
    target = settings.target(appeal.topic)
    if appeal.incident and target.addressee == lines.LINE:
        target = lines.Target(line=TECHNICAL_LINE)
    return lines.target_key(target)


TECHNICAL_LINE = 2


def target_name(key: str, settings: RouteSettings) -> str:
    if key.startswith("line:"):
        line = int(key.split(":", 1)[1])
        return f"Линия {line} — {lines.line_label(settings.line_specs, line)}"
    return lines.addressee_label(settings.addressees, key) or key


def routes(
    probs: Floats, labels: Sequence[str], texts: Sequence[str], settings: RouteSettings
) -> tuple[list[str], list[bool]]:
    """Адресат после правил регламента и признак «модель не уверена в линии» — на каждое обращение."""
    keys, unsure = [], []
    for row, text in zip(probs, texts, strict=True):
        model = route_from_proba(row, labels, settings)
        pred = apply_rules(model, text, "", settings)
        keys.append(lines.target_key(lines.Target(line=pred.line, addressee=pred.addressee)))
        unsure.append(model.source == "unsure")
    return keys, unsure


def predicted_targets(
    probs: Floats, labels: Sequence[str], texts: Sequence[str], settings: RouteSettings
) -> list[str]:
    return routes(probs, labels, texts, settings)[0]


def line_reports(
    probs: Floats, labels: Sequence[str], test: Sequence[Appeal], settings: RouteSettings
) -> dict[str, Any]:
    """Точность по линиям трёх ступеней: только тема модели → + адресаты регламента → + сбои."""
    gold = [target_name(gold_target(a, settings), settings) for a in test]
    texts = [a.text for a in test]
    stages = {
        "topic_only": replace(settings, use_rules=False, use_outage=False),
        "with_rules": replace(settings, use_rules=True, use_outage=False),
        "with_rules_and_outage": replace(settings, use_rules=True, use_outage=True),
    }
    out: dict[str, Any] = {}
    for name, stage in stages.items():
        pred = [target_name(k, settings) for k in predicted_targets(probs, labels, texts, stage)]
        names = sorted(set(gold) | set(pred))
        out[name] = classification_report(gold, pred, names)
    return out


def topic_report(probs: Floats, labels: Sequence[str], test: Sequence[Appeal]) -> dict[str, Any]:
    pred = [labels[i] for i in probs.argmax(axis=1)]
    return classification_report([a.topic for a in test], pred, labels)


# --- эмбеддинги BERTA (вариант «а») ------------------------------------------------------


class EmbeddingCache:
    """Векторы текстов с диска, недостающие — у TEI батчами по 32 в один поток.

    Ключ — sha1 текста: кэш переживает смену выборки и перезапуск прогона, а
    сервис эмбеддингов, который делят с другими замерами, не считает дважды одно и то же.
    """

    def __init__(self, path: Path | None, tei_url: str) -> None:
        self.path = path
        self.tei_url = tei_url.rstrip("/")
        self.vectors: dict[str, Floats] = {}
        if path is not None and path.exists():
            with np.load(path) as data:
                self.vectors = dict(zip(data["keys"].tolist(), data["vectors"], strict=True))

    @property
    def available(self) -> bool:
        return bool(self.tei_url or self.vectors)

    @staticmethod
    def key(text: str) -> str:
        return hashlib.sha1(text.encode("utf-8")).hexdigest()

    def _save(self) -> None:
        if self.path is None or not self.vectors:
            return
        keys = list(self.vectors)
        np.savez(
            self.path,
            keys=np.array(keys),
            vectors=np.array([self.vectors[k] for k in keys], dtype=np.float32),
        )

    def embed(self, texts: Sequence[str]) -> Floats | None:
        missing = list(dict.fromkeys(t for t in texts if self.key(t) not in self.vectors))
        if missing and not self.tei_url:
            return None
        if missing:
            self._fetch(missing)
        return np.stack([self.vectors[self.key(t)] for t in texts]).astype(np.float32)

    def _fetch(self, texts: Sequence[str]) -> None:
        import httpx

        _say(f"эмбеддинги: считаем {len(texts)} текстов у {self.tei_url}")
        started = time.perf_counter()
        with httpx.Client(base_url=self.tei_url, timeout=600.0) as client:
            for n, start in enumerate(range(0, len(texts), EMB_BATCH), start=1):
                batch = list(texts[start : start + EMB_BATCH])
                resp = client.post("/embed", json={"inputs": batch, "truncate": True})
                resp.raise_for_status()
                for text, vec in zip(batch, resp.json(), strict=True):
                    self.vectors[self.key(text)] = np.array(vec, dtype=np.float32)
                if n % 10 == 0:
                    self._save()
                    done = start + len(batch)
                    _say(f"  {done}/{len(texts)} за {time.perf_counter() - started:.0f} с")
        self._save()

    def latency_ms(self, texts: Sequence[str]) -> float | None:
        """Сколько ждать вектор одного вопроса — столько стоит вариант «а» на каждом обращении."""
        if not self.tei_url:
            return None
        import httpx

        spent = []
        with httpx.Client(base_url=self.tei_url, timeout=120.0) as client:
            for text in texts:
                started = time.perf_counter()
                client.post("/embed", json={"inputs": [text], "truncate": True}).raise_for_status()
                spent.append((time.perf_counter() - started) * 1000)
        return round(float(np.median(spent)), 2)


def fit_embeddings(x: Floats, y: Sequence[str], labels: Sequence[str]) -> Callable[[Floats], Floats]:
    """Вариант «а» — ровно прежний способ: softmax полным градиентным спуском по векторам."""
    y_idx = np.array([labels.index(t) for t in y], dtype=np.int64)
    w, b = fit_dense_softmax(x, y_idx, len(labels))
    return lambda vectors: softmax(vectors @ w + b)


def fuse(p_tfidf: Floats, p_emb: Floats, alpha: float) -> Floats:
    """Вариант «в» — смесь в лог-вероятностях: alpha — доля TF-IDF."""
    logits = alpha * np.log(p_tfidf + 1e-9) + (1 - alpha) * np.log(p_emb + 1e-9)
    return softmax(logits.astype(np.float32))


def macro_f1(probs: Floats, labels: Sequence[str], gold: Sequence[Appeal]) -> float:
    return float(topic_report(probs, labels, gold)["macro_f1"])


# --- прогон ----------------------------------------------------------------------------


def tfidf_latency(model: TopicModel, texts: Sequence[str], settings: RouteSettings) -> dict[str, float]:
    """Полный путь одного обращения: признаки, модель, линия и правила регламента."""
    spent = []
    for text in texts[:LATENCY_SAMPLES]:
        started = time.perf_counter()
        apply_rules(route_from_proba(model.proba(text), model.labels, settings), text, "", settings)
        spent.append((time.perf_counter() - started) * 1000)
    return {"p50_ms": round(float(np.median(spent)), 3), "p95_ms": round(float(np.percentile(spent, 95)), 3)}


@dataclass(frozen=True)
class Holdout:
    """На чём проверяем: темы-классы, отложенные обращения и настройки маршрута."""

    labels: Sequence[str]
    test: Sequence[Appeal]
    settings: RouteSettings


def _variant(name: str, probs: Floats, holdout: Holdout, latency_ms: float | None) -> dict[str, Any]:
    topics = topic_report(probs, holdout.labels, holdout.test)
    by_line = line_reports(probs, holdout.labels, holdout.test, holdout.settings)
    return {
        "name": name,
        "topic_accuracy": topics["accuracy"],
        "topic_macro_f1": topics["macro_f1"],
        "line_accuracy": by_line["with_rules_and_outage"]["accuracy"],
        "line_accuracy_topic_only": by_line["topic_only"]["accuracy"],
        "latency_ms": latency_ms,
        "topics": topics,
        "lines": by_line,
    }


def compare_on_subset(
    ds: Dataset,
    parts: tuple[Sequence[int], Sequence[int]],
    cache: EmbeddingCache,
    settings: RouteSettings,
    sizes: tuple[int, int],
) -> dict[str, Any] | None:
    """Три подхода на одной подвыборке: (а) эмбеддинги, (б) TF-IDF, (в) их смесь.

    Долю TF-IDF в смеси подбираем на валидации внутри обучающей части подвыборки:
    подбор на проверочной части — подгонка под ответ.
    """
    train, test = parts
    if not cache.available:
        return None
    labels = ds.labels
    sub_train = subsample(train, ds.appeals, sizes[0])
    sub_test = subsample(test, ds.appeals, sizes[1], seed=SEED + 1)
    tr = [ds.appeals[i] for i in sub_train]
    te = [ds.appeals[i] for i in sub_test]
    x_te = cache.embed([a.text for a in te])
    x_tr = cache.embed([a.text for a in tr])
    if x_tr is None or x_te is None:
        _say("эмбеддингов в кэше не хватает, а TEI не задан — сравнение с вариантом «а» пропущено")
        return None
    fit_idx, val_idx = split(tr, share=0.2, seed=SEED + 2)
    alpha = _pick_alpha(tr, fit_idx, val_idx, x_tr, labels)
    emb_model = fit_embeddings(x_tr, [a.topic for a in tr], labels)
    tfidf = TopicModel.fit([a.text for a in tr], [a.topic for a in tr])
    p_emb, p_tfidf = emb_model(x_te), tfidf.proba_many([a.text for a in te])
    held = Holdout(labels, te, settings)
    emb_ms = cache.latency_ms([a.text for a in te[:10]])
    tfidf_ms = tfidf_latency(tfidf, [a.text for a in te], settings)["p50_ms"]
    return {
        "train": len(tr),
        "test": len(te),
        "alpha": alpha,
        "variants": [
            _variant("а) softmax на эмбеддингах BERTA (прежний подход)", p_emb, held, emb_ms),
            _variant("б) TF-IDF слова + символьные n-граммы, softmax", p_tfidf, held, tfidf_ms),
            _variant(
                f"в) смесь а+б (доля TF-IDF {alpha})",
                fuse(p_tfidf, p_emb, alpha),
                held,
                None if emb_ms is None else round(emb_ms + tfidf_ms, 2),
            ),
        ],
    }


def _pick_alpha(
    tr: Sequence[Appeal], fit_idx: Sequence[int], val_idx: Sequence[int], x: Floats, labels: Sequence[str]
) -> float:
    fit_part, val_part = [tr[i] for i in fit_idx], [tr[i] for i in val_idx]
    emb = fit_embeddings(x[list(fit_idx)], [a.topic for a in fit_part], labels)
    tfidf = TopicModel.fit([a.text for a in fit_part], [a.topic for a in fit_part])
    p_emb, p_tfidf = emb(x[list(val_idx)]), tfidf.proba_many([a.text for a in val_part])
    return max(ALPHAS, key=lambda alpha: macro_f1(fuse(p_tfidf, p_emb, alpha), labels, val_part))


def rules_on_data(ds: Dataset) -> dict[str, Any]:
    """Где срабатывают правила регламента и какие темы стоят у этих обращений в выгрузке."""
    hits: dict[str, Counter[str]] = {}
    for appeal in ds.appeals:
        _, rule = lines.addressee_by_rules(appeal.text)
        if not rule and lines.looks_like_outage(appeal.text):
            rule = "outage"
        if rule:
            hits.setdefault(rule, Counter())[appeal.topic] += 1
    blocking = [a for a in ds.appeals if a.topic == lines.BLOCKING]
    caught = sum(1 for a in blocking if lines.addressee_by_rules(a.text)[1])
    return {
        "hits": {
            rule: {"total": sum(c.values()), "topics": dict(c.most_common(5))} for rule, c in hits.items()
        },
        "blocking_topic_caught": f"{caught} из {len(blocking)}",
    }


def error_examples(probs: Floats, labels: Sequence[str], test: Sequence[Appeal]) -> list[dict[str, Any]]:
    wrong = [i for i, row in enumerate(probs) if labels[int(row.argmax())] != test[i].topic]
    rng = np.random.default_rng(SEED)
    picked = sorted(rng.permutation(len(wrong))[:ERROR_EXAMPLES].tolist()) if wrong else []
    return [
        {
            "text": test[wrong[j]].text[:220],
            "gold": test[wrong[j]].topic,
            "pred": labels[int(probs[wrong[j]].argmax())],
            "proba": round(float(probs[wrong[j]].max()), 3),
        }
        for j in picked
    ]


def line_shares(ds: Dataset, settings: RouteSettings) -> dict[str, Any]:
    """Сколько обращений выгрузки пришлось бы на каждую линию — обоснование состава линий."""
    counts = Counter(target_name(gold_target(a, settings), settings) for a in ds.appeals)
    total = sum(counts.values())
    incidents = Counter(settings.target(a.topic).line for a in ds.appeals if a.incident)
    return {
        "counts": dict(counts.most_common()),
        "shares": {k: round(v / total, 4) for k, v in counts.most_common()},
        "incidents_by_topic_line": {str(k): v for k, v in sorted(incidents.items())},
    }


@dataclass(frozen=True)
class EvalOptions:
    test_share: float = TEST_SHARE
    seed: int = SEED
    emb_train: int = EMB_TRAIN
    emb_test: int = EMB_TEST
    min_topic: int = MIN_TOPIC


def evaluate(
    rows: Sequence[tuple[str, str, str]], cache: EmbeddingCache, options: EvalOptions | None = None
) -> dict[str, Any]:
    """Весь прогон п. 2: данные → отложенная выборка → модели → метрики и примеры ошибок."""
    options = options or EvalOptions()
    settings = RouteSettings.from_dyn(None)
    ds = build_dataset(rows, options.min_topic)
    train, test = split(ds.appeals, options.test_share, options.seed)
    _say(f"данные: {len(ds.appeals)} обращений, обучение {len(train)}, проверка {len(test)}")
    labels = ds.labels
    started = time.perf_counter()
    model = TopicModel.fit([ds.appeals[i].text for i in train], [ds.appeals[i].topic for i in train])
    fit_s = round(time.perf_counter() - started, 1)
    held = [ds.appeals[i] for i in test]
    probs = model.proba_many([a.text for a in held])
    latency = tfidf_latency(model, [a.text for a in held], settings)
    full = _variant("б) TF-IDF — вся выгрузка", probs, Holdout(labels, held, settings), latency["p50_ms"])
    subset = compare_on_subset(ds, (train, test), cache, settings, (options.emb_train, options.emb_test))
    return {
        "data": ds.stats,
        "split": {
            "train": len(train),
            "test": len(test),
            "test_share": options.test_share,
            "seed": options.seed,
        },
        "line_shares": line_shares(ds, settings),
        "full": {**full, "fit_seconds": fit_s, "latency": latency},
        "subset": subset,
        "choice": choose(full, subset),
        "rules": rules_on_data(ds),
        "errors": error_examples(probs, labels, held),
    }


def choose(full: dict[str, Any], subset: dict[str, Any] | None) -> dict[str, Any]:
    """Лучший по macro-F1 среди тех, кто укладывается в 20 мс на обращение."""
    candidates = list(subset["variants"]) if subset else [full]
    fast = [v for v in candidates if v["latency_ms"] is not None and v["latency_ms"] < MAX_LATENCY_MS]
    best = max(fast or candidates, key=lambda v: v["topic_macro_f1"])
    return {"name": best["name"], "macro_f1": best["topic_macro_f1"], "latency_ms": best["latency_ms"]}


# --- порог уверенности в линии ---------------------------------------------------------------

#: Сетка порогов routing.line_min_confidence: 0 — как без порога, дальше шаг 0,05,
#: а там, где точность начинает падать (0,4–0,5), — шаг 0,01.
THRESHOLDS = (0.0, 0.3, 0.35, 0.4, 0.42, 0.44, 0.45, 0.46, 0.47, 0.48, 0.49, 0.5, 0.55, 0.6)
#: Сколько точности линий можно отдать за то, что вопросы не по теме уходят на первую линию.
MAX_ACCURACY_DROP = 0.005


@dataclass(frozen=True)
class Probe:
    """Вопросы не по теме и вероятности тем одной модели на них."""

    probs: Floats
    labels: Sequence[str]


def _share(flags: Sequence[bool]) -> float:
    return round(sum(flags) / len(flags), 4) if flags else 0.0


def threshold_rows(
    held: Holdout, probs: Floats, off_topic: Sequence[str], probes: dict[str, Probe], grid: Sequence[float]
) -> list[dict[str, Any]]:
    """На каждый порог: доля неуверенных, точность линий и сколько вопросов не по теме — на линию 1.

    Точность — тем же путём, что в чате: модель, затем правила регламента и сбои.
    Неуверенное обращение, ушедшее на первую линию, засчитывается верным, только
    если его линия и правда первая, — порог не получает скидки за осторожность.
    """
    gold = [gold_target(a, held.settings) for a in held.test]
    texts = [a.text for a in held.test]
    general = lines.target_key(lines.Target(line=1))
    out: list[dict[str, Any]] = []
    for threshold in grid:
        stage = replace(held.settings, line_min_confidence=threshold)
        pred, unsure = routes(probs, held.labels, texts, stage)
        hits = [p == g for p, g in zip(pred, gold, strict=True)]
        sure = [hit for hit, low in zip(hits, unsure, strict=True) if not low]
        row: dict[str, Any] = {
            "threshold": threshold,
            "below": _share(unsure),
            "line_accuracy": _share(hits),
            "confident_accuracy": _share(sure) if sure else None,
        }
        for name, probe in probes.items():
            keys, _ = routes(probe.probs, probe.labels, off_topic, stage)
            row[f"off_topic_line1_{name}"] = _share([key == general for key in keys])
        out.append(row)
    base = next((r["line_accuracy"] for r in out if r["threshold"] == 0), out[0]["line_accuracy"])
    for row in out:
        row["accuracy_change_pp"] = round((row["line_accuracy"] - base) * 100, 2)
    return out


def choose_threshold(
    rows: Sequence[dict[str, Any]], probe: str, max_drop: float = MAX_ACCURACY_DROP
) -> dict[str, Any]:
    """Больше всего вопросов не по теме на первой линии при потере точности не больше max_drop.

    При равной доле — порог ниже: он реже трогает обычные обращения.
    """
    allowed = [r for r in rows if -r["accuracy_change_pp"] <= max_drop * 100 + 1e-9]
    best = max(allowed, key=lambda r: (r[f"off_topic_line1_{probe}"], -r["threshold"]))
    return {
        "threshold": best["threshold"],
        "probe": probe,
        "max_drop_pp": max_drop * 100,
        "off_topic_majority": best[f"off_topic_line1_{probe}"] > 0.5,  # noqa: PLR2004 — «большинство»
    }


def _off_topic_guesses(probe: Probe, texts: Sequence[str], settings: RouteSettings) -> list[dict[str, Any]]:
    """Куда модель без порога отправила бы каждый вопрос не по теме и насколько уверенно."""
    plain = replace(settings, line_min_confidence=0.0)
    out = []
    for row, text in zip(probe.probs, texts, strict=True):
        pred = route_from_proba(row, probe.labels, plain)
        key = lines.target_key(lines.Target(line=pred.line, addressee=pred.addressee))
        out.append({"text": text, "target": target_name(key, settings), "proba": pred.proba})
    return out


def evaluate_thresholds(
    rows: Sequence[tuple[str, str, str]],
    grid: Sequence[float] = THRESHOLDS,
    options: EvalOptions | None = None,
) -> dict[str, Any]:
    """Замер порога: точность — на отложенной части, вопросы не по теме — на двух моделях.

    Модель на 80 % выгрузки даёт честную точность; модель на всей выгрузке — та,
    что стоит на стенде (команда train), и на вопросах не по теме смотрим обе:
    вопросов про погоду в выгрузке нет, подглядывания здесь не бывает.
    """
    from app.tools.kb_eval import OUT_OF_DOMAIN

    options = options or EvalOptions()
    settings = RouteSettings.from_dyn(None)
    ds = build_dataset(rows, options.min_topic)
    train, test = split(ds.appeals, options.test_share, options.seed)
    _say(f"порог: {len(ds.appeals)} обращений, обучение {len(train)}, проверка {len(test)}")
    model = TopicModel.fit([ds.appeals[i].text for i in train], [ds.appeals[i].topic for i in train])
    held = Holdout(model.labels, [ds.appeals[i] for i in test], settings)
    probs = model.proba_many([a.text for a in held.test])
    _say("порог: модель на всей выгрузке, как на стенде")
    full = TopicModel.fit([a.text for a in ds.appeals], [a.topic for a in ds.appeals])
    off_topic = list(OUT_OF_DOMAIN)
    probes = {
        "holdout": Probe(model.proba_many(off_topic), model.labels),
        "full": Probe(full.proba_many(off_topic), full.labels),
    }
    table = threshold_rows(held, probs, off_topic, probes, grid)
    return {
        "split": {"train": len(train), "test": len(test), "seed": options.seed},
        "off_topic": len(off_topic),
        "rows": table,
        "choice": choose_threshold(table, "full"),
        "guesses": _off_topic_guesses(probes["full"], off_topic, settings),
    }


def render_thresholds(report: dict[str, Any]) -> str:
    choice = report["choice"]
    out = ["# Порог уверенности в линии", ""]
    out += [
        f"Отложенные {report['split']['test']} обращений выгрузки (20 %, seed {report['split']['seed']}) "
        f"и {report['off_topic']} вопросов не про портал (tools/kb_eval.OUT_OF_DOMAIN). Точность — "
        "с правилами регламента и сбоями, как в чате. Ниже порога обращение идёт на линию 1 без темы.",
        "",
    ]
    out += _table(
        [
            "порог",
            "ниже порога",
            "точность линий",
            "изменение, п.п.",
            "точность уверенных",
            "не по теме → линия 1 (модель на 80 %)",
            "не по теме → линия 1 (модель на всей выгрузке)",
        ],
        [
            (
                f"{r['threshold']:.2f}",
                _pct(r["below"]),
                _pct(r["line_accuracy"]),
                f"{r['accuracy_change_pp']:+.2f}",
                _pct(r["confident_accuracy"]),
                _pct(r["off_topic_line1_holdout"]),
                _pct(r["off_topic_line1_full"]),
            )
            for r in report["rows"]
        ],
    )
    majority = "большинство" if choice["off_topic_majority"] else "НЕ большинство"
    out += [
        f"Выбран порог **{choice['threshold']:.2f}**: самая большая доля вопросов не по теме на линии 1 "
        f"(модель на всей выгрузке) при потере точности линий не больше {choice['max_drop_pp']:.1f} п.п.; "
        f"на линии 1 — {majority} вопросов не по теме.",
        "",
        "## Куда без порога ушли вопросы не по теме",
        "",
    ]
    out += _table(
        ["вопрос", "линия без порога", "уверенность"],
        [(g["text"], g["target"], f"{g['proba']:.3f}") for g in report["guesses"]],
    )
    return "\n".join(out)


# --- отчёт -----------------------------------------------------------------------------


def _pct(value: float | None) -> str:
    return "—" if value is None else f"{value * 100:.1f} %"


def _ms(value: float | None) -> str:
    return "не замерено" if value is None else f"{value:.1f} мс"


def _table(header: Sequence[str], rows: Sequence[Sequence[object]]) -> list[str]:
    out = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    out += ["| " + " | ".join(str(c) for c in row) + " |" for row in rows]
    return out + [""]


def _confusion(report: dict[str, Any]) -> list[str]:
    labels = report["labels"]
    short = [str(i + 1) for i in range(len(labels))]
    rows = [
        [f"{i + 1}. {label}", *row]
        for i, (label, row) in enumerate(zip(labels, report["confusion"], strict=True))
    ]
    return _table(["правильная \\ ответ модели", *short], rows)


def render_markdown(report: dict[str, Any]) -> str:
    data, full = report["data"], report["full"]
    out = ["# Кому адресовано обращение: тема и линия на выгрузке СТП", ""]
    out += [
        f"Обращений в выгрузке: {data['rows']}, в оценке: {data['kept']} (тема восстановлена "
        f"по справочнику у {data['repaired_topic']}, отброшено: {data['dropped']}).",
        f"Проверка на отложенных {report['split']['test']} обращениях (20 %, тексты-дубли не "
        "попадают по обе стороны, seed 42).",
        "",
        "## Сколько обращений на каждую линию",
        "",
    ]
    out += _table(
        ["кому", "обращений", "доля"],
        [
            (k, v, _pct(report["line_shares"]["shares"][k]))
            for k, v in report["line_shares"]["counts"].items()
        ],
    )
    if report["subset"]:
        sub = report["subset"]
        out += [
            f"## Сравнение подходов (одна подвыборка: обучение {sub['train']}, проверка {sub['test']})",
            "",
        ]
        out += _table(
            ["подход", "точность темы", "macro-F1 темы", "точность линии", "время на обращение"],
            [
                (
                    v["name"],
                    _pct(v["topic_accuracy"]),
                    _pct(v["topic_macro_f1"]),
                    _pct(v["line_accuracy"]),
                    _ms(v["latency_ms"]),
                )
                for v in sub["variants"]
            ],
        )
    out += [f"Выбран: **{report['choice']['name']}**.", "", "## Модель на всей выгрузке", ""]
    out += _table(
        ["метрика", "значение"],
        [
            ("точность темы", _pct(full["topic_accuracy"])),
            ("macro-F1 по темам", _pct(full["topic_macro_f1"])),
            ("точность линии: только тема", _pct(full["lines"]["topic_only"]["accuracy"])),
            ("точность линии: + адресаты регламента", _pct(full["lines"]["with_rules"]["accuracy"])),
            (
                "точность линии: + сбои на техническую",
                _pct(full["lines"]["with_rules_and_outage"]["accuracy"]),
            ),
            (
                "время на обращение (медиана / 95 %)",
                f"{full['latency']['p50_ms']} / {full['latency']['p95_ms']} мс",
            ),
            ("обучение", f"{full['fit_seconds']} с"),
        ],
    )
    out += [
        "Правила регламента чуть снижают совпадение с разметкой выгрузки, и это ожидаемо: "
        "разметка ставит тему, а не адресата. «Просьба снять блокировку с личного кабинета» "
        "в выгрузке числится за темой «Личный кабинет», а по регламенту её рассматривает СКК; "
        "«портал не работает» при подписании контракта размечено как контракт, а чинит сбой "
        "техническая линия.",
        "",
        "### По темам",
        "",
    ]
    out += _table(
        ["тема", "точность (P)", "полнота (R)", "F1", "обращений"],
        [
            (t, _pct(c["precision"]), _pct(c["recall"]), _pct(c["f1"]), c["support"])
            for t, c in full["topics"]["per_class"].items()
        ],
    )
    out += ["### Матрица ошибок по темам", "", *_confusion(full["topics"])]
    out += ["### Матрица ошибок по линиям", "", *_confusion(full["lines"]["with_rules_and_outage"])]
    out += ["## Правила регламента на выгрузке", ""]
    out += _table(
        ["правило", "срабатываний", "темы этих обращений"],
        [
            (rule, h["total"], ", ".join(f"{t}: {n}" for t, n in h["topics"].items()))
            for rule, h in report["rules"]["hits"].items()
        ],
    )
    out += [f"Обращений темы «Блокировка» правило узнаёт: {report['rules']['blocking_topic_caught']}.", ""]
    out += ["## Примеры ошибок", ""]
    out += _table(
        ["текст", "правильно", "модель", "уверенность"],
        [(e["text"].replace("|", "/"), e["gold"], e["pred"], e["proba"]) for e in report["errors"]],
    )
    return "\n".join(out)


# --- обучение финальной модели --------------------------------------------------------------


async def _bump_version(redis_url: str) -> bool:
    if not redis_url:
        return False
    from redis.asyncio import Redis

    client = Redis.from_url(redis_url)
    try:
        await client.incr(VERSION_KEY)
    finally:
        await client.aclose()
    return True


def train_final(
    rows: Sequence[tuple[str, str, str]], data_dir: Path, source: str, params: TrainParams | None = None
) -> dict[str, Any]:
    """Модель темы на всей выгрузке → DATA_DIR, плюс корпус для фонового дообучения."""
    ds = build_dataset(rows)
    texts, topics = [a.text for a in ds.appeals], [a.topic for a in ds.appeals]
    model = TopicModel.fit(texts, topics, params)
    meta = model.save(data_dir, {"samples": len(texts), "source": source, "topics": ds.stats["topics"]})
    save_corpus(data_dir, list(zip(texts, topics, strict=True)))
    return meta


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0] if __doc__ else "")
    sub = parser.add_subparsers(dest="command", required=True)
    ev = sub.add_parser("eval", help="сравнить модели на отложенной выборке и написать отчёт")
    ev.add_argument("--export", type=Path, required=True)
    ev.add_argument("--out", type=Path, required=True, help="папка для report.json и report.md")
    ev.add_argument("--tei-url", default="", help="TEI для варианта «а» (эмбеддинги BERTA)")
    ev.add_argument("--emb-cache", type=Path, default=None, help="npz-кэш векторов, переживает перезапуск")
    ev.add_argument("--emb-train", type=int, default=EMB_TRAIN)
    ev.add_argument("--emb-test", type=int, default=EMB_TEST)
    th = sub.add_parser(
        "thresholds", help="подобрать порог уверенности в линии (routing.line_min_confidence)"
    )
    th.add_argument("--export", type=Path, required=True)
    th.add_argument("--out", type=Path, required=True, help="папка для thresholds.json и thresholds.md")
    th.add_argument(
        "--grid",
        default=",".join(str(t) for t in THRESHOLDS),
        help="пороги через запятую; 0 — как без порога, от него считается потеря точности",
    )
    tr = sub.add_parser("train", help="обучить модель темы на всей выгрузке и положить в DATA_DIR")
    tr.add_argument("--export", type=Path, required=True)
    tr.add_argument("--data-dir", type=Path, default=None, help="по умолчанию DATA_DIR из настроек")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    rows = read_rows(args.export)
    if args.command == "eval":
        cache = EmbeddingCache(args.emb_cache, args.tei_url)
        report = evaluate(rows, cache, EvalOptions(emb_train=args.emb_train, emb_test=args.emb_test))
        args.out.mkdir(parents=True, exist_ok=True)
        (args.out / "report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (args.out / "report.md").write_text(render_markdown(report), encoding="utf-8")
        _say(f"отчёт: {args.out / 'report.md'}")
        return 0
    if args.command == "thresholds":
        grid = sorted({0.0, *(float(t) for t in args.grid.split(",") if t.strip())})
        report = evaluate_thresholds(rows, grid)
        args.out.mkdir(parents=True, exist_ok=True)
        (args.out / "thresholds.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (args.out / "thresholds.md").write_text(render_thresholds(report), encoding="utf-8")
        _say(f"отчёт: {args.out / 'thresholds.md'}")
        return 0
    from app.config import get_settings

    settings = get_settings()
    data_dir = args.data_dir or settings.data_dir
    meta = train_final(rows, data_dir, args.export.name)
    bumped = asyncio.run(_bump_version(settings.redis_url))
    _say(
        f"модель темы: {meta['samples']} обращений, {len(meta['labels'])} тем → {data_dir}. "
        + ("Инстансы подхватят её сами." if bumped else "Redis не задан (lite): перезапустите api.")
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
