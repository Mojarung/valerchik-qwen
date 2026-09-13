"""Источник regulation: нормативный акт режется по пунктам и отдаётся дословно.

Текст синтетический, но устроен как выгрузка КонсультантПлюс: приказ-обёртка,
регламент приложением к нему, приложения с собственной нумерацией, пометки о
редакциях и шапка приказа на каждой странице. Реальный регламент проверяется
отдельным smoke-тестом, если путь к его тексту передан через REGLAMENT_TXT.
"""

import os
from pathlib import Path

import pytest
from pdfminer.glyphlist import glyphname2unicode

from app.ingest.blob import FileBlob
from app.ingest.registry import IngestContext, registry
from app.ingest.sources.base import SourceMismatch
from app.ingest.sources.regulation import (
    CONFIDENCE,
    MAX_ARTICLE_CHARS,
    RegulationSource,
    clause_question,
    parse_act,
    regulation_signals,
)
from app.services import kbkind

HEADER = [
    "Приказ Тендерного комитета г. Москвы от 14.02.2024 N 70-01-17/24",
    "Документ предоставлен КонсультантПлюс",
    "(ред. от 10.06.2025)",
    '"Об утверждении Регламента информа...',
    "Дата сохранения: 15.07.2025",
]

FILLER = (
    "поставщика, являющегося участником котировочной сессии, получившим статус ее победителя, "
    "три и более раз в течение тридцати дней, исчисляемых со дня первого уклонения от подписания "
    "оферты и (или) заключения контракта (договора) по итогам проведения котировочной сессии"
)

