"""Офлайн-анализ выгрузки СТП: вид ответа, темы, частые проблемы, массовые сбои.

В выгрузке нет оценок — качество донесения информации меряется по тексту ответа.
Здесь правила разбора проверяются на коротких примерах в духе реальной выгрузки.
"""

import json

import pandas as pd
import pytest
from sqlalchemy import select

from app.db import Database
from app.models import AnalyticsReport
from app.tools import support_quality as sq

MASS_ANSWER = (
    "По данной проблеме заведена массовая задача 33550005 . Со стороны оператора ЭДО ведутся работы "
    "по восстановлению. В течение суток рекомендуем отправлять УПД на подписание."
)
INSTRUCTION = (
    "Для подписания контракта перейдите в раздел «Мои контракты», откройте карточку контракта и нажмите "
    "«Подписать». Если кнопка неактивна, проверьте срок действия сертификата электронной подписи."
)
REDIRECT = "По данному вопросу необходимо обратиться в службу технической поддержки ГИС ЕИС."
STATUS = "На момент проверки сведений УПД в статусе «Подписано заказчиком», исполнение завершено."


@pytest.mark.parametrize(
    ("answer", "kind"),
    [
        ("Решено", sq.SHORT),
        ("Выдано полномочие.", sq.SHORT),
        ("Просьба предоставить ссылку на исполнение.", sq.ASK),
        (MASS_ANSWER, sq.MASS),
        (REDIRECT, sq.REDIRECT),
        (
            "Рекомендуем обратиться к заказчику для перенаправления дополнительного соглашения на подпись.",
            sq.REDIRECT,
        ),
        (INSTRUCTION, sq.INSTRUCTION),
        (STATUS, sq.STATUS),
    ],
)
def test_answer_kind(answer, kind):
    assert sq.answer_kind(answer) == kind


def test_mentioning_another_system_is_not_a_redirect():
    # Объяснение причины со словом «ЕАИСТ» — не отписка «обратитесь в ЕАИСТ»
    answer = (
        "Цены поступают из системы заказчика ЕАИСТ. Заказчику следует перерегистрировать контракт, "
        "после этого корректные данные поступят на Портал поставщиков автоматически, и можно будет "
        "сформировать исполнение заново."
    )
    assert sq.answer_kind(answer) != sq.REDIRECT


def test_instruction_with_an_address_at_the_end_stays_an_instruction():
    answer = INSTRUCTION + " По вопросам изменения условий контракта обратитесь к заказчику."
    assert sq.answer_kind(answer) == sq.INSTRUCTION


def test_conditional_request_is_not_an_ask_back():
    answer = (
        "Рекомендуется повторить операцию прикрепления МЧД через раздел «Доверенности». "
        "В случае возобновления ошибки просьба прислать полноэкранный скриншот ошибки."
    )
    assert sq.answer_kind(answer) == sq.INSTRUCTION


def test_mass_task_number():
    assert sq.mass_number(MASS_ANSWER) == "33550005"
    assert sq.mass_number("Массовая задача № 32536739 зафиксирована.") == "32536739"
    assert sq.mass_number(INSTRUCTION) is None


def _record(topic, subtopic, text, answer, impact="RFI - запрос информации"):
    return {
        "Влияние": impact,
        "Заявитель": "Заявитель",
        "Тема": topic,
        "Статус": "Завершено",
        "Описание": f"Подтема запроса: {subtopic}/{text}",
        "Решение": answer,
    }


RECORDS = (
    [_record("Работа с контрактами", "Подписание контракта", "Не могу подписать контракт", INSTRUCTION)] * 4
    + [_record("Работа с контрактами.", "Подписание контракта", "Как подписать контракт?", "Решено")] * 4
    + [_record("Электронное исполнение (ЕИС)", "Подписание и отправка УПД", "Не уходит УПД", MASS_ANSWER)] * 3
    + [_record("ДОП", "Прочее", "Вопрос не по теме портала", REDIRECT, impact="Высокое - услуга недоступна")]
)


