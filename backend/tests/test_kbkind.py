"""Виды записей базы знаний: вид берётся из происхождения и доезжает до базы.

Вид решает, можно ли отвечать записью человеку. Ошибка в любую сторону дорогая:
случай для оператора, принятый за ответ, — бот пересказывает чужую заявку («заказчику
направлено приглашение»); ответ, принятый за случай, — бот молчит там, где знает.
"""

import pytest
from sqlalchemy import select

from app.ingest.pipeline import persist
from app.ingest.records import ArticleRecord, Provenance
from app.models import KBArticle
from app.services import kbkind


@pytest.mark.parametrize(
    ("origin", "kind"),
    [
        # Статьи, заведённые до появления видов, — ответы, как и были
        (None, kbkind.ANSWER),
        ({}, kbkind.ANSWER),
        ({"file": "qa.xlsx", "authored": True}, kbkind.ANSWER),
        ({"kind": None}, kbkind.ANSWER),
        ({"kind": ""}, kbkind.ANSWER),
        ({"kind": "answer"}, kbkind.ANSWER),
        ({"kind": "operator_case"}, kbkind.OPERATOR_CASE),
        ({"kind": "reference"}, kbkind.REFERENCE),
        # Незнакомый вид — не повод терять статью: она остаётся ответом
        ({"kind": "draft"}, kbkind.ANSWER),
    ],
)
def test_kind_of_origin(origin, kind):
    assert kbkind.of(origin) == kind


def test_only_answers_answer_the_user():
    assert kbkind.answers_user(kbkind.ANSWER)
    assert not kbkind.answers_user(kbkind.OPERATOR_CASE)
    assert not kbkind.answers_user(kbkind.REFERENCE)
    # Подпись вида есть у каждого: админка показывает её рядом со статьёй
    assert set(kbkind.LABELS) == kbkind.ALL


def test_provenance_carries_kind_and_defaults_to_answer():
    assert Provenance().as_dict()["kind"] == kbkind.ANSWER
    origin = Provenance(
        file="stp.xlsx", source_name="support_export", authored=True, kind=kbkind.OPERATOR_CASE
    )
    assert origin.as_dict() == {
        "file": "stp.xlsx",
        "locator": "",
        "source": "support_export",
        "authored": True,
        "kind": kbkind.OPERATOR_CASE,
    }


@pytest.mark.asyncio
async def test_kind_reaches_the_database(client):
    """Конвейер пишет вид в origin статьи — по нему поиск и отличает ответ от случая."""
    rt = client.app.state.rt
    records = [
        ArticleRecord(
            question="Как исправить ошибку в карточке СТЕ?",
            answer="Нажмите «Сообщить об ошибке» в карточке СТЕ и опишите, что исправить.",
            origin=Provenance(file="stp.xlsx", source_name="support_export", authored=True),
        ),
        ArticleRecord(
            question="Не могу подписать УПД, нет связи с заказчиком",
            answer="Заказчику направлено приглашение на установку связи.",
            origin=Provenance(
                file="stp.xlsx", source_name="support_export", authored=True, kind=kbkind.OPERATOR_CASE
            ),
        ),
        ArticleRecord(
            question="Блокировка личного кабинета",
            answer="Работа с личным кабинетом",
            origin=Provenance(
                file="темы.xlsx", source_name="topic_taxonomy", authored=True, kind=kbkind.REFERENCE
            ),
        ),
    ]
    result = await persist(rt, records, title="Виды записей", params={"status": "draft"})
    assert result["created"] == 3

    async with rt.db.session_factory() as db:
        rows = (
            (await db.execute(select(KBArticle).where(KBArticle.doc_key == "виды записей"))).scalars().all()
        )
    kinds = {row.question: kbkind.of(row.origin) for row in rows}
    assert kinds == {
        "Как исправить ошибку в карточке СТЕ?": kbkind.ANSWER,
        "Не могу подписать УПД, нет связи с заказчиком": kbkind.OPERATOR_CASE,
        "Блокировка личного кабинета": kbkind.REFERENCE,
    }
    # Вид лежит в самом JSON, а не выводится на лету: его видит и векторный индекс
    assert {row.origin["kind"] for row in rows} == kbkind.ALL