BODY = f"""ПРАВИТЕЛЬСТВО МОСКВЫ
ПРИКАЗ
от 14 февраля 2024 г. N 70-01-17/24
В целях поддержания в актуальном состоянии распорядительных документов приказываю:
1. Утвердить Регламент информационного взаимодействия согласно приложению к настоящему приказу.
2. Настоящий приказ вступает в силу с 1 марта 2024 г.
Приложение
к приказу Департамента
от 14 февраля 2024 г. N 70-01-17/24
РЕГЛАМЕНТ
ИНФОРМАЦИОННОГО ВЗАИМОДЕЙСТВИЯ С ИСПОЛЬЗОВАНИЕМ
АИС "ПОРТАЛ ПОСТАВЩИКОВ"
Список изменяющих документов
(в ред. приказов Тендерного комитета г. Москвы от 20.01.2025 N 70-01-1/25,
от 10.06.2025 N 70-01-92/25)
Настоящий Регламент информационного взаимодействия определяет порядок взаимодействия участников
информационного взаимодействия в АИС "Портал поставщиков", состав размещаемой информации и иные
правила функционирования АИС "Портал поставщиков".
1. Общие положения
1.1. В настоящем Регламенте используются следующие сокращения:
1.1.1. СКК - служба контроля качества АИС "Портал поставщиков", являющаяся постоянным составом
работников Уполномоченного органа.
1.1.1(1). КПГЗ - классификатор предметов государственного заказа города Москвы.
(п. 1.1.1(1) введен приказом Тендерного комитета г. Москвы от 20.01.2025 N 70-01-1/25)
1.1.2. СТП - служба технической поддержки АИС "Портал поставщиков".
1.2. В Регламенте используются следующие понятия:
1.2.1. Администратор пользователя - представитель пользователя, которому предоставлены
полномочия по созданию учетных записей других представителей пользователя.
1.2.2. Система - источник команды - информационная система, которая является инициатором вызова
интеграционной команды.
1.3. Документы, формируемые в рамках функционирования АИС "Портал поставщиков", не должны
противоречить настоящему Регламенту.
2. Порядок доступа к информации, содержащейся в АИС
"Портал поставщиков"
2.1. Предоставление доступа к информации осуществляется на безвозмездной основе в соответствии с
пунктом 1.6 Положения, при этом доступ предоставляется через сеть "Интернет"
4.5. Положения об АИС и настоящего Регламента.
2.2. Регистрация пользователей в АИС "Портал поставщиков" осуществляется следующими способами:
2.2.1. Упрощенная регистрация.
2.2.2. Регистрация с использованием УКЭП.
Приложение 1
к Регламенту информационного
взаимодействия с использованием
системы "Портал поставщиков"
ПРАВИЛА
ПРОВЕДЕНИЯ КОТИРОВОЧНОЙ СЕССИИ
1. Снятие котировочной сессии с публикации
1.1. Просьба о снятии котировочной сессии с публикации направляется:
- заказчиком города Москвы - в службу технической поддержки ЕАИСТ;
- региональным заказчиком - в СТП.
1.2. Просьба о снятии котировочной сессии с публикации подлежит рассмотрению при соблюдении
следующих условий:
- до завершения котировочной сессии осталось не менее 5 часов;
(в ред. приказа Тендерного комитета г. Москвы от 20.01.2025 N 70-01-1/25)
- просьба направлена в период с 9.00 до 17.00 по московскому времени.
Приложение 2
к Регламенту информационного
взаимодействия с использованием
системы "Портал поставщиков"
ПРАВИЛА
БЛОКИРОВКИ ЛИЧНОГО КАБИНЕТА И ЕЕ СНЯТИЯ
1. Поставщик вправе обжаловать блокировку своего личного кабинета, направив соответствующую
жалобу в Арбитражную комиссию АИС "Портал поставщиков" через свой личный кабинет.
2. Арбитражная комиссия АИС "Портал поставщиков" рассматривает жалобу в течение десяти
рабочих дней с даты ее поступления.
3. Блокировка личного кабинета поставщика осуществляется:
3.1. Сроком на три месяца в случаях:
3.1.1. Отказа от подписания оферты {FILLER}, а также {FILLER}.
3.1.2. Уклонения от заключения контракта {FILLER}.
3.1.3. Расторжения контракта по вине {FILLER}.
3.2. Бессрочно в случаях:
3.2.1. Направления пользователем обращения, содержащего нецензурные либо оскорбительные
выражения, угрозы жизни, здоровью и имуществу должностного лица Оператора, Уполномоченного органа
(личный кабинет такого пользователя подлежит блокировке без объяснения причин и без права ее
обжалования в Арбитражной комиссии АИС "Портал поставщиков").
Приложение 3
к Регламенту информационного
взаимодействия
Примерная форма
Протокол информационного взаимодействия
1. Информационное взаимодействие АИС "Портал поставщиков" и
___________________________________________
(наименование информационной системы)
осуществляется в следующих целях:
1.1. ...
2. Участниками информационного обмена являются:
_______________________________ и ________________________
"""


def consultant_pages(body: str, per_page: int = 9) -> str:
    """Тело акта, разложенное по страницам выгрузки: шапка сверху, подвал снизу."""
    lines = body.split("\n")
    chunks = [lines[i : i + per_page] for i in range(0, len(lines), per_page)]
    pages = []
    for number, chunk in enumerate(chunks, start=1):
        footer = [
            "КонсультантПлюс",
            f"www.consultant.ru Страница {number} из {len(chunks)}",
            "надежная правовая поддержка",
            "1",
        ]
        pages.append("\n".join([*HEADER, *chunk, *footer]))
    return "\n\n".join(pages)


TEXT = consultant_pages(BODY)


def by_locator(articles, locator: str):
    found = [article for article in articles if article.locator == locator]
    assert len(found) == 1, f"{locator}: {[a.locator for a in articles]}"
    return found[0]


@pytest.fixture(scope="module")
def articles():
    return parse_act(TEXT)


# ---------------------------------------------------------------- распознавание


@pytest.mark.asyncio
async def test_sniff_takes_regulation_above_document_and_pdf():
    source = RegulationSource()
    confidence = await source.sniff(FileBlob("reglament.txt", TEXT.encode("utf-8")))
    assert confidence == CONFIDENCE
    # Выше document (0.45) и pdf_figures (0.85): иначе регламент порезали бы как методичку
    assert confidence > 0.85
    ranked = await registry.ranked(FileBlob("reglament.txt", TEXT.encode("utf-8")))
    assert ranked[0][1].name == "regulation"


