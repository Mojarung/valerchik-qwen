"""Инструмент оценки модели темы и линии: данные, честное разбиение, отчёт, обучение — без сети."""

import asyncio
import json

import numpy as np
import openpyxl
import pytest
from fakeredis import aioredis as fake_aioredis

from app.adapters.embeddings import HashEmbedder
from app.services import lines
from app.services.routing import LineClassifier, load_corpus
from app.tools import lines_eval

HEADER = ["Влияние", "Заявитель", "Тема", "Статус", "Описание", "Решение"]
REQUEST = "RFI - запрос информации"
INCIDENT = "Среднее - услуга недоступна для одного пользователя"
PHRASES = {
    "Личный кабинет пользователя": (
        "Регистрация",
        ["как зарегистрироваться на портале", "не могу войти в кабинет"],
    ),
    "Электронное исполнение (ЕИС)": ("Создание исполнения", ["ошибка рдик в упд", "упд не уходит в еис"]),
    "Закупочные процедуры": ("Участие в Котировочной сессии", ["как участвовать в котировочной сессии"]),
    "Работа с контрактами": ("Подписание контракта", ["как подписать контракт", "протокол разногласий"]),
}
WORDS = ["срочно", "подскажите", "помогите", "добрый день", "спасибо", "вопрос", "опять", "снова"]


def _rows() -> list[list[str]]:
    rng = np.random.default_rng(0)
    rows = []
    for topic, (subtopic, phrases) in PHRASES.items():
        for i in range(40):
            phrase = phrases[i % len(phrases)]
            tail = " ".join(rng.choice(WORDS, size=2))
            impact = INCIDENT if topic == "Работа с контрактами" and i < 4 else REQUEST
            rows.append(
                [
                    impact,
                    "Заявитель",
                    f"{topic}." if i % 7 == 0 else topic,
                    "Завершено",
                    f"Подтема запроса: {subtopic}/{phrase} {tail} {i}",
                    "ответ",
                ]
            )
    # шум темы: восстанавливается по подтеме
    rows.append(
        [REQUEST, "З", "ДОП", "Завершено", "Подтема запроса: Подписание контракта/подписать контракт", "о"]
    )
    rows.append(
        [REQUEST, "З", "INTEGRTP-5", "Завершено", "Подтема запроса: Создание исполнения/ошибка рдик", "о"]
    )
    rows.append([REQUEST, "З", "М", "Завершено", "текст без темы", "о"])
    # редкая тема — в модель не идёт
    rows += [
        [REQUEST, "З", "Уполномоченный орган (Региональный заказчик)", "Завершено", "статистика региона", "о"]
    ]
    # дубли: одинаковый текст обязан целиком оказаться по одну сторону разбиения
    rows += [
        [REQUEST, "З", "Работа с контрактами", "Завершено", "Подтема запроса: Консультация/дубль", "о"]
    ] * 6
    return rows


@pytest.fixture(scope="module")
def export(tmp_path_factory):
    path = tmp_path_factory.mktemp("export") / "Выгрузка.xlsx"
    book = openpyxl.Workbook()
    sheet = book.active
    sheet.append(HEADER)
    for row in _rows():
        sheet.append(row)
    book.save(path)
    return path


def test_dataset_normalizes_topics_and_drops_noise(export):
    ds = lines_eval.build_dataset(lines_eval.read_rows(export))
    stats = ds.stats
    assert stats["rows"] == len(_rows())
    assert set(stats["topics"]) == set(PHRASES)
    assert stats["dropped"]["тема не восстанавливается"] == 1
    assert stats["dropped"]["мало примеров: Уполномоченный орган (Региональный заказчик)"] == 1
    assert stats["repaired_topic"] >= 2  # «ДОП» и INTEGRTP-… вернулись к справочнику
    assert stats["incidents"] == 4
    assert all("Подтема запроса" not in a.text for a in ds.appeals)


def test_split_is_stratified_and_keeps_duplicates_together(export):
    ds = lines_eval.build_dataset(lines_eval.read_rows(export))
    train, test = lines_eval.split(ds.appeals)
    assert not set(train) & set(test) and len(train) + len(test) == len(ds.appeals)
    assert {ds.appeals[i].topic for i in test} == set(PHRASES)
    assert 0.1 < len(test) / len(ds.appeals) < 0.3
    seen_train = {ds.appeals[i].text for i in train}
    assert not seen_train & {ds.appeals[i].text for i in test}
    assert lines_eval.split(ds.appeals) == (train, test)  # seed 42 — воспроизводимо


def test_classification_report_counts():
    report = lines_eval.classification_report(["a", "a", "b", "b"], ["a", "b", "b", "b"], ["a", "b"])
    assert report["accuracy"] == 0.75
    assert report["confusion"] == [[1, 1], [0, 2]]
    assert report["per_class"]["a"] == {"precision": 1.0, "recall": 0.5, "f1": 0.6667, "support": 2}
    assert report["macro_f1"] == pytest.approx((0.6667 + 0.8) / 2, abs=1e-3)


def test_gold_line_counts_incident_as_technical():
    settings = lines_eval.RouteSettings.from_dyn(None)
    contract = lines_eval.Appeal(text="x", topic=lines.CONTRACTS, incident=False)
    assert lines_eval.gold_target(contract, settings) == "line:3"
    assert lines_eval.gold_target(lines_eval.Appeal("x", lines.CONTRACTS, True), settings) == "line:2"
    assert lines_eval.gold_target(lines_eval.Appeal("x", lines.BLOCKING, True), settings) == lines.SKK


