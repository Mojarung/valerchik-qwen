"""Линии и адресаты: справочник тем, правила регламента, модель темы и её горячая подмена."""

from types import SimpleNamespace

import numpy as np
import pytest
from fakeredis import aioredis as fake_aioredis

from app.adapters.embeddings import HashEmbedder
from app.chat import operator_flow
from app.dynamic_settings import SETTINGS
from app.services import lines
from app.services.routing import (
    TOPICS_NPZ,
    LineClassifier,
    RoutePrediction,
    RouteSettings,
    TopicModel,
    apply_rules,
    route_from_proba,
    save_corpus,
)

PHRASES = {
    lines.CABINET: [
        "как зарегистрироваться на портале поставщиков",
        "не могу войти в личный кабинет пароль",
        "добавить сотрудника и выдать полномочия",
        "восстановить доступ к личному кабинету",
    ],
    lines.EXECUTION_EIS: [
        "ошибка рдик при формировании упд",
        "упд не отправляется в еис статус исполнения",
        "создание исполнения контракта в еис",
        "исполнение зависло в статусе отправка в еис",
    ],
    lines.CONTRACTS: [
        "подписать контракт электронной подписью",
        "документ контракта не отображается",
        "дата заключения контракта неверная",
        "протокол разногласий к контракту",
    ],
    lines.BLOCKING: [
        "почему личный кабинет заблокирован",
        "когда снимут блокировку профиля",
        "компания исключена из рнп снимите блокировку",
        "срок блокировки истек",
    ],
}
TAILS = ["", "подскажите пожалуйста", "срочно", "что делать", "добрый день", "спасибо"]


def _corpus() -> tuple[list[str], list[str]]:
    texts, topics = [], []
    for topic, phrases in PHRASES.items():
        for phrase in phrases:
            for tail in TAILS:
                texts.append(f"{phrase} {tail}".strip())
                topics.append(topic)
    return texts, topics


class _Dyn:
    """Настройки как в админке: по умолчанию, с правками поверх."""

    def __init__(self, **overrides):
        self.values = {key.replace("__", "."): value for key, value in overrides.items()}

    def get(self, key, default=None):
        return self.values.get(key, SETTINGS[key].default if key in SETTINGS else default)


@pytest.fixture(scope="module")
def topic_model() -> TopicModel:
    texts, topics = _corpus()
    return TopicModel.fit(texts, topics)


# --- справочник и чистка текста ---------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "subtopic", "expected"),
    [
        ("Работа с СТЕ.", "", lines.STE),
        ("  личный кабинет пользователя ", "", lines.CABINET),
        ("INTEGRTP-57973 Электронное исполнение (малый объем)", "", lines.EXECUTION_SMALL),
        ("ОШ Личный кабинет пользователя", "", lines.CABINET),
        ("Консультация - Контракты (44-ФЗ)", "", lines.CONTRACTS),
        ("ДОП", "Подписание контракта", lines.CONTRACTS),
        ("М", "Отправка УПД и статус исполнения (ошибки интеграции)", lines.EXECUTION_EIS),
        # Подтема надёжнее синонима: «Контракты» с подтемой УПД — это исполнение
        ("Контракты (44-ФЗ)", "Подписание и отправка УПД", lines.EXECUTION_SMALL),
        ("Автоставка", "", lines.PROCUREMENT),
        ("ДОП", "Консультация", None),  # подтема у пяти тем — не угадать
        ("М", "", None),
        ("INTEGRTP-58506", "", None),
    ],
)
def test_normalize_topic(raw, subtopic, expected):
    assert lines.normalize_topic(raw, subtopic) == expected


def test_normalize_topic_uses_learned_subtopics():
    learned = {lines.topic_key("Исполнение контракта"): lines.CONTRACTS}
    assert lines.normalize_topic("ДОП", "Исполнение контракта") is None
    assert lines.normalize_topic("ДОП", "Исполнение контракта", learned) == lines.CONTRACTS


