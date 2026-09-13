"""Конвейер записей: каждая запись ложится в свою таблицу и со своим провенансом."""

import pytest
from sqlalchemy import select

from app.ingest.pipeline import IMPORT_CHANNEL, persist
from app.ingest.records import (
    ArticleRecord,
    AssetRecord,
    DialogRecord,
    FeedbackRecord,
    ImageRef,
    Provenance,
)
from app.models import ChatSession, Feedback, KBArticle, Message, User


@pytest.mark.asyncio
async def test_records_land_in_their_tables(client):
    rt = client.app.state.rt
    records = [
        ArticleRecord(
            question="Как войти в личный кабинет?",
            answer="Через электронную подпись.",
            origin=Provenance(file="qa.xlsx", locator="строка 1", source_name="qa_table", authored=True),
        ),
        FeedbackRecord(rating=2, comment="Долго ждал ответа", operator="Мария Ковалёва"),
        DialogRecord(messages=[("user", "Как войти?"), ("assistant", "Через ЭП.")], line=1, resolved=True),
    ]
    result = await persist(rt, records, title="Выгрузка поддержки", params={"status": "draft"})

    assert result["created"] == 1
    assert result["feedback"] == 1
    assert result["dialogs"] == 1
    assert result["skipped"] == 0

    async with rt.db.session_factory() as db:
        article = (
            (await db.execute(select(KBArticle).where(KBArticle.question == "Как войти в личный кабинет?")))
            .scalars()
            .one()
        )
        # Провенанс — то, чем включается дословный ответ
        assert article.origin["authored"] is True
        assert article.origin["locator"] == "строка 1"
        assert article.origin["source"] == "qa_table"
        assert article.status == "draft"
        assert article.source == "import"
        # Ключевые слова конвейер добирает сам, если источник их не дал
        assert article.keywords

        review = (
            (await db.execute(select(Feedback).where(Feedback.comment == "Долго ждал ответа")))
            .scalars()
            .one()
        )
        assert review.rating == 2
        assert review.operator_id  # имя специалиста сопоставлено с пользователем
        specialist = await db.get(User, review.operator_id)
        assert specialist.display_name == "Мария Ковалёва"
        # Заведён как адресат оценок, а не для входа
        assert specialist.is_active is False

        imported = (
            (await db.execute(select(ChatSession).where(ChatSession.channel == IMPORT_CHANNEL)))
            .scalars()
            .all()
        )
        # Чужие обращения не должны подменять собой статистику стенда
        assert imported
        dialog = [s for s in imported if s.line == 1]
        assert dialog
        messages = (
            (await db.execute(select(Message).where(Message.session_id == dialog[0].id))).scalars().all()
        )
        assert [m.role for m in messages] == ["user", "assistant"]


@pytest.mark.asyncio
async def test_same_operator_is_not_duplicated(client):
    """Сто отзывов об одном специалисте — один пользователь, а не сто."""
    rt = client.app.state.rt
    records = [FeedbackRecord(rating=5, comment=f"Отзыв {i}", operator="Пётр Одинцов") for i in range(3)]
    await persist(rt, records, title="Отзывы", params={})

    async with rt.db.session_factory() as db:
        found = (await db.execute(select(User).where(User.display_name == "Пётр Одинцов"))).scalars().all()
        assert len(found) == 1


@pytest.mark.asyncio
async def test_answer_by_picture_is_kept_and_empty_one_is_skipped(client):
    """Ответ картинкой — валидная статья; пустая без картинки — брак."""
    rt = client.app.state.rt
    result = await persist(
        rt,
        [
            ArticleRecord(
                question="Где кнопка «Выгрузить»?",
                answer="",
                images=[ImageRef(url="/media/kb/x/0000.png", caption="Кнопка")],
            ),
            ArticleRecord(question="Вопрос без ответа", answer="   "),
            ArticleRecord(question="   ", answer="Ответ без вопроса"),
        ],
        title="Скриншоты",
        params={},
    )

    assert result["created"] == 1
    assert result["skipped"] == 2

    async with rt.db.session_factory() as db:
        article = (
            (await db.execute(select(KBArticle).where(KBArticle.question == "Где кнопка «Выгрузить»?")))
            .scalars()
            .one()
        )
        assert article.answer == ""
        assert article.images[0]["url"] == "/media/kb/x/0000.png"


