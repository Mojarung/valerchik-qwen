"""Уточняющий вопрос вместо ответа наугад: узкая полоса срабатывания и предохранители."""

import time

import pytest

from app.chat import steps
from app.services import kbkind
from app.services.retrieval import RetrievedDoc

# Ответы у статей разные: уточнение решает именно разница ответов. Одинаковый
# текст у всех статей означал бы «выбирать не из чего», и уточнения не было бы.
_ANSWERS = {
    "оферту": "Оферта снимается с публикации в её карточке кнопкой «Снять с публикации».",
    "сессию": "Котировочную сессию отменяет заказчик до окончания приёма предложений.",
}


def _docs(*specs: tuple[str, str, float]) -> list[RetrievedDoc]:
    return [
        RetrievedDoc(
            article_id=aid,
            question=f"Как отменить {aid}",
            answer=_ANSWERS.get(aid, f"Ответ по теме «{aid}»"),
            category=category,
            line=1,
            score=score,
        )
        for aid, category, score in specs
    ]


def _doc(aid: str, question: str, answer: str, score: float, kind: str = kbkind.ANSWER) -> RetrievedDoc:
    return RetrievedDoc(
        article_id=aid, question=question, answer=answer, category="Тема", line=1, score=score, kind=kind
    )


OFFER = _doc("оферта", "Как снять оферту с публикации?", _ANSWERS["оферту"], 0.50)
SESSION = _doc("сессия", "Как отменить котировочную сессию?", _ANSWERS["сессию"], 0.49)
CONTRACT = _doc(
    "контракт",
    "Как расторгнуть контракт?",
    "Расторжение оформляется соглашением сторон в карточке контракта.",
    0.48,
)


def _session(client) -> str:
    return client.post("/api/chat/sessions").json()["session_id"]


async def test_close_scores_from_different_topics_ask_instead_of_guessing(client):
    """«Как отменить» — это и оферта, и котировочная сессия. Гадать нельзя."""
    rt = client.app.state.rt
    sid = _session(client)
    docs = _docs(("оферту", "Оферты", 0.50), ("сессию", "Котировочные сессии", 0.48))

    result = await rt.chat._maybe_clarify(sid, "как отменить", docs, time.perf_counter())

    assert result["status"] == "clarify"
    assert [o["text"] for o in result["options"]] == ["Как отменить оферту", "Как отменить сессию"]
    # Варианты обязаны быть в самом тексте: в голосовом режиме кнопок нет
    assert "Как отменить оферту" in result["answer"]
    assert "Как отменить сессию" in result["answer"]

    saved = client.get(f"/api/chat/sessions/{sid}/messages").json()[-1]
    assert saved["meta"]["kind"] == "clarify"
    assert saved["meta"]["answered"] is False, "уточнение — не автоответ"


async def test_never_asks_twice_in_a_row(client):
    """Второе уточнение подряд — это уже допрос, а не помощь."""
    rt = client.app.state.rt
    sid = _session(client)
    docs = _docs(("оферту", "Оферты", 0.50), ("сессию", "Котировочные сессии", 0.48))

    assert await rt.chat._maybe_clarify(sid, "как отменить", docs, time.perf_counter())
    assert await rt.chat._maybe_clarify(sid, "как отменить", docs, time.perf_counter()) is None


async def test_confident_match_is_answered_not_questioned(client):
    rt = client.app.state.rt
    docs = _docs(("оферту", "Оферты", 0.94), ("сессию", "Котировочные сессии", 0.93))
    assert (
        await rt.chat._maybe_clarify(_session(client), "как отменить оферту", docs, time.perf_counter())
        is None
    )


async def test_below_answer_threshold_is_a_refusal_not_a_choice(client):
    """Ниже порога ответа выдача — шум, и близкие оценки означают не двусмысленность
    вопроса, а отсутствие ответа. Выбор из двух случайных статей хуже отказа."""
    rt = client.app.state.rt
    docs = _docs(("оферту", "Оферты", 0.30), ("сессию", "Котировочные сессии", 0.29))
    assert rt.dyn.get("agent.confidence_threshold") > 0.30, "тест опирается на порог 0.4"
    assert (
        await rt.chat._maybe_clarify(_session(client), "как испечь пиццу", docs, time.perf_counter()) is None
    )


async def test_nearly_identical_answers_are_answered_not_questioned(client):
    """Две статьи с почти одним ответом — выбирать не из чего, отвечаем лучшей.

    Рубрики тут разные, и раньше этого хватало для «выберите из двух». Выгрузка
    поддержки кладёт одну проблему в несколько рубрик, и человек получал на выбор
    две формулировки одного и того же.
    """
    rt = client.app.state.rt
    same = "Оферта снимается с публикации в её карточке кнопкой «Снять с публикации»."
    docs = [
        _doc("a", "Как снять оферту с публикации?", same, 0.50),
        _doc("b", "Где убрать оферту из каталога?", same.replace("её карточке", "карточке оферты"), 0.49),
    ]
    docs[1].category = "Каталог"
    assert await rt.chat._maybe_clarify(_session(client), "убрать оферту", docs, time.perf_counter()) is None