def test_parse_rows_uses_taxonomy_to_drop_noise_topics():
    rows = sq.parse_rows(RECORDS, taxonomy=["Работа с контрактами", "Электронное исполнение (ЕИС)"])
    topics = {r.topic for r in rows}
    # «Работа с контрактами.» с точкой — та же тема, «ДОП» — шум
    assert topics == {"Работа с контрактами", "Электронное исполнение (ЕИС)", sq.OTHER_TOPIC}
    assert sum(r.incident for r in rows) == 1
    assert [r.mass for r in rows if r.mass] == ["33550005"] * 3


def test_parse_rows_without_taxonomy_uses_row_threshold():
    rows = sq.parse_rows(RECORDS, min_topic_rows=4)
    assert {r.topic for r in rows} == {"Работа с контрактами", sq.OTHER_TOPIC}


def test_build_report_shares_topics_and_conclusion():
    rows = sq.parse_rows(RECORDS, taxonomy=["Работа с контрактами", "Электронное исполнение (ЕИС)"])
    report = sq.build_report(rows, source="выгрузка.xlsx")
    assert report["total"] == 12
    assert report["answer_kinds"] == {
        "instruction": 4,
        "status": 0,
        "short": 4,
        "redirect": 1,
        "ask": 0,
        "mass": 3,
    }
    assert report["explained_share"] == round(4 / 12, 3)
    assert report["brush_off_share"] == round(5 / 12, 3)
    contracts = next(t for t in report["topics"] if t["topic"] == "Работа с контрактами")
    assert (contracts["count"], contracts["explained_share"], contracts["short_share"]) == (8, 0.5, 0.5)
    assert contracts["top_subtopics"][0]["subtopic"] == "Подписание контракта"
    assert report["topics"][-1]["topic"] == sq.OTHER_TOPIC
    assert report["problems"][0]["count"] == 8 and report["problems"][0]["keywords"]
    assert report["mass_incidents"][0]["number"] == "33550005" and report["mass_task_count"] == 1
    conclusion = report["conclusion"]
    assert "Разобрано 12 обращений" in conclusion
    assert "Понятную инструкцию получили 33%" in conclusion
    assert "33550005" in conclusion
    # Совет называет, про что писать статьи: подтемы с отписками, по убыванию
    assert (
        "по подтемам «Подписание контракта» (отписка в 4 из 8), «Прочее» (отписка в 1 из 1) —" in conclusion
    )
    # массовый сбой — не отписка, и в список статей его подтема не попадает
    assert "«Подписание и отправка УПД» (отписка" not in conclusion
    assert report["notes"], "ограничения выгрузки должны быть названы"


def test_read_taxonomy_finds_topics_under_the_header(tmp_path):
    path = tmp_path / "темы.xlsx"
    frame = pd.DataFrame(
        [
            ["", "", ""],
            ["№", "Тема обращений", "Подтема обращений:"],
            ["1", "Личный кабинет пользователя", "Регистрация"],
            ["2", "", "Полномочия"],
            ["3", "Работа с СТЕ", "Создание заявки СТЕ"],
        ]
    )
    frame.to_excel(path, header=False, index=False)
    assert sq.read_taxonomy(path) == ["Личный кабинет пользователя", "Работа с СТЕ"]


def test_cli_writes_json_report(tmp_path, capsys):
    export = tmp_path / "выгрузка.xlsx"
    pd.DataFrame(RECORDS).to_excel(export, index=False)
    out = tmp_path / "report.json"
    assert sq.main([str(export), "--out", str(out), "--min-topic", "1"]) == 0
    report = json.loads(out.read_text(encoding="utf-8"))
    assert report["total"] == 12 and report["source"] == "выгрузка.xlsx"
    assert "Разобрано 12 обращений" in capsys.readouterr().out


async def test_save_report_stores_analytics_row(tmp_path):
    url = f"sqlite+aiosqlite:///{tmp_path / 'db.sqlite3'}"
    report = sq.build_report(sq.parse_rows(RECORDS, min_topic_rows=1), source="x.xlsx")
    await sq.save_report(report, url)
    db = Database(url)
    try:
        async with db.session_factory() as session:
            row = (await session.execute(select(AnalyticsReport))).scalars().one()
    finally:
        await db.dispose()
    assert row.kind == sq.KIND
    assert row.payload["total"] == 12