def test_appeal_text_drops_subtopic_markup_and_portal_hint():
    subtopic, text = lines.appeal_text("Подтема запроса: Регистрация/<p>Не могу  <c #1a1a1a>войти</c></p>")
    assert (subtopic, text) == ("Регистрация", "Не могу войти")

    report = (
        "Подтема запроса: Формирование УПД (вопросы по ошибкам РДИК)/помогите. ID запроса поддержки: "
        "e15024d2,Текст ошибки: РДИК_0161 - ошибка с контрактом,ID шаблона: ee6e,Решение: <p>Ошибка "
        "является предупреждающей</p>"
    )
    _, text = lines.appeal_text(report)
    assert "РДИК_0161" in text and "предупреждающей" not in text  # подсказка портала — не вопрос

    assert lines.appeal_text("Портал не открывается. С уважением, Иванов")[1] == "Портал не открывается."


def test_incident_is_everything_but_requests():
    assert lines.is_incident("Среднее - услуга недоступна для одного пользователя")
    assert not lines.is_incident("RFI - запрос информации")
    assert not lines.is_incident("RFC - запрос на изменение")
    assert not lines.is_incident("")


# --- правила регламента -------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "audience", "expected"),
    [
        ("Почему заблокирован личный кабинет?", "", (lines.SKK, "blocking")),
        ("Срок блокировки истёк, а ограничения не сняли", "", (lines.SKK, "blocking")),
        ("Ограничения на профиле! Просьба снять ограничения", "", (lines.SKK, "blocking")),
        ("Как обжаловать блокировку кабинета?", "", (lines.ARBITRATION, "appeal_blocking")),
        ("Не могу подать жалобу в Арбитражную комиссию", "", (lines.ARBITRATION, "appeal_blocking")),
        ("Прошу снять котировочную сессию с публикации", "", (lines.EAIST, "withdraw_moscow")),
        ("Прошу снять КС с публикации", "customer", (lines.EAIST, "withdraw_moscow")),
        ("Просим снять КС с публикации, в ТЗ ошибка", "supplier", (lines.SKK, "withdraw_by_supplier")),
        ("Мы региональный заказчик, как снять КС с публикации?", "", (lines.LINE, "withdraw_regional")),
        # не правило, а обычный вопрос линии
        ("Поле ИНН заблокировано для редактирования", "", ("", "")),
        ("Профиль зарегистрирован, ограничений нет, а кнопки нет", "", ("", "")),
        ("Почему котировочная сессия снята с публикации?", "", ("", "")),
        ("Прошу пояснить причины снятия с публикации КС", "", ("", "")),
        ("Как подписать контракт?", "", ("", "")),
    ],
)
def test_addressee_rules(text, audience, expected):
    assert lines.addressee_by_rules(text, audience) == expected


def test_outage_words():
    assert lines.looks_like_outage("Портал не работает с утра")
    assert lines.looks_like_outage("При отправке УПД ошибка 500")
    assert lines.looks_like_outage("белый экран при подписании")
    assert not lines.looks_like_outage("Не работает кнопка подписать контракт")


def test_topic_targets_accept_lines_and_addressees():
    addressees = SETTINGS["routing.addressees"].default
    targets = lines.topic_targets(
        {"Работа с СТЕ.": "1", "Блокировка личного кабинета": "skk", "X": "?"}, addressees
    )
    assert targets[lines.STE] == lines.Target(line=1)
    assert targets[lines.BLOCKING] == lines.Target(line=1, addressee=lines.SKK)
    assert targets["X"] == lines.Target(line=1)  # непонятное значение — первая линия, а не падение
    assert lines.transfer_notice("линия {line} — {line_label} ({topic}).", 2, "Техподдержка", "") == (
        "линия 2 — Техподдержка."
    )