@pytest.mark.asyncio
async def test_sniff_refuses_instruction_with_numbered_sections():
    """Инструкция тоже нумерует разделы «3.1.», но не ссылается на «настоящий Регламент»
    и через строку отсылает к рисункам — её должен резать document."""
    lines = ["Инструкция по работе с Порталом для поставщика", "1. Общие сведения"]
    for n in range(1, 25):
        lines += [
            f"3.{n}. Откройте раздел «Мои оферты» и нажмите кнопку «Создать» (Рисунок {n}).",
            f"Рисунок {n} – Создание оферты. Порядок действий описан в Регламенте портала.",
        ]
    instruction = "\n".join(lines)

    signals = regulation_signals(instruction)
    assert signals.deep_clauses >= 20
    assert not signals.is_regulation
    assert await RegulationSource().sniff(FileBlob("instrukciya.md", instruction.encode("utf-8"))) == 0.0


@pytest.mark.asyncio
async def test_sniff_ignores_tables_and_images():
    source = RegulationSource()
    assert await source.sniff(FileBlob("stp.xlsx", b"PK\x03\x04")) == 0.0
    assert await source.sniff(FileBlob("screen.png", b"\x89PNG\r\n\x1a\n")) == 0.0
    assert await source.sniff(FileBlob("broken.pdf", b"%PDF-1.7 not really")) == 0.0


# ---------------------------------------------------------------- структура


def test_order_wrapper_and_blank_form_give_no_articles(articles):
    answers = "\n".join(article.answer for article in articles)
    # Приказ-обёртка («утвердить», «вступает в силу») пользователю ни на что не отвечает
    assert "Утвердить Регламент" not in answers
    # Приложение-бланк с прочерками — шаблон, а не правило
    assert "____" not in answers
    assert not [a for a in articles if a.locator.startswith("прил. 3")]


def test_page_headers_and_edition_notes_do_not_leak(articles):
    answers = "\n".join(article.answer for article in articles)
    for leak in (
        "КонсультантПлюс",
        "Дата сохранения",
        "Страница",
        "Об утверждении Регламента информа",
        "(в ред.",
    ):
        assert leak not in answers
    assert "введен приказом" not in answers


def test_glossary_becomes_term_articles(articles):
    skk = by_locator(articles, "п. 1.1.1")
    assert skk.question == "Что такое СКК?"
    assert skk.answer.startswith("СКК — служба контроля качества АИС «Портал поставщиков»")
    assert "(пункт 1.1.1 Регламента)" in skk.answer
    assert skk.category == "Регламент: термины и сокращения"
    assert "служба контроля качества АИС «Портал поставщиков»" in skk.keywords
    # Вопрос уже точный — модели его не отдаём
    assert skk.rephrase is False

    assert by_locator(articles, "п. 1.1.1(1)").question == "Что такое КПГЗ?"
    assert by_locator(articles, "п. 1.1.2").question == "Что такое СТП?"
    admin = by_locator(articles, "п. 1.2.1")
    assert admin.question == "Что означает «Администратор пользователя»?"
    assert admin.answer.startswith("Администратор пользователя — представитель пользователя")
    # Термин с дефисом внутри: режем по второму тире, а не по первому
    assert by_locator(articles, "п. 1.2.2").question == "Что означает «Система - источник команды»?"
    # Вводная фраза раздела сокращений сама по себе статьёй не становится
    assert not [a for a in articles if a.locator == "п. 1.1"]


def test_clause_keeps_number_section_and_verbatim_text(articles):
    clause = by_locator(articles, "п. 1.3")
    assert clause.category == "Регламент: Общие положения"
    assert clause.answer.startswith("Пункт 1.3 Регламента, раздел «Общие положения»:\n1.3. Документы")
    assert clause.authored is True

    registration = by_locator(articles, "п. 2.2")
    assert registration.question == "Как осуществляется регистрация пользователей в АИС «Портал поставщиков»?"
    assert "2.2.1. Упрощенная регистрация." in registration.answer
    assert "2.2.2. Регистрация с использованием УКЭП." in registration.answer
    # Заголовок раздела, перенесённый на вторую строку, склеен целиком
    assert (
        registration.category
        == "Регламент: Порядок доступа к информации, содержащейся в АИС «Портал поставщиков»"
    )


