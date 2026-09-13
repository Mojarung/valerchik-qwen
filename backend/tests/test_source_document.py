"""Источник document: методичка режется на разделы, вопрос к куску придумываем мы.

Тесты идут мимо фикстуры `client`: разбор — чистая функция над байтами, поднимать
ради него приложение с базой и очередью незачем.
"""

import io
import zipfile

import pytest

from app.ingest.blob import FileBlob
from app.ingest.registry import IngestContext
from app.ingest.sources.base import SourceMismatch
from app.ingest.sources.document import DocumentSource
from app.services import kbkind

DOC = """# Регистрация на портале

Для регистрации нужна усиленная квалифицированная электронная подпись.
Заявка подаётся в личном кабинете и рассматривается уполномоченным органом
в течение трёх рабочих дней. После одобрения организация появляется в реестре.

## Подписание контракта

Карточка контракта формируется у заказчика после подписания оферты победителем.
Поставщик подписывает её электронной подписью в разделе «Мои контракты».
"""

# Один раздел из нескольких самостоятельных абзацев: при большом потолке они
# склеиваются в один кусок, при маленьком расходятся по разным.
LONG_DOC = """# Работа с контрактами

Карточка контракта формируется на стороне заказчика после того, как победитель
подписал оферту. До этого момента карточка поставщику недоступна, поэтому искать
её в разделе «Мои контракты» раньше срока бессмысленно, а обращаться в поддержку
за ускорением — тем более.

Подписание идёт усиленной квалифицированной электронной подписью того же
юридического лица, которое подавало заявку. Подпись сотрудника без доверенности
портал отклонит, и в карточке останется статус ожидания, который многие путают
с ошибкой на стороне площадки.

Изменить условия подписанного контракта в одностороннем порядке нельзя. Любая
правка оформляется дополнительным соглашением, которое проходит тот же путь
согласования, что и сам контракт, и точно так же подписывается обеими сторонами
в личных кабинетах.

Оплата отражается в карточке после того, как заказчик проведёт документ о
приёмке. Задержка между приёмкой и появлением платежа в карточке считается
нормальной в пределах суток, и обращение в поддержку до истечения этого срока
ничего не ускорит.
"""


_CONTENT_TYPES = (
    '<?xml version="1.0"?>'
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.'
    'relationships+xml"/><Default Extension="xml" ContentType="application/xml"/>'
    '<Override PartName="/word/document.xml" ContentType="application/vnd.'
    'openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>'
)
_RELS = (
    '<?xml version="1.0"?>'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/'
    '2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>'
)


def _docx(paragraphs: list[tuple[str, str]]) -> bytes:
    """Настоящий .docx из заголовков и абзацев — методичка в том виде, в каком её грузят.

    Собираем архив руками, а не кладём файл в репозиторий: тест должен показывать,
    почему источник берётся за такой файл (внутри zip — размеченный текст), и не
    зависеть от бинарного вложения, которое никто не перечитает.
    """
    body = "".join(
        f'<w:p><w:pPr><w:pStyle w:val="{style}"/></w:pPr><w:r><w:t>{text}</w:t></w:r></w:p>'
        for style, text in paragraphs
    )
    document = (
        '<?xml version="1.0"?><w:document xmlns:w="http://schemas.openxmlformats.org/'
        f'wordprocessingml/2006/main"><w:body>{body}</w:body></w:document>'
    )
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", _CONTENT_TYPES)
        archive.writestr("_rels/.rels", _RELS)
        archive.writestr("word/document.xml", document)
    return buffer.getvalue()


class _RuntimeWithoutModel:
    """Рантайм, в котором модель не настроена.

    Ветку с LLM надо проверить, не выходя в сеть: `generate_question` сам обязан
    свалиться в заголовок, если адрес модели пустой. Так тест видит и семафор, и
    гарантию «наполнение базы важнее одной формулировки».
    """

    dyn = {"llm.base_url": "", "llm.model": ""}


@pytest.mark.asyncio
async def test_sniff_takes_text_and_refuses_binary():
    source = DocumentSource()
    assert await source.sniff(FileBlob("manual.md", DOC.encode("utf-8"))) > 0.15
    # Картинка — не наш файл: разбирать её как текст нечем
    assert await source.sniff(FileBlob("screen.png", b"\x89PNG\r\n\x1a\n\x00\x00")) == 0.0