def test_every_reference_topic_has_a_line():
    """Таблица по умолчанию покрывает весь справочник, а адресаты в ней — описаны."""
    table = SETTINGS["routing.topic_lines"].default
    addressees = SETTINGS["routing.addressees"].default
    assert set(table) == set(lines.TOPICS)
    for value in table.values():
        assert value.isdigit() or value in addressees
    for spec in addressees.values():
        assert spec["label"] and spec["how"] and spec["line"] in (1, 2, 3)


# --- от вероятностей к линии ------------------------------------------------------------


def test_route_sums_topics_of_one_line():
    settings = RouteSettings.from_dyn(None)
    labels = [lines.EXECUTION_EIS, lines.EXECUTION_SMALL, lines.CONTRACTS]
    pred = route_from_proba(np.array([0.3, 0.3, 0.4], dtype=np.float32), labels, settings)
    # две темы технической линии вместе увереннее одной темы третьей
    assert (pred.line, pred.proba, pred.source) == (2, 0.6, "topics")
    assert pred.topic == ""  # сама тема неуверенная — её не называем


def test_external_addressee_only_when_confident():
    settings = RouteSettings.from_dyn(None)
    labels = [lines.BLOCKING, lines.CABINET, lines.CONTRACTS]
    unsure = route_from_proba(np.array([0.38, 0.32, 0.30], dtype=np.float32), labels, settings)
    assert (unsure.line, unsure.external) == (1, False)
    sure = route_from_proba(np.array([0.7, 0.2, 0.1], dtype=np.float32), labels, settings)
    assert sure.external and sure.addressee == lines.SKK and sure.topic == lines.BLOCKING
    assert "контроля качества" in sure.addressee_label


def _guarded(threshold: float) -> RouteSettings:
    return RouteSettings.from_dyn(_Dyn(routing__line_min_confidence=threshold))


LINE_LABELS = [lines.CONTRACTS, lines.PROCUREMENT, lines.EXECUTION_EIS, lines.CABINET]


def test_unsure_line_goes_to_consultations_without_topic():
    """«Какая завтра погода»: догадка «линия 3» с уверенностью 0,43 — не адресат."""
    probs = np.array([0.25, 0.18, 0.30, 0.27], dtype=np.float32)  # линия 3 — 0,43
    pred = route_from_proba(probs, LINE_LABELS, _guarded(0.45))
    assert (pred.line, pred.topic, pred.source, pred.external) == (1, "", "unsure", False)
    assert pred.addressee_label == "Консультации"
    assert pred.proba == pytest.approx(0.43, abs=1e-3)  # в трассе видно, насколько слабой была догадка
    plain = route_from_proba(probs, LINE_LABELS, _guarded(0.0))  # 0 — прежний выбор по сумме тем
    assert (plain.line, plain.source) == (3, "topics")


def test_confident_line_is_unchanged_by_threshold():
    probs = np.array([0.55, 0.2, 0.15, 0.1], dtype=np.float32)
    sure = route_from_proba(probs, LINE_LABELS, _guarded(0.45))
    assert sure == route_from_proba(probs, LINE_LABELS, _guarded(0.0))
    assert (sure.line, sure.topic, sure.source) == (3, lines.CONTRACTS, "topics")
    # уверенный внешний адресат по модели — тоже как раньше
    blocking = route_from_proba(
        np.array([0.7, 0.2, 0.1], dtype=np.float32),
        [lines.BLOCKING, lines.CABINET, lines.CONTRACTS],
        _guarded(0.45),
    )
    assert blocking.addressee == lines.SKK and blocking.topic == lines.BLOCKING


def test_rules_and_outage_still_win_over_unsure_line():
    """Порог решает только за модель: норма регламента и сбой — не догадка."""
    settings = _guarded(0.9)
    probs = np.array([0.55, 0.2, 0.15, 0.1], dtype=np.float32)
    text = "Нас заблокировали, не можем подписать контракт"
    unsure = route_from_proba(probs, LINE_LABELS, settings)
    assert unsure.source == "unsure"
    blocked = apply_rules(unsure, text, "", settings)
    assert (blocked.addressee, blocked.source, blocked.rule) == (lines.SKK, "rules", "blocking")
    outage = apply_rules(unsure, "Портал не работает, ошибка 500 при подписании контракта", "", settings)
    assert (outage.line, outage.rule, outage.topic) == (2, "outage", "")