async def test_same_category_with_different_answers_is_still_ambiguity(client):
    """Одна рубрика — ещё не один ответ: решает текст ответа, а не шумная категория."""
    rt = client.app.state.rt
    docs = [OFFER, SESSION]
    result = await rt.chat._maybe_clarify(_session(client), "как отменить", docs, time.perf_counter())
    assert result is not None
    assert [o["article_id"] for o in result["options"]] == ["оферта", "сессия"]


RAW = _doc(
    "сырое",
    "Добрый вечер. Опять повторяется одна и та же проблема - скрин прилагаю…",
    "Проверьте, что в браузере разрешены всплывающие окна портала.",
    0.495,
)
LONG = _doc(
    "длинное",
    "Как отменить " + "очень подробно описанную ситуацию с документами, " * 3 + "если заказчик не отвечает?",
    "Направьте заказчику уведомление через раздел «Исполнение контракта».",
    0.49,
)
TICKET = _doc(
    "номер",
    "Почему заявка 7712345678 не отображается?",
    "Заявка появляется в реестре после подписания электронной подписью.",
    0.49,
)


async def test_raw_long_and_numbered_questions_are_not_options(client):
    """Сырое обращение, пересказ длиннее 120 символов и номер чужой заявки кнопкой не бывают."""
    rt = client.app.state.rt
    docs = [OFFER, RAW, LONG, TICKET, SESSION]
    result = await rt.chat._maybe_clarify(_session(client), "как отменить", docs, time.perf_counter())
    assert result is not None
    assert [o["article_id"] for o in result["options"]] == ["оферта", "сессия"]
    assert "Добрый вечер" not in result["answer"]


async def test_no_clarify_without_two_presentable_options(client):
    """Остался один достойный вариант — это не выбор, отвечаем обычным путём."""
    rt = client.app.state.rt
    docs = [OFFER, RAW, LONG, TICKET]
    assert await rt.chat._maybe_clarify(_session(client), "как отменить", docs, time.perf_counter()) is None


async def test_best_article_must_be_among_options(client):
    """Первая статья с сырым вопросом: выбор без неё прятал бы самый вероятный ответ."""
    rt = client.app.state.rt
    raw_top = _doc("сырое", RAW.question, RAW.answer, 0.50)
    docs = [raw_top, SESSION, CONTRACT]
    assert await rt.chat._maybe_clarify(_session(client), "как отменить", docs, time.perf_counter()) is None


async def test_operator_cases_are_never_options(client):
    """Случай для оператора кнопкой быть не может: выбравший его получил бы чужую отписку."""
    rt = client.app.state.rt
    case = _doc(
        "случай",
        "Как отменить заявку на участие?",
        "На момент проверки заявка отозвана.",
        0.495,
        kbkind.OPERATOR_CASE,
    )
    docs = [OFFER, case, SESSION]
    result = await rt.chat._maybe_clarify(_session(client), "как отменить", docs, time.perf_counter())
    assert result is not None
    assert "случай" not in [o["article_id"] for o in result["options"]]


async def test_duplicate_options_collapse_into_one(client):
    """Третья статья повторяет ответ первой — второй раз её не предлагаем.

    Двойник стоит третьим: будь он вторым, две лучшие статьи дали бы один ответ,
    и уточнения не было бы вовсе (см. тест про почти одинаковые ответы).
    """
    rt = client.app.state.rt
    twin = _doc("двойник", "Как убрать оферту?", OFFER.answer, 0.485)
    docs = [OFFER, SESSION, twin]
    result = await rt.chat._maybe_clarify(_session(client), "как отменить", docs, time.perf_counter())
    assert result is not None
    assert [o["article_id"] for o in result["options"]] == ["оферта", "сессия"]


#: Инструкции портала по одному шаблону: общих слов под 0.9, действия противоположные
BLOCK = _doc(
    "блок",
    "Как заблокировать компанию в профиле Уполномоченного органа?",
    "Чтобы заблокировать компанию, перейдите в блок «Управление» — «Заказчики», выберите "
    "организацию и нажмите кнопку «Заблокировать».",
    0.50,
)
UNBLOCK = _doc(
    "разблок",
    "Как разблокировать компанию в профиле Уполномоченного органа?",
    "Чтобы разблокировать компанию, перейдите в блок «Управление» — «Заказчики», выберите "
    "организацию и нажмите кнопку «Разблокировать».",
    0.49,
)


