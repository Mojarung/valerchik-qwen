"""Дословный ответ: отдаём авторскую формулировку, а не пересказ."""

import pytest

from app.services.retrieval import RetrievedDoc
from app.services.verbatim import (
    is_same_question,
    pick_verbatim,
    similarity,
    visual_answer_text,
)


class FakeDyn:
    """Настройки как объект: у настоящего DynamicSettings тот же .get(key)."""

    def __init__(self, **overrides) -> None:
        self.values = {
            "answer.verbatim_enabled": True,
            "answer.verbatim_similarity": 0.75,
            "answer.verbatim_score": 0.60,
            "answer.visual_message": "Показываю на скриншоте:",
            **overrides,
        }

    def get(self, key):
        return self.values[key]


def _doc(
    question: str,
    answer: str = "Нужна ЭП и заявка в личном кабинете.",
    score: float = 0.9,
    article_id: str = "a1",
) -> RetrievedDoc:
    return RetrievedDoc(
        article_id=article_id, question=question, answer=answer, category="Регистрация", line=1, score=score
    )


def test_authored_pair_answers_verbatim():
    docs = [_doc("Как зарегистрироваться на портале?")]
    picked = pick_verbatim(FakeDyn(), "Как зарегистрироваться на портале?", docs, {"a1": {"authored": True}})
    assert picked is not None
    assert picked.answer == "Нужна ЭП и заявка в личном кабинете."


def test_collected_article_never_answers_verbatim():
    """Кусок методички, к которому вопрос придумали мы, дословно отдавать нельзя."""
    docs = [_doc("Как зарегистрироваться на портале?")]
    assert (
        pick_verbatim(FakeDyn(), "Как зарегистрироваться на портале?", docs, {"a1": {"authored": False}})
        is None
    )
    assert pick_verbatim(FakeDyn(), "Как зарегистрироваться на портале?", docs, {}) is None


def test_other_question_falls_through_to_agent():
    docs = [_doc("Как зарегистрироваться на портале?")]
    assert pick_verbatim(FakeDyn(), "Почему не приходит УПД?", docs, {"a1": {"authored": True}}) is None


def test_low_search_score_falls_through():
    docs = [_doc("Как зарегистрироваться на портале?", score=0.4)]
    assert (
        pick_verbatim(FakeDyn(), "Как зарегистрироваться на портале?", docs, {"a1": {"authored": True}})
        is None
    )


def test_empty_answer_is_not_verbatim():
    """Статья-картинка отвечает через обычный путь: дословно отдавать нечего."""
    docs = [_doc("Где кнопка «Выгрузить»?", answer="   ")]
    assert pick_verbatim(FakeDyn(), "Где кнопка «Выгрузить»?", docs, {"a1": {"authored": True}}) is None


def test_toggle_disables_the_whole_path():
    docs = [_doc("Как зарегистрироваться на портале?")]
    assert (
        pick_verbatim(
            FakeDyn(**{"answer.verbatim_enabled": False}),
            "Как зарегистрироваться на портале?",
            docs,
            {"a1": {"authored": True}},
        )
        is None
    )


def test_no_docs_is_not_an_error():
    assert pick_verbatim(FakeDyn(), "Любой вопрос", [], {}) is None


def test_similarity_ignores_case_and_punctuation():
    assert similarity("Как выгрузить УПД?", "как выгрузить упд") > 0.9
    assert similarity("Как выгрузить УПД?", "Как выгрузить УПД") > 0.9


def test_similarity_separates_questions_sharing_service_words():
    """«Как создать сессию» и «как создать оферту» — разные вопросы.

    Без отсева служебных слов их совпадение перевалило бы за порог, и человек
    получил бы чужой готовый ответ с высокой уверенностью.
    """
    assert similarity("Как создать котировочную сессию?", "Как создать оферту?") < 0.75
    assert similarity("Как выгрузить УПД?", "Что такое котировочная сессия?") < 0.3


def test_picture_only_article_is_answerable():
    """Ответ — картинка: текста нет, но отдать статью можно и нужно."""
    docs = [_doc("Где кнопка «Выгрузить»?", answer="")]
    assert (
        pick_verbatim(
            FakeDyn(),
            "Где кнопка «Выгрузить»?",
            docs,
            {"a1": {"authored": True}},
            {"a1": [{"url": "/media/a.png", "caption": "Кнопка"}]},
        )
        is not None
    )


def test_picture_only_article_without_images_is_not_answerable():
    docs = [_doc("Где кнопка «Выгрузить»?", answer="")]
    assert (
        pick_verbatim(FakeDyn(), "Где кнопка «Выгрузить»?", docs, {"a1": {"authored": True}}, {"a1": []})
        is None
    )


FALSE_MATCHES = [
    # Одно «не» — и это другое обращение с другим ответом, а Жаккар видит 0.83.
    ("Что делать, если контракт подписан в срок?", "Что делать, если контракт не подписан в срок?"),
    # Ответ для заказчика уехал бы поставщику: лишнее слово стоит всего 0.25.
    ("Как отменить котировочную сессию поставщику?", "Как отменить котировочную сессию?"),
    # Антоним в длинном вопросе: чем длиннее вопрос, тем дешевле подмена смысла.
    (
        "Как поставщику расторгнуть подписанный контракт в личном кабинете портала?",
        "Как поставщику продлить подписанный контракт в личном кабинете портала?",
    ),
    (
        "Можно ли изменить цену контракта после подписания сторонами?",
        "Можно ли изменить цену контракта до подписания сторонами?",
    ),
]