def test_wrapped_line_starting_with_number_is_not_a_clause(articles):
    """«4.5. Положения…» после пункта 2.1 — перенос фразы: нумерация назад не идёт."""
    clause = by_locator(articles, "п. 2.1")
    assert "4.5. Положения об АИС" in clause.answer
    assert not [a for a in articles if a.locator == "п. 4.5"]


def test_sentence_split_by_page_break_is_whole(articles):
    """Пункт, разрезанный колонтитулом, собирается в одну фразу без шапки внутри."""
    skk = by_locator(articles, "п. 1.1.1")
    assert "являющаяся постоянным составом работников Уполномоченного органа." in skk.answer


def test_appendix_is_a_section_with_its_own_numbering(articles):
    removal = by_locator(articles, "прил. 1, п. 1.2")
    assert removal.category == "Регламент: Правила проведения котировочной сессии"
    assert removal.answer.startswith(
        "Пункт 1.2 приложения 1 к Регламенту «Правила проведения котировочной сессии», "
        "раздел «Снятие котировочной сессии с публикации»:"
    )
    assert (
        removal.question
        == "При каких условиях просьба о снятии котировочной сессии с публикации подлежит рассмотрению?"
    )
    # Перечень — каждый пункт с новой строки, пометка о редакции внутри него вырезана
    assert (
        "\n- до завершения котировочной сессии осталось не менее 5 часов;\n- просьба направлена"
        in removal.answer
    )

    where = by_locator(articles, "прил. 1, п. 1.1")
    assert where.question == "Куда направляется просьба о снятии котировочной сессии с публикации?"


def test_appendix_without_sections_gives_article_per_clause(articles):
    complaint = by_locator(articles, "прил. 2, п. 2")
    assert (
        complaint.question
        == "В какой срок арбитражная комиссия АИС «Портал поставщиков» рассматривает жалобу?"
    )
    assert "в течение десяти рабочих дней" in complaint.answer
    assert complaint.category == "Регламент: Правила блокировки личного кабинета и ее снятия"

    appeal = by_locator(articles, "прил. 2, п. 1")
    assert appeal.question == "Может ли поставщик обжаловать блокировку своего личного кабинета?"


def test_long_clause_splits_into_subclauses_with_lead(articles):
    """Пункт длиннее порога делится на подпункты, и каждый несёт вводную фразу родителя."""
    assert not [a for a in articles if a.locator == "прил. 2, п. 3"]
    forever = by_locator(articles, "прил. 2, п. 3.2")
    assert (
        forever.question == "В каких случаях блокировка личного кабинета поставщика осуществляется бессрочно?"
    )
    assert (
        "3. Блокировка личного кабинета поставщика осуществляется:\n3.2. Бессрочно в случаях:\n3.2.1."
        in forever.answer
    )
    assert "нецензурные либо оскорбительные" in forever.answer
    three_months = by_locator(articles, "прил. 2, п. 3.1")
    assert len(three_months.answer) <= MAX_ARTICLE_CHARS + 400
    assert "3.1.3. Расторжения контракта" in three_months.answer


def test_every_article_is_a_verbatim_answer(articles):
    assert articles
    for article in articles:
        assert article.question and article.answer
        assert "?" in article.question or len(article.question) <= 150
        assert article.keywords


# ---------------------------------------------------------------- вопросы


