"""Единая точка входа импорта: формат выбирает реестр, архив разбирается целиком."""

import io
import json
import zipfile

import pytest
from sqlalchemy import select

from app.ingest.blob import FileBlob
from app.models import KBArticle
from app.services.ingest import IngestFailed, ingest_file

TABLE = (
    "| Вопрос | Ответ |\n|---|---|\n"
    "| Как зарегистрироваться на портале? | Нужна электронная подпись и заявка. |\n"
    "| Как подписать контракт? | Откройте карточку контракта и нажмите «Подписать». |\n"
    "| Почему не приходит УПД? | Проверьте статус в разделе «Исполнение». |\n"
    "| Как сменить реквизиты? | В профиле организации, раздел «Банковские счета». |\n"
    "| Куда жаловаться? | В ФАС через форму на портале поставщиков. |\n"
)


@pytest.mark.asyncio
async def test_table_goes_through_the_registry_and_stays_authored(client):
    rt = client.app.state.rt
    result = await ingest_file(
        rt, FileBlob(name="qa.md", data=TABLE.encode("utf-8")), title="Выгрузка", params={"status": "draft"}
    )

    assert result["created"] == 5
    assert result["source"] == "qa_table"

    async with rt.db.session_factory() as db:
        article = (
            (
                await db.execute(
                    select(KBArticle).where(KBArticle.question == "Как зарегистрироваться на портале?")
                )
            )
            .scalars()
            .first()
        )
        assert article is not None
        # Авторская пара — тот самый признак, по которому ответ уйдёт дословно
        assert article.origin["authored"] is True


@pytest.mark.asyncio
async def test_unknown_extension_is_not_rejected_upfront(client):
    """Раньше «.dat» отсекался списком расширений до всякого разбора."""
    result = await ingest_file(
        client.app.state.rt,
        FileBlob(name="dump.dat", data=TABLE.encode("utf-8")),
        title="Странный",
        params={},
    )
    assert result["created"] == 5


@pytest.mark.asyncio
async def test_zip_is_imported_without_unpacking(client):
    """Организаторы выкладывают архив — распаковывать руками не должно быть нужно.

    Разведка zip читала, а импорт отвечал «ни один источник не разобрал файл»:
    ручка архив принимала, и падало это уже в фоновой задаче, на глазах у всех.
    """
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("данные/qa.md", TABLE)
        archive.writestr(
            "данные/faq.json",
            json.dumps(
                [
                    {
                        "question": "Что такое котировочная сессия?",
                        "answer": "Электронная процедура закупки малого объёма.",
                    }
                ],
                ensure_ascii=False,
            ),
        )

    rt = client.app.state.rt
    result = await ingest_file(
        rt,
        FileBlob(name="Выгрузка.zip", data=buffer.getvalue()),
        title="Архив организаторов",
        params={"status": "draft"},
    )

    assert result["created"] == 6, result

    async with rt.db.session_factory() as db:
        article = (
            (
                await db.execute(
                    select(KBArticle).where(KBArticle.question == "Что такое котировочная сессия?")
                )
            )
            .scalars()
            .first()
        )
        assert article is not None
        # Провенанс хранит путь внутри архива: «faq.json» без папки бесполезно,
        # когда файлов с таким именем в архиве несколько
        assert article.origin["file"] == "данные/faq.json"


@pytest.mark.asyncio
async def test_broken_archive_explains_itself(client):
    with pytest.raises(IngestFailed, match="Архив не читается"):
        await ingest_file(
            client.app.state.rt,
            FileBlob(name="битый.zip", data=b"PK\x03\x04".ljust(64, b"\x00")),
            title="Битый",
            params={},
        )