def test_new_threshold_has_default_from_code():
    """На стенде таблица настроек заполнена при первом старте: новый ключ берёт умолчание из кода."""
    default = SETTINGS["routing.line_min_confidence"].default
    assert isinstance(default, float) and 0.0 < default < 1.0
    assert RouteSettings.from_dyn(None).line_min_confidence == default
    assert RouteSettings.from_dyn(_Dyn()).line_min_confidence == default


def test_rules_override_model():
    settings = RouteSettings.from_dyn(None)
    base = RoutePrediction(line=3, proba=0.8, source="topics", topic=lines.CONTRACTS, topic_proba=0.8)
    blocked = apply_rules(base, "Нас заблокировали, не можем подписать контракт", "", settings)
    assert (blocked.addressee, blocked.source, blocked.rule, blocked.line) == (
        lines.SKK,
        "rules",
        "blocking",
        1,
    )
    regional = apply_rules(base, "Региональный заказчик: снимите КС с публикации", "", settings)
    assert (regional.line, regional.topic, regional.external) == (3, lines.PROCUREMENT, False)
    outage = apply_rules(base, "Портал не работает, ошибка 500 при подписании контракта", "", settings)
    assert (outage.line, outage.rule, outage.topic) == (2, "outage", lines.CONTRACTS)
    quiet = RouteSettings.from_dyn(
        _Dyn(routing__outage_rule_enabled=False, routing__addressee_rules_enabled=False)
    )
    assert apply_rules(base, "Портал не работает, нас заблокировали", "", quiet) is base


# --- модель темы и LineClassifier --------------------------------------------------------


def test_topic_model_learns_and_roundtrips(tmp_path, topic_model):
    probs = topic_model.proba("не могу подписать контракт электронной подписью")
    assert topic_model.labels[int(probs.argmax())] == lines.CONTRACTS
    topic_model.save(tmp_path)
    loaded = TopicModel.load(tmp_path)
    assert loaded is not None and loaded.labels == topic_model.labels
    again = loaded.proba("не могу подписать контракт электронной подписью")
    assert np.allclose(probs, again, atol=1e-2)  # веса хранятся в float16
    batch = loaded.proba_many(["ошибка рдик в упд", ""])
    assert loaded.labels[int(batch[0].argmax())] == lines.EXECUTION_EIS
    assert np.allclose(batch[1], 1 / len(loaded.labels))  # пустой текст — равные шансы, не падение


def test_broken_artifact_is_ignored(tmp_path, topic_model):
    topic_model.save(tmp_path)
    (tmp_path / TOPICS_NPZ).write_bytes(b"not a zip")
    assert TopicModel.load(tmp_path) is None


async def test_predict_is_backward_compatible_without_topic_model(tmp_path):
    clf = LineClassifier(HashEmbedder(), tmp_path, fake_aioredis.FakeRedis(decode_responses=True))
    pred = await clf.predict("вопрос по котировочной сессии")
    assert (pred.line, pred.source) == (3, "keywords")
    assert pred.topic == "" and pred.addressee == lines.LINE and not pred.external
    assert pred.addressee_label == "Закупки и контракты"
    # правила регламента работают и без модели
    ruled = await clf.predict("личный кабинет заблокирован")
    assert (ruled.source, ruled.addressee) == ("rules", lines.SKK)