@pytest.mark.parametrize(
    ("leads", "expected"),
    [
        (
            ["Решение о снятии котировочной сессии с публикации принимается модератором АИС."],
            "Кем принимается решение о снятии котировочной сессии с публикации?",
        ),
        (
            ["Котировочная сессия по выбору заказчика длится 3, 6 или 24 часа согласно таблице."],
            "Сколько длится котировочная сессия по выбору заказчика?",
        ),
        (
            ["В публичной части АИС размещается и обрабатывается следующая информация:"],
            "Какая информация размещается и обрабатывается в публичной части АИС?",
        ),
        (
            [
                "Пользователь имеет право в любое время расторгнуть Соглашение путем письменного "
                "уведомления не позднее чем за пять рабочих дней."
            ],
            "Может ли пользователь в любое время расторгнуть Соглашение?",
        ),
        (
            ["Источниками информации для осуществления блокировки являются:"],
            "Что является источниками информации для осуществления блокировки?",
        ),
    ],
)
def test_clause_question_patterns(leads, expected):
    assert clause_question(leads, "Раздел", "1.1") == expected


def test_clause_question_refuses_broken_patterns():
    """Действие после срока, условие, отрицание — не вопрос; тогда заголовок, а не обрывок."""
    deadline_first = clause_question(
        ["Заказчик и поставщик в срок не позднее двадцати рабочих дней должны согласовать проект контракта."],
        "Правила прямой закупки",
        "7",
    )
    assert not deadline_first.startswith("В какой срок заказчик и поставщик?")

    conditional = clause_question(
        ["Если личный кабинет не используется в течение трех месяцев, уведомления в него не направляются."],
        "Раздел",
        "3.7",
    )
    assert not conditional.startswith("Как используется")

    # Длинная фраза без запятых — её начало по границе слова, а не «Раздел (п. N)»:
    # одиннадцать пунктов «Общие положения (п. …)» подряд не различить
    fallback = clause_question(["Очень " * 40 + "длинная фраза без запятых"], "Состав информации", "2.5")
    assert fallback.startswith("Очень Очень") and fallback.endswith("…")
    assert len(fallback) <= 121
    # Фразы нет вовсе — тогда раздел с номером пункта
    assert clause_question(["Нет."], "Состав информации", "2.5") == "Состав информации (п. 2.5)"


def test_fallback_title_keeps_the_lead_of_the_parent():
    """Подпункт без родителя — обрывок без подлежащего («Подачу и регистрацию
    оферт…»); склейка с вводной фразой читается как заголовок."""
    question = clause_question(
        [
            "Взаимодействие поставщика и заказчика в АИС включает:",
            "Подачу и регистрацию оферт в порядке, приведенном в приложении 3.",
        ],
        "Раздел",
        "3.1.5",
    )
    assert question.startswith("Взаимодействие поставщика и заказчика в АИС включает подачу")


SAME_CLAUSE_TWICE = (
    BODY
    + """Приложение 4
к Регламенту информационного
взаимодействия
ПРАВИЛА
ОСУЩЕСТВЛЕНИЯ ПРЯМОЙ ЗАКУПКИ
1. Сведения о заключенном контракте направляются в реестр контрактов.
2. Прямая закупка проводится заказчиком в соответствии с настоящими Правилами.
Приложение 5
к Регламенту информационного
взаимодействия
ПРАВИЛА
ОСУЩЕСТВЛЕНИЯ ЗАКУПКИ ПО ПОТРЕБНОСТЯМ
1. Сведения о заключенном контракте направляются в реестр контрактов.
2. Закупка по потребностям проводится заказчиком в соответствии с настоящими Правилами.
"""
)


def test_same_clause_in_two_procedures_gets_distinct_questions():
    """Одинаковый пункт в правилах прямой закупки и закупки по потребностям —
    два разных ответа. С одним вопросом на двоих их не различить ни поиску,
    ни человеку в вариантах уточнения."""
    articles = parse_act(consultant_pages(SAME_CLAUSE_TWICE))

    direct = by_locator(articles, "прил. 4, п. 1")
    needs = by_locator(articles, "прил. 5, п. 1")

    assert (
        direct.question
        == "Куда направляются сведения о заключенном контракте (Правила осуществления прямой закупки)?"
    )
    assert needs.question == (
        "Куда направляются сведения о заключенном контракте (Правила осуществления закупки по потребностям)?"
    )
    # Уникальные вопросы не трогаем
    assert by_locator(articles, "прил. 2, п. 2").question == (
        "В какой срок арбитражная комиссия АИС «Портал поставщиков» рассматривает жалобу?"
    )