@pytest.mark.parametrize("asked, authored", FALSE_MATCHES)
def test_similar_but_different_question_never_answers_verbatim(asked, authored):
    """Похожий вопрос — не тот же вопрос.

    Все четыре пары проходят порог схожести 0.75 (0.75–0.83), и до появления
    проверки покрытия бот отдавал на них готовый ответ на ЧУЖОЙ вопрос — с
    оценкой уверенности около единицы. Уверенный ответ не на тот вопрос хуже
    честного отказа: человек не может его перепроверить.
    """
    assert similarity(asked, authored) >= 0.75, "пара перестала быть опасной — проверьте порог"
    assert is_same_question(asked, authored) is False
    docs = [_doc(authored)]
    assert pick_verbatim(FakeDyn(), asked, docs, {"a1": {"authored": True}}) is None


def test_word_forms_and_punctuation_are_still_the_same_question():
    """Проверка покрытия не должна требовать посимвольного совпадения."""
    assert is_same_question("Как выгрузить реестр оферт?", "как выгрузить реестры оферты")
    assert is_same_question("Что такое котировочная сессия?", "Что такое котировочные сессии?")


def test_exact_pair_wins_over_a_superset_ranked_higher():
    """Поиск ставит первой статью-надмножество — дословно отвечаем всё равно точной.

    «Как подать жалобу в ФАС?» поднимает статью «...в ФАС ПОВТОРНО?» с оценкой 1.0
    выше дословно совпавшей. Отдать первую — ответить не на заданный вопрос ровно
    там, где ответ в выгрузке есть.
    """
    docs = [
        _doc("Как подать жалобу на закупку в ФАС повторно?", answer="Б", score=1.0, article_id="b1"),
        _doc("Как подать жалобу на закупку в ФАС?", answer="А", score=0.83, article_id="a1"),
    ]
    origins = {"a1": {"authored": True}, "b1": {"authored": True}}
    picked = pick_verbatim(FakeDyn(), "Как подать жалобу на закупку в ФАС?", docs, origins)
    assert picked is not None
    assert picked.answer == "А"


def test_authored_pair_below_the_top_still_answers_verbatim():
    """Авторская пара может лежать второй — она всё равно ответ на этот вопрос."""
    docs = [
        _doc(
            "Что такое котировочная сессия?", answer="Собранный кусок методички", score=1.0, article_id="doc1"
        ),
        _doc(
            "Как выгрузить реестр котировочных сессий?",
            answer="Нажмите «Экспорт».",
            score=0.7,
            article_id="qa1",
        ),
    ]
    origins = {"doc1": {"authored": False}, "qa1": {"authored": True}}
    picked = pick_verbatim(FakeDyn(), "Как выгрузить реестр котировочных сессий?", docs, origins)
    assert picked is not None and picked.answer == "Нажмите «Экспорт»."


def test_visual_text_prefers_the_author_caption():
    dyn = FakeDyn(**{"answer.visual_message": "Показываю на скриншоте:"})
    assert (
        visual_answer_text(dyn, [{"url": "/a.png", "caption": "Кнопка в карточке"}])
        == "Показываю на скриншоте: Кнопка в карточке"
    )
    # Без подписи остаётся фраза из настроек: пустой пузырь выглядел бы сбоем
    assert visual_answer_text(dyn, [{"url": "/a.png"}]) == "Показываю на скриншоте:"


def test_polite_wrapping_does_not_switch_off_verbatim():
    """«Подскажите, как ...» — тот же вопрос. Люди пишут именно так.

    Симметричное покрытие оказалось лекарством хуже болезни: одно вежливое слово
    выключало главную фичу молча, и вместо ответа из файла человек получал
    пересказ модели.
    """
    for wording in (
        "Подскажите, как зарегистрироваться на портале?",
        "Здравствуйте! Подскажите пожалуйста как зарегистрироваться на портале",
        "как зарегистрироваться на портале",
    ):
        assert is_same_question(wording, "Как зарегистрироваться на портале?"), wording


def test_words_that_change_the_meaning_still_block_verbatim():
    """Лишнее слово прощается, только если оно не меняет смысл обращения."""
    blocked = [
        ("Как создать котировочную сессию заказчику?", "Как создать котировочную сессию?"),
        ("Можно ли изменить цену после подписания?", "Можно ли изменить цену до подписания?"),
        ("Что делать, если контракт не подписан в срок?", "Что делать, если контракт подписан в срок?"),
        ("Как подать повторную жалобу на заказчика?", "Как подать жалобу на заказчика?"),
    ]
    for asked, article in blocked:
        assert not is_same_question(asked, article), asked


def test_article_must_be_covered_by_the_question():
    """Вопрос статьи уже вопроса пользователя — значит спрашивали не про это."""
    assert not is_same_question(
        "Как выгрузить реестр?", "Как выгрузить реестр котировочных сессий заказчику?"
    )