async def test_topic_model_hot_swaps_into_running_instance(tmp_path):
    cache = fake_aioredis.FakeRedis(decode_responses=True)
    running = LineClassifier(HashEmbedder(), tmp_path, cache)
    assert (await running.predict("ошибка рдик при формировании упд")).source != "topics"

    trainer = LineClassifier(HashEmbedder(), tmp_path, cache)  # другой инстанс, например воркер
    texts, topics = _corpus()
    meta = await trainer.train_topics(texts, topics, extra={"source": "test"})
    assert meta["samples"] == len(texts) and meta["source"] == "test"
    assert trainer.topics_meta()["labels"] == sorted(PHRASES)

    pred = await running.predict("ошибка рдик при формировании упд")
    assert (pred.source, pred.topic, pred.line) == ("topics", lines.EXECUTION_EIS, 2)
    assert pred.addressee_label == "Техническая поддержка"


async def test_topic_lines_from_admin_apply(tmp_path):
    cache = fake_aioredis.FakeRedis(decode_responses=True)
    clf = LineClassifier(HashEmbedder(), tmp_path, cache)
    texts, topics = _corpus()
    await clf.train_topics(texts, topics)
    text = "подписать контракт электронной подписью"
    assert (await clf.predict(text)).line == 3
    moved = _Dyn(routing__topic_lines={**SETTINGS["routing.topic_lines"].default, lines.CONTRACTS: "2"})
    assert (await clf.predict(text, dyn=moved)).line == 2
    off = _Dyn(routing__topic_model_enabled=False)
    assert (await clf.predict(text, dyn=off)).source != "topics"


async def test_off_topic_question_goes_to_first_line(tmp_path):
    clf = LineClassifier(HashEmbedder(), tmp_path, fake_aioredis.FakeRedis(decode_responses=True))
    texts, topics = _corpus()
    await clf.train_topics(texts, topics)
    guarded = _Dyn(routing__line_min_confidence=0.6)
    weather = await clf.predict("какая завтра погода в нижнем новгороде", dyn=guarded)
    assert (weather.line, weather.topic, weather.source) == (1, "", "unsure")
    sure = await clf.predict("ошибка рдик при формировании упд", dyn=guarded)
    assert (sure.line, sure.topic, sure.source) == (2, lines.EXECUTION_EIS, "topics")


async def test_retrain_task_adds_kb_articles_to_corpus(tmp_path):
    from app.tasks.jobs import _retrain_topics

    clf = LineClassifier(HashEmbedder(), tmp_path, fake_aioredis.FakeRedis(decode_responses=True))
    rt = SimpleNamespace(classifier=clf)
    article = SimpleNamespace(
        question="как выгрузить yml каталог", keywords=["yml"], category="Работа с СТЕ."
    )
    skipped = await _retrain_topics(rt, [article])
    assert skipped["skipped"], "без корпуса выгрузки модель темы не дообучается"

    texts, topics = _corpus()
    save_corpus(tmp_path, list(zip(texts, topics, strict=True)))
    meta = await _retrain_topics(rt, [article, SimpleNamespace(question="x", keywords=[], category="Прочее")])
    assert meta["corpus"] == len(texts) and meta["kb_articles"] == 1
    assert lines.STE in meta["labels"]  # тема пришла из статьи БЗ


# --- что видит человек ------------------------------------------------------------------


def _svc(**overrides):
    return SimpleNamespace(rt=SimpleNamespace(dyn=_Dyn(**overrides)))


def test_transfer_notice_names_line_and_topic():
    route = RoutePrediction(line=2, proba=0.9, source="topics", topic=lines.EXECUTION_EIS)
    text = operator_flow._route_notice(_svc(), 2, route)
    assert text == "Направление обращения: Техническая поддержка (Электронное исполнение (ЕИС))."
    # линию выбрал не классификатор (принятое предложение) — чужую тему не подписываем
    assert (
        operator_flow._route_notice(_svc(), 3, route) == "Направление обращения: Закупки и контракты."
    )
    assert operator_flow._route_notice(_svc(routing__transfer_notice=""), 2, route) == ""


def test_unsure_route_notice_has_no_topic():
    route = RoutePrediction(line=1, proba=0.43, source="unsure", addressee_label="Консультации")
    assert operator_flow._route_notice(_svc(), 1, route) == "Направление обращения: Консультации."