NESTED_IN_FORM = (
    BODY
    + """Приложение 1
к Протоколу (регламенту)
информационного взаимодействия
1. Сторона-координатор обеспечивает обмен сведениями между информационными системами.
2. Сторона-участник направляет сведения в согласованном формате.
"""
)


def test_nested_appendix_of_blank_form_is_addressed_to_the_form():
    """«Приложение 1 к Протоколу» внутри бланка (приложение 3) — приложение к нему,
    а не к предыдущему приложению 2: иначе адрес и цитата указывают на чужое."""
    articles = parse_act(consultant_pages(NESTED_IN_FORM))

    nested = [a for a in articles if "к Протоколу" in a.answer]
    assert nested
    assert all(a.locator.startswith("прил. 3, прил. 1, ") for a in nested)
    assert all("(приложение 3 к Регламенту)" in a.answer for a in nested)


def _pdf(lines: list[str]) -> bytes:
    """Настоящий PDF с текстом на кириллице — для боевого пути sniff через pdfminer.

    Шрифт не встроен: коды 128–255 перекодированы (Differences) в именованные
    глифы, по их именам pdfminer восстанавливает буквы. Имя шрифта не из
    стандартных четырнадцати — иначе pdfminer взял бы ширины Helvetica, где
    кириллицы нет, и буквы слиплись бы в одну точку.
    """
    names: dict[str, str] = {}
    for name, char in sorted(glyphname2unicode.items()):
        names.setdefault(char, name)
    extra = sorted({char for line in lines for char in line if ord(char) > 126})
    codes = {char: 128 + i for i, char in enumerate(extra)}

    def encode(text: str) -> bytes:
        out = bytearray()
        for char in text:
            if char in codes:
                out.append(codes[char])
                continue
            if char in "()\\":
                out.append(0x5C)
            out += char.encode("ascii")
        return bytes(out)

    stream = b"BT /F1 10 Tf 12 TL 40 800 Td " + b" T* ".join(b"(" + encode(line) + b") Tj" for line in lines)
    stream += b" ET"
    differences = " ".join("/" + names[char] for char in extra)
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] "
        b"/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
        b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
        (
            "<< /Type /Font /Subtype /Type1 /BaseFont /TestCyrillic /FirstChar 32 /LastChar 255 "
            f"/Widths [{' '.join(['500'] * 224)}] /FontDescriptor 6 0 R "
            f"/Encoding << /Type /Encoding /Differences [128 {differences}] >> >>"
        ).encode("ascii"),
        b"<< /Type /FontDescriptor /FontName /TestCyrillic /Flags 32 /FontBBox [0 -200 1000 800] "
        b"/ItalicAngle 0 /Ascent 800 /Descent -200 /CapHeight 700 /StemV 80 >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % number + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    out += b"".join(b"%010d 00000 n \n" % offset for offset in offsets)
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objects) + 1, xref)
    return bytes(out)


ACT_LINES = [
    "РЕГЛАМЕНТ ИНФОРМАЦИОННОГО ВЗАИМОДЕЙСТВИЯ",
    "1. Общие положения",
    "1.1. Настоящий Регламент определяет порядок работы участников.",
    *(f"1.{n}. Участник соблюдает требования настоящего Регламента в части {n}." for n in range(2, 14)),
]
MANUAL_LINES = [
    "Инструкция по работе с Порталом для поставщика",
    *(f"3.{n}. Откройте раздел и нажмите кнопку Создать (Рисунок {n})." for n in range(1, 21)),
]


@pytest.mark.asyncio
async def test_readable_pdf_goes_to_regulation_and_manual_to_pdf_figures():
    """Боевой путь: sniff читает первые страницы PDF через pdfminer. Регламент
    обязан достаться regulation (0.88), а не pdf_figures (0.85), который берёт
    любой PDF, — иначе docling порежет его как методичку. Инструкция с
    рисунками — наоборот."""
    act = await registry.ranked(FileBlob("reglament.pdf", _pdf(ACT_LINES)))
    manual = await registry.ranked(FileBlob("instrukciya.pdf", _pdf(MANUAL_LINES)))

    assert act[0][1].name == "regulation"
    assert manual[0][1].name == "pdf_figures"
    assert "regulation" not in {source.name for _, source in manual}