@pytest.mark.asyncio
async def test_word_document_is_taken_despite_binary_bytes():
    """.docx — zip, и в первых байтах у него \\x00; отказ по этому признаку означал бы,
    что методичку в самом ходовом формате не берёт ни один источник."""
    source = DocumentSource()
    blob = FileBlob(
        "metodichka.docx",
        _docx(
            [
                ("Heading1", "Регистрация на портале"),
                (
                    "Normal",
                    "Для регистрации нужна усиленная квалифицированная электронная подпись. "
                    "Заявка подаётся в личном кабинете и рассматривается уполномоченным "
                    "органом в течение трёх рабочих дней.",
                ),
            ]
        ),
    )

    assert blob.is_binary is True
    assert await source.sniff(blob) > 0.15

    records = [r async for r in source.parse(blob, IngestContext(params={"use_llm": False}))]
    assert len(records) == 1
    assert "электронная подпись" in records[0].answer
    assert records[0].origin.locator == "фрагмент 1"
    assert records[0].origin.authored is False


@pytest.mark.asyncio
async def test_broken_office_file_is_refused_not_crashed():
    """Битый архив — отказ источника, а не падение: файл обязан достаться следующему."""
    source = DocumentSource()
    blob = FileBlob("metodichka.docx", b"PK\x03\x04\x00\x00" + "мусор, а не документ".encode())

    with pytest.raises(SourceMismatch):
        [r async for r in source.parse(blob, IngestContext(params={"use_llm": False}))]


@pytest.mark.asyncio
async def test_document_becomes_sections_without_llm():
    source = DocumentSource()
    blob = FileBlob(name="manual.md", data=DOC.encode("utf-8"))
    assert await source.sniff(blob) > 0.15

    ctx = IngestContext(rt=None, params={"use_llm": False})
    records = [record async for record in source.parse(blob, ctx)]

    assert len(records) == 2
    assert records[0].question.startswith("Регистрация")
    assert "электронная подпись" in records[0].answer
    assert records[1].question.startswith("Подписание")
    assert "Мои контракты" in records[1].answer
    for index, record in enumerate(records, start=1):
        # Вопрос придуман нами, не автором: дословный ответ по такой статье запрещён
        assert record.origin.authored is False
        assert record.origin.source_name == "document"
        assert record.origin.file == "manual.md"
        assert record.origin.locator == f"фрагмент {index}"
        assert record.keywords
        assert record.answer.strip()
    # Рубрика без явного указания — заголовок раздела
    assert records[0].category == "Регистрация на портале"


@pytest.mark.asyncio
async def test_figure_captions_and_contents_are_not_answers():
    """Подпись под картинкой — короткая изолированная строка, то есть заголовок по
    всем признакам нарезки, и импорт методичек завёл 782 статьи «Рисунок N - …» и
    66 «СОДЕРЖАНИЕ». Ответа в них нет, а слот кандидата они занимают."""
    doc = (
        "# СОДЕРЖАНИЕ\n\nРегистрация на портале поставщиков\nПодписание контрактов "
        "и оферт\nРабота с электронным актированием и УПД\n\n"
        "# Регистрация на портале\n\nЗаявка подаётся в личном кабинете и "
        "рассматривается уполномоченным органом в течение трёх рабочих дней.\n\n"
        "Рисунок 82 - Экспорт оферт\n\nКнопка выгрузки находится в правом верхнем "
        "углу списка оферт, рядом с фильтром по датам.\n"
    )
    blob = FileBlob(name="manual.md", data=doc.encode("utf-8"))
    ctx = IngestContext(rt=None, params={"use_llm": False})

    records = [record async for record in DocumentSource().parse(blob, ctx)]

    kinds = {record.question: record.origin.kind for record in records}
    assert kinds["СОДЕРЖАНИЕ"] == kbkind.REFERENCE
    assert kinds["Рисунок 82 - Экспорт оферт"] == kbkind.REFERENCE
    assert kinds["Регистрация на портале"] == kbkind.ANSWER