@pytest.mark.asyncio
async def test_screenshot_binds_to_its_article_by_filename(client):
    """Папка скриншотов целиком: картинка обязана привязаться к своей статье.

    Проверяется весь путь, а не звенья по отдельности: источник даёт пару
    «AssetRecord + пустая статья», конвейер сохраняет картинку и связывает их по
    имени файла. Пока звенья проверялись врозь, статья доезжала без картинки и
    отбраковывалась как пустая — импорт скриншота молча заканчивался нулём.
    """
    rt = client.app.state.rt
    origin = Provenance(
        file="kak_najti_dogovor.png", locator="файл целиком", source_name="image_folder", authored=True
    )
    result = await persist(
        rt,
        [
            AssetRecord(
                data=b"\x89PNG\r\n\x1a\n" + b"\x00" * 32,
                caption="Как найти договор",
                anchor={"ref": "kak_najti_dogovor.png"},
                origin=origin,
            ),
            ArticleRecord(question="Как найти договор", answer="", origin=origin),
        ],
        title="Скриншоты",
        params={},
    )

    assert result["images"] == 1
    assert result["created"] == 1, "статья с ответом-картинкой не должна отбраковываться"
    assert result["skipped"] == 0

    async with rt.db.session_factory() as db:
        article = (
            (await db.execute(select(KBArticle).where(KBArticle.question == "Как найти договор")))
            .scalars()
            .one()
        )
        assert len(article.images) == 1, "одна картинка, а не три копии одной"
        assert article.images[0]["url"]
        assert article.images[0]["caption"] == "Как найти договор"


@pytest.mark.asyncio
async def test_reimport_leaves_no_vectors_of_previous_version(client):
    """Повторная загрузка документа заменяет его и в индексе, а не только в базе.

    Архив в базе точку в векторном хранилище не удаляет: на стенде после трёх
    переимпортов векторов стало на тысячу больше, чем статей.
    """
    rt = client.app.state.rt

    def version(tag: str) -> list[ArticleRecord]:
        return [
            ArticleRecord(question=f"Вопрос {n} о замене версии", answer=f"Ответ {n}, редакция {tag}")
            for n in range(3)
        ]

    before = await rt.store.count()
    first = await persist(rt, version("1"), title="Методичка о замене", params={"status": "active"})
    assert await rt.store.count() == before + 3

    second = await persist(rt, version("2"), title="Методичка  о ЗАМЕНЕ", params={"status": "active"})

    assert second["replaced"] == 3
    assert await rt.store.count() == before + 3, "векторы прошлой версии должны уйти из индекса"
    [vector] = await rt.embedder.embed(["Вопрос 1 о замене версии"])
    hits = await rt.store.search(vector, top_k=50)
    found = {hit.id for hit in hits}
    assert not found & set(first["article_ids"])
    assert found & set(second["article_ids"])


@pytest.mark.asyncio
async def test_upload_button_replaces_version_built_from_folder(client):
    """Сборка из каталога называет документ именем файла, кнопка в админке — им же,
    но с пробелами вместо «_». И статьи, сохранённые с ключом прошлой версии кода,
    тоже должны уйти в архив, а не остаться рядом с новыми."""
    rt = client.app.state.rt
    async with rt.db.session_factory() as db:
        db.add(
            KBArticle(
                id="legacy-key-article",
                question="Старая тема из справочника",
                answer="Старая подтема",
                category="Справочник",
                status="active",
                source="import",
                doc_key="справочник_тем_обращений",  # так ключ хранила прошлая версия
            )
        )
        await db.commit()

    result = await persist(
        rt,
        [ArticleRecord(question="Новая тема из справочника", answer="Новая подтема")],
        title="Справочник тем обращений",
        params={"status": "active"},
    )

    assert result["replaced"] == 1
    async with rt.db.session_factory() as db:
        assert (await db.get(KBArticle, "legacy-key-article")).status == "archived"


@pytest.mark.asyncio
async def test_empty_input_is_not_an_error(client):
    result = await persist(client.app.state.rt, [], title="Пусто", params={})
    assert result["created"] == 0
    assert result["article_ids"] == []