async def test_template_answers_about_opposite_actions_are_a_real_choice(client):
    """«Заблокировать» и «разблокировать» — не один ответ, хоть слова почти все общие.

    По доле общих слов пара выглядела одним ответом: бот не переспрашивал, а
    отвечал первой статьёй, и человек, хотевший разблокировать, получал
    инструкцию блокировки. И кнопками их нельзя склеивать как дубли.
    """
    rt = client.app.state.rt
    assert steps._word_overlap(BLOCK.answer, UNBLOCK.answer) >= rt.dyn.get("clarify.same_answer_overlap")

    result = await rt.chat._maybe_clarify(
        _session(client), "блокировка компании", [BLOCK, UNBLOCK], time.perf_counter()
    )

    assert result is not None
    assert [o["article_id"] for o in result["options"]] == ["блок", "разблок"]


@pytest.mark.parametrize(
    "question",
    [
        # Сырые обращения, которые парсер выгрузки оставил вопросами (замер ревьюера)
        "Ещё 22.05. была создана сте а сдвигов никаких нет. Больнице надо отгрузить товар, а мы не можем",
        "Не можем подписать оферту. Помогите.",
        "Мне нужно создать СТЕ, но в категории работы.",
        "Срок шестичасовой котировочной сессии самопроизвольно увеличился на два часа.НА КАКОМ ОСНОВАНИИ?",
        "У нашей компании сменились банковские реквизиты.",
        "Почему СТЕ не отправляется на проверку, нажимаю кнопку и ни чего не происходит?",
        "Направили приглашение в ЭДО.",
        "Утвердите, пожалуйста заявку на СТЕ",
        "При отправке документов в ЕИС появляется ошибка (скрин прилагается в файле)",
        "E_7UH8CgvH Подписать исполнение может только подписант",
        "Консультация",
    ],
)
def test_raw_appeals_are_not_presentable(question):
    assert not steps._presentable_question(question, 120)


@pytest.mark.parametrize(
    "question",
    [
        "Как зарегистрироваться в качестве поставщика физическому лицу?",
        "Ошибка РДИК_0161: ошибка с контрактом",
        "Не приходят уведомления на почту",
        "Почему по окончании котировочной сессии появился другой победитель?",
        "Как произвести настройку плагина КРИПТОПРО?",
        "Подписание контракта после отказа от заключения со стороны заказчика.",
        # Цитата ошибки с экрана — не голос спрашивающего, её «Пожалуйста» не в счёт
        "При регистрации возникает ошибка «Этот логин уже используется. Пожалуйста, выберите другой»",
        "Что означает статус в котировочной сессии «Идет обработка ставок. Возможна переторжка»?",
    ],
)
def test_authored_questions_stay_presentable(question):
    assert steps._presentable_question(question, 120)


async def test_far_behind_article_is_not_an_option(client):
    """Варианты — только из статей вплотную к первой: отставшая — шум, а не соперник."""
    rt = client.app.state.rt
    far = _doc("далёкая", "Как расторгнуть контракт?", CONTRACT.answer, 0.30)
    docs = [OFFER, SESSION, far]
    result = await rt.chat._maybe_clarify(_session(client), "как отменить", docs, time.perf_counter())
    assert result is not None
    assert [o["article_id"] for o in result["options"]] == ["оферта", "сессия"]


async def test_clear_leader_is_not_ambiguity(client):
    rt = client.app.state.rt
    docs = _docs(("оферту", "Оферты", 0.50), ("сессию", "Котировочные сессии", 0.20))
    assert await rt.chat._maybe_clarify(_session(client), "как отменить", docs, time.perf_counter()) is None


async def test_request_for_a_human_is_never_questioned(client):
    """Переспрашивать в ответ на «позовите оператора» — издевательство."""
    rt = client.app.state.rt
    docs = _docs(("оферту", "Оферты", 0.50), ("сессию", "Котировочные сессии", 0.48))
    assert (
        await rt.chat._maybe_clarify(_session(client), "позовите оператора", docs, time.perf_counter())
        is None
    )


async def test_product_request_is_not_answered_with_a_clarifying_question(client, monkeypatch):
    """Уточнение стояло раньше решения про внешние инструменты и перехватывало всё.

    «найди машины до 1.5м» ложилось на две статьи про закупки с оценками 0.501 и
    0.500 — ровно полоса уточнения, — и человек вместо каталога получал вопрос
    про ЗМО. У такого запроса нет двусмысленности, которую стоит выяснять: есть
    каталог, где надо посмотреть.
    """
    rt = client.app.state.rt
    docs = _docs(("змо", "Закупки", 0.501), ("обеспечение", "Регламент", 0.500))

    async def probe(text, page_context="", audience=""):
        return docs

    monkeypatch.setattr(rt.chat, "_probe_kb", probe)

    # тот же набор статей на вопрос о правилах — уточнение уместно и срабатывает
    assert (
        await rt.chat._maybe_clarify(_session(client), "как отменить", docs, time.perf_counter()) is not None
    )

    result = await rt.chat.handle_user_message(_session(client), "найди машины до 1.5м")
    assert result["status"] != "clarify", "товарный запрос ушёл в уточнение вместо каталога"
