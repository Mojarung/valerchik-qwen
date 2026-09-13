"""Авто-регресс качества: золотой набор, метрики и изоляция от боевых диалогов."""

from sqlalchemy import func, select

from app.models import ChatSession, Message
from app.services import quality


def test_score_requires_every_expected_fragment():
    """Все фрагменты, а не любой: иначе засчитывалось бы «угадал тему, но не сказал главного»."""
    assert quality.score_answer("Регистрация идёт через ЕСИА и Госуслуги", ["есиа", "госуслуг"])
    assert not quality.score_answer("Регистрация идёт через ЕСИА", ["есиа", "снилс"])


def test_score_ignores_case_and_yo():
    """В базе знаний «трёх», в вопросе «трех» — иначе эталон срабатывал бы через раз."""
    assert quality.score_answer("Подписать в течение ТРЁХ рабочих дней", ["трех рабочих"])


def test_golden_set_is_loaded_and_well_formed(client):
    questions = quality.load_questions(client.app.state.rt.dyn)

    assert len(questions) >= 50, "набор должен покрывать базу знаний, а не пару вопросов"
    assert all(q["q"].strip() for q in questions)
    assert all(q.get("expect") for q in questions), "вопрос без эталона ничего не меряет"
    assert len({q["q"] for q in questions}) == len(questions), "дубли искажают метрику"


def test_stp_set_is_chosen_by_setting_and_well_formed(client):
    """Набор по выгрузке СТП подключается настройкой и не попадает в прогон демо-базы."""
    dyn = client.app.state.rt.dyn
    base = quality.load_questions(dyn)
    dyn.override("eval.question_set", "stp")
    try:
        stp = quality.load_questions(dyn)
        dyn.override("eval.question_set", "all")
        both = quality.load_questions(dyn)
        dyn.override("eval.question_set", "нет такого")
        fallback = quality.load_questions(dyn)
    finally:
        dyn.override("eval.question_set", "base")

    assert len(stp) >= 1000, "набор по выгрузке — тысячи реальных обращений"
    assert all(q.get("expect") for q in stp), "вопрос без эталона ничего не меряет"
    assert len({q["q"] for q in stp}) == len(stp), "дубли искажают метрику"
    assert len(both) == len(base) + len(stp)
    assert fallback == base


def test_v2_set_and_sampling(client):
    """v2 — эталоны под боевую базу плюс СТП; sample_every прореживает набор через шаг."""
    dyn = client.app.state.rt.dyn
    stp = None
    dyn.override("eval.question_set", "stp")
    try:
        stp = quality.load_questions(dyn)
        dyn.override("eval.question_set", "v2")
        v2 = quality.load_questions(dyn)
        dyn.override("eval.sample_every", 2)
        half = quality.load_questions(dyn)
    finally:
        dyn.override("eval.question_set", "base")
        dyn.override("eval.sample_every", 1)

    own = v2[: len(v2) - len(stp)]
    assert len(own) >= 40, "темы base, сверенные с боевой базой"
    assert all(q.get("expect") for q in own), "вопрос без эталона ничего не меряет"
    assert len(half) == (len(v2) + 1) // 2
    assert half == v2[::2]


def test_admin_can_extend_the_set_without_touching_the_file(client, admin_headers):
    rt = client.app.state.rt
    before = len(quality.load_questions(rt.dyn))
    client.put(
        "/api/admin/settings",
        headers=admin_headers,
        json={
            "updates": {
                "eval.extra_questions": [{"q": "свой вопрос из практики", "expect": ["портал"]}],
            }
        },
    )
    try:
        assert len(quality.load_questions(rt.dyn)) == before + 1
    finally:
        client.put(
            "/api/admin/settings", headers=admin_headers, json={"updates": {"eval.extra_questions": []}}
        )


async def test_run_does_not_touch_live_dialogs(client):
    """Главное требование к прогону: полсотни служебных вопросов не должны
    осесть в статистике дашборда как реальные обращения людей."""
    rt = client.app.state.rt
    async with rt.db.session_factory() as db:
        sessions_before = (await db.execute(select(func.count(ChatSession.id)))).scalar()
        messages_before = (await db.execute(select(func.count(Message.id)))).scalar()

    report = await quality.run(rt)

    async with rt.db.session_factory() as db:
        assert (await db.execute(select(func.count(ChatSession.id)))).scalar() == sessions_before
        assert (await db.execute(select(func.count(Message.id)))).scalar() == messages_before
    assert report["total"] >= 50


async def test_report_shape_and_metric_bounds(client):
    report = await quality.run(client.app.state.rt)
    metrics = report["metrics"]

    assert set(metrics) == {
        "accuracy",
        "answer_rate",
        "confident_wrong_rate",
        "avg_confidence",
        "median_latency_ms",
    }
    assert 0.0 <= metrics["accuracy"] <= 1.0
    assert 0.0 <= metrics["confident_wrong_rate"] <= 1.0
    # Промахи — то, ради чего отчёт открывают: без них он бесполезен
    assert len(report["misses"]) == min(
        quality.MISSES_LIMIT, report["total"] - round(metrics["accuracy"] * report["total"])
    )
    assert report["by_topic"], "разрез по темам показывает, какой раздел базы знаний просел"


async def test_previous_run_is_kept_for_comparison(client):
    rt = client.app.state.rt
    await quality.run(rt)
    await quality.run(rt)

    reports = await quality.last_reports(rt, limit=2)
    assert len(reports) == 2
    assert reports[0]["generated_at"] >= reports[1]["generated_at"]


def test_endpoints_are_protected(client, operator_headers):
    assert client.get("/api/admin/eval").status_code in (401, 403)
    assert client.get("/api/admin/eval", headers=operator_headers).status_code == 200
    # Прогон стоит денег на LLM — запускать может только админ
    assert client.post("/api/admin/eval/run", headers=operator_headers).status_code in (401, 403)