# ---------------------------------------------------------------- источник


class _RuntimeWithoutModel:
    """Модель не настроена: generate_question обязан вернуть наш же вопрос."""

    dyn = {"llm.base_url": "", "llm.model": ""}


@pytest.mark.asyncio
async def test_parse_gives_authored_answer_records():
    source = RegulationSource()
    blob = FileBlob("reglament.txt", TEXT.encode("utf-8"))
    ctx = IngestContext(params={"use_llm": False, "audience": "supplier", "line": 1})

    records = [record async for record in source.parse(blob, ctx)]

    assert len(records) == len(parse_act(TEXT))
    complaint = next(r for r in records if r.origin.locator == "прил. 2, п. 2")
    assert complaint.origin.source_name == "regulation"
    assert complaint.origin.file == "reglament.txt"
    assert complaint.origin.authored is True
    assert complaint.origin.kind == kbkind.ANSWER
    assert complaint.audience == "supplier"
    assert complaint.line == 1


@pytest.mark.asyncio
async def test_llm_branch_keeps_deterministic_questions_without_model():
    source = RegulationSource()
    blob = FileBlob("reglament.txt", TEXT.encode("utf-8"))

    with_llm = [r async for r in source.parse(blob, IngestContext(rt=_RuntimeWithoutModel(), params={}))]
    without = [r async for r in source.parse(blob, IngestContext(params={"use_llm": False}))]

    assert [r.question for r in with_llm] == [r.question for r in without]


@pytest.mark.asyncio
async def test_category_param_overrides_sections():
    source = RegulationSource()
    blob = FileBlob("reglament.txt", TEXT.encode("utf-8"))
    records = [
        r async for r in source.parse(blob, IngestContext(params={"use_llm": False, "category": "Регламент"}))
    ]
    assert {r.category for r in records} == {"Регламент"}


@pytest.mark.asyncio
async def test_text_without_clauses_is_refused():
    """Признаки акта могли обмануть sniff — без пунктов файл уходит следующему источнику."""
    blob = FileBlob("note.txt", "Настоящий Регламент определяет порядок. Настоящий Регламент важен.".encode())
    with pytest.raises(SourceMismatch):
        [r async for r in RegulationSource().parse(blob, IngestContext(params={"use_llm": False}))]


# ---------------------------------------------------------------- реальный регламент

_REAL = os.environ.get("REGLAMENT_TXT", "")


@pytest.mark.skipif(
    not (_REAL and Path(_REAL).is_file()), reason="REGLAMENT_TXT не задан — реального текста нет"
)
def test_real_reglament_smoke():
    """Текст регламента постранично («=== PAGE N»): разрывы страниц — как у PDF."""
    raw = Path(_REAL).read_text(encoding="utf-8")
    text = "\f".join(part.split("\n", 1)[1] if "\n" in part else "" for part in raw.split("=== PAGE ")[1:])
    assert regulation_signals(text).is_regulation

    articles = parse_act(text)
    assert len(articles) >= 300
    questions = {article.question: article for article in articles}
    assert "Что такое СКК?" in questions
    assert "Что такое СТП?" in questions
    assert "Что означает «Администратор пользователя»?" in questions
    complaint = questions["В какой срок арбитражная комиссия АИС «Портал поставщиков» рассматривает жалобу?"]
    assert "десяти рабочих дней" in complaint.answer
    removal = questions[
        "При каких условиях просьба о снятии котировочной сессии с публикации подлежит рассмотрению?"
    ]
    assert "не менее 5 часов" in removal.answer and "с 9.00 до 17.00" in removal.answer
    blocking = next(
        a for a in articles if "нецензурные либо оскорбительные" in a.answer and "бессрочно" in a.question
    )
    assert "без права ее обжалования" in blocking.answer
    answers = "\n".join(article.answer for article in articles)
    assert "КонсультантПлюс" not in answers and "Дата сохранения" not in answers