@pytest.mark.asyncio
async def test_glossary_table_becomes_one_article_per_term():
    """Глоссарий методички приезжал одной статьёй «Таблица 1 - Перечень терминов
    и сокращений»: расшифровка УПД лежала в теле, а 75 % лексической уверенности
    даёт заголовок — «что такое УПД» не находило ничего. Разобранная по строкам,
    таблица отвечает, а сама остаётся справочником, чтобы не соперничать."""
    doc = (
        "# Таблица 1 - Перечень терминов и сокращений\n\n"
        "| Сокращение | Расшифровка |\n"
        "| --- | --- |\n"
        "| УПД | Универсальный передаточный документ |\n"
        "| МЧД | Машиночитаемая доверенность |\n"
        "| КС | Котировочная сессия |\n"
        "| 12 | Страница методички |\n"
    )
    blob = FileBlob(name="manual.md", data=doc.encode("utf-8"))
    ctx = IngestContext(rt=None, params={"use_llm": False})

    records = [record async for record in DocumentSource().parse(blob, ctx)]

    terms = {r.question: r.answer for r in records if r.origin.kind == kbkind.ANSWER}
    assert terms["Что означает УПД?"] == "УПД — Универсальный передаточный документ."
    assert set(terms) == {"Что означает УПД?", "Что означает МЧД?", "Что означает КС?"}
    # Заголовок колонок статьёй не становится
    assert "Что означает Сокращение?" not in terms
    table = [r for r in records if r.question.startswith("Таблица 1")]
    assert [r.origin.kind for r in table] == [kbkind.REFERENCE]


@pytest.mark.asyncio
async def test_empty_document_gives_no_records():
    source = DocumentSource()
    blob = FileBlob(name="empty.txt", data=b"")

    assert await source.sniff(blob) == 0.0
    ctx = IngestContext(rt=None, params={"use_llm": False})
    assert [record async for record in source.parse(blob, ctx)] == []


@pytest.mark.asyncio
async def test_llm_branch_falls_back_when_model_is_not_configured():
    source = DocumentSource()
    blob = FileBlob(name="manual.md", data=DOC.encode("utf-8"))

    ctx = IngestContext(rt=_RuntimeWithoutModel(), params={})
    records = [record async for record in source.parse(blob, ctx)]

    assert [record.question for record in records] == [
        "Регистрация на портале",
        "Подписание контракта",
    ]
    assert all(record.origin.authored is False for record in records)


@pytest.mark.asyncio
async def test_page_headers_do_not_reach_articles():
    """Шапка и номер страницы PDF не должны попадать в куски методички.

    Страницы собраны так, как их отдаёт markitdown: шапка документа, текст, номер
    страницы отдельной строкой. Одна фраза разорвана шапкой — после чистки она
    обязана оказаться в одном куске целиком.
    """
    header = "Инструкция по работе с Порталом поставщиков. Версия 3.2"
    pages = []
    for number in range(1, 16):
        body = f"Шаг {number}. Откройте раздел «Мои оферты» и проверьте статус заявки номер {number}."
        if number == 7:
            body = "Подписание оферты выполняется электронной подписью руководителя"
        if number == 8:
            body = "организации, указанного в карточке. " + body.replace("Шаг 8", "Шаг восемь")
        pages.append(f"{header}\n{body}\n{number}")
    blob = FileBlob("instrukciya.txt", "\n\n".join(pages).encode("utf-8"))

    records = [r async for r in DocumentSource().parse(blob, IngestContext(params={"use_llm": False}))]

    answers = "\n".join(record.answer for record in records)
    assert header not in answers
    assert "\n7\n" not in f"\n{answers}\n"
    assert "электронной подписью руководителя\nорганизации, указанного в карточке" in answers


@pytest.mark.asyncio
async def test_params_set_category_and_chunk_size():
    source = DocumentSource()
    blob = FileBlob(name="contracts.md", data=LONG_DOC.encode("utf-8"))

    default = [r async for r in source.parse(blob, IngestContext(params={"use_llm": False}))]
    narrow = [
        r
        async for r in source.parse(
            blob, IngestContext(params={"use_llm": False, "max_chunk_chars": 300, "category": "Контракты"})
        )
    ]

    assert len(narrow) > len(default)
    # Явная рубрика важнее заголовка: её задал человек, который знает, что грузит
    assert {record.category for record in narrow} == {"Контракты"}
    assert {record.category for record in default} == {"Работа с контрактами"}