def test_external_notice_explains_where_to_go():
    route = RoutePrediction(
        line=1,
        proba=1.0,
        source="rules",
        addressee=lines.ARBITRATION,
        addressee_label="Арбитражная комиссия портала",
    )
    text = operator_flow._route_notice(_svc(), 1, route)
    assert text.startswith("Ваш вопрос решает Арбитражная комиссия портала.")
    assert "«Жалобы на блокировку»" in text and "10 рабочих дней" in text
    assert "Интеграции с этим адресатом пока нет" in text
    assert "передано" not in text.casefold()


def _escalated(client, text: str) -> list[dict]:
    sid = client.post("/api/chat/sessions").json()["session_id"]
    resp = client.post(f"/api/chat/sessions/{sid}/messages", json={"text": text})
    assert resp.json()["escalated"] is not None, resp.text
    return client.get(f"/api/chat/sessions/{sid}/messages").json()


def test_user_sees_whom_the_appeal_goes_to(client):
    history = _escalated(client, "Позовите живого оператора, у меня не работает ЭЦП")
    notice = [m for m in history if m["role"] == "system" and m["meta"].get("kind") == "routing"]
    assert len(notice) == 1
    assert notice[0]["content"].startswith("Обращение принято в очередь:")
    assert notice[0]["meta"]["addressee"] == lines.LINE


def test_user_is_told_about_quality_service_for_blocking(client):
    history = _escalated(client, "Позовите живого оператора: наш личный кабинет заблокирован")
    [notice] = [m for m in history if m["meta"].get("kind") == "routing"]
    assert notice["meta"]["addressee"] == lines.SKK
    assert "Служба контроля качества" in notice["content"] and "форму обратной связи" in notice["content"]


# --- кому адресовано: подписи для шапки чата (п. 4 ТЗ) -----------------------------------


def test_route_labels_name_line_or_external_addressee():
    specs = {"2": {"label": "Техническая поддержка"}}
    addressees = {lines.SKK: {"label": "Служба контроля качества портала (СКК)", "line": 1}}
    assert lines.route_labels(specs, addressees, 2) == {
        "line_label": "Техническая поддержка",
        "addressee_label": "",
    }
    assert lines.route_labels(specs, addressees, 1, lines.SKK)["addressee_label"] == (
        "Служба контроля качества портала (СКК)"
    )
    # адресата убрали из настроек — вопрос снова решает наша линия
    assert lines.route_labels(specs, {}, 1, lines.EAIST)["addressee_label"] == ""


def test_escalation_event_and_notice_carry_addressee_words(client, monkeypatch):
    """Шапка чата называет адресата словами — подписи едут и в событии, и в сообщении."""
    ws = client.app.state.rt.ws
    events: list[dict] = []
    publish = ws.publish

    async def recording(room: str, event: dict) -> None:
        events.append(event)
        await publish(room, event)

    monkeypatch.setattr(ws, "publish", recording)
    history = _escalated(client, "Позовите живого оператора: наш личный кабинет заблокирован")

    [notice] = [m for m in history if m["meta"].get("kind") == "routing"]
    assert notice["meta"]["addressee_label"] == "Служба контроля качества портала (СКК)"
    assert notice["meta"]["line_label"] == "Консультации"


def test_resumed_dialog_still_names_the_addressee(client):
    """После перезагрузки страницы шапка называет того же адресата, что и до неё."""
    sid = client.post("/api/chat/sessions").json()["session_id"]
    text = "Позовите живого оператора: наш личный кабинет заблокирован"
    reply = client.post(f"/api/chat/sessions/{sid}/messages", json={"text": text}).json()
    assert reply["escalated"] is not None

    state = client.get(f"/api/chat/sessions/{sid}").json()
    assert state["addressee_label"] == "Служба контроля качества портала (СКК)"
    assert state["line_label"] == "Консультации"