def test_evaluate_compares_three_approaches_offline(export):
    rows = lines_eval.read_rows(export)
    cache = lines_eval.EmbeddingCache(None, "")
    ds = lines_eval.build_dataset(rows)
    vectors = asyncio.run(HashEmbedder().embed([a.text for a in ds.appeals]))
    for appeal, vec in zip(ds.appeals, vectors, strict=True):
        cache.vectors[cache.key(appeal.text)] = np.array(vec, dtype=np.float32)

    report = lines_eval.evaluate(rows, cache, lines_eval.EvalOptions(emb_train=80, emb_test=30))

    names = [v["name"][:2] for v in report["subset"]["variants"]]
    assert names == ["а)", "б)", "в)"]
    assert 0.0 <= report["subset"]["alpha"] <= 1.0
    full = report["full"]
    assert full["topic_accuracy"] >= 0.9 and full["line_accuracy"] >= 0.8
    assert full["latency"]["p50_ms"] < lines_eval.MAX_LATENCY_MS
    assert set(full["lines"]) == {"topic_only", "with_rules", "with_rules_and_outage"}
    assert report["choice"]["name"].startswith("б)")  # у варианта «а» без TEI время не замерено
    assert "Линия 3 — Закупки и контракты" in report["line_shares"]["counts"]
    markdown = lines_eval.render_markdown(report)
    assert "Сравнение подходов" in markdown and "Матрица ошибок по темам" in markdown


def test_embeddings_missing_without_tei_skip_comparison(export, tmp_path):
    out = tmp_path / "report"
    assert lines_eval.main(["eval", "--export", str(export), "--out", str(out)]) == 0
    report = json.loads((out / "report.json").read_text(encoding="utf-8"))
    assert report["subset"] is None and report["full"]["topic_macro_f1"] > 0
    assert (out / "report.md").read_text(encoding="utf-8").startswith("# Кому адресовано обращение")


def test_thresholds_command_measures_accuracy_and_off_topic(export, tmp_path):
    out = tmp_path / "thresholds"
    code = lines_eval.main(["thresholds", "--export", str(export), "--out", str(out), "--grid", "0.5,0.9"])
    assert code == 0
    report = json.loads((out / "thresholds.json").read_text(encoding="utf-8"))
    rows = {r["threshold"]: r for r in report["rows"]}
    assert set(rows) == {0.0, 0.5, 0.9}  # 0 добавляется сам: от него считается потеря точности
    assert rows[0.0]["below"] == 0 and rows[0.0]["accuracy_change_pp"] == 0
    assert rows[0.0]["below"] <= rows[0.5]["below"] <= rows[0.9]["below"]
    assert rows[0.9]["off_topic_line1_full"] >= rows[0.0]["off_topic_line1_full"]
    assert report["choice"]["threshold"] in rows
    assert len(report["guesses"]) == report["off_topic"] == 40
    assert (out / "thresholds.md").read_text(encoding="utf-8").startswith("# Порог уверенности в линии")


def test_threshold_choice_keeps_line_accuracy():
    rows = [
        {"threshold": 0.0, "accuracy_change_pp": 0.0, "off_topic_line1_full": 0.3},
        {"threshold": 0.4, "accuracy_change_pp": -0.2, "off_topic_line1_full": 0.6},
        {"threshold": 0.45, "accuracy_change_pp": -0.4, "off_topic_line1_full": 0.6},
        {"threshold": 0.5, "accuracy_change_pp": -1.0, "off_topic_line1_full": 0.9},
    ]
    choice = lines_eval.choose_threshold(rows, "full")
    # 0,5 теряет целый процент; из равных по вопросам не по теме — тот, что ниже
    assert choice["threshold"] == 0.4 and choice["off_topic_majority"]


def test_embedding_cache_survives_restart(tmp_path):
    path = tmp_path / "emb.npz"
    cache = lines_eval.EmbeddingCache(path, "")
    cache.vectors[cache.key("текст")] = np.ones(3, dtype=np.float32)
    cache._save()
    again = lines_eval.EmbeddingCache(path, "")
    assert again.embed(["текст"]).shape == (1, 3)
    assert again.embed(["другой текст"]) is None  # недостающее без TEI не выдумываем


async def test_train_command_puts_model_where_instances_find_it(export, tmp_path, monkeypatch):
    async def no_redis(url: str) -> bool:
        return False

    monkeypatch.setattr(lines_eval, "_bump_version", no_redis)
    data_dir = tmp_path / "data"
    code = await asyncio.to_thread(
        lines_eval.main, ["train", "--export", str(export), "--data-dir", str(data_dir)]
    )
    assert code == 0
    assert len(load_corpus(data_dir)) == len(lines_eval.build_dataset(lines_eval.read_rows(export)).appeals)

    clf = LineClassifier(HashEmbedder(), data_dir, fake_aioredis.FakeRedis(decode_responses=True))
    pred = await clf.predict("как подписать контракт срочно")
    assert (pred.source, pred.topic, pred.line) == ("topics", "Работа с контрактами", 3)
    assert clf.topics_meta()["source"] == "Выгрузка.xlsx"
