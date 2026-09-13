"""Ключевые слова кластеров и заключение отчёта по отзывам — то, что читает администратор.

Раньше ключевыми словами служили токены поиска: обрезки по шесть букв («операт»,
«подпис») вперемешку со служебными словами («очень», «почти», «так»), и те же
обрезки попадали в заключение. Здесь держим то, что видит человек: целые слова из
самих отзывов, без служебных; формы одного слова считаются вместе и показываются
самой частой формой; в заключении перечислены только такие слова.
"""

import re

import numpy as np

from app.seed.data import NEGATIVE_COMMENTS
from app.services.feedback import _compose_conclusion, _greedy_clusters

# Служебные слова, которые встречаются в демо-отзывах и в живой речи. Список свой,
# а не импорт из кода: тест должен ловить пропуск в стоп-листе, а не повторять его.
SERVICE_WORDS = {
    "очень", "почти", "это", "так", "уже", "был", "была", "было", "для", "при",
    "мой", "вообще", "том", "слишком", "себе", "все", "всё", "что", "как", "его",
    "они", "еще", "ещё", "тоже", "только", "даже", "нет", "или", "без", "про",
}  # fmt: skip

SUMMARY = {"count": 3, "avg_rating": 2.0, "positive_share": 0.0}

_WORD = re.compile(r"[а-яёa-z0-9]+")


def _words(texts):
    """Целые слова текстов так, как они написаны (в нижнем регистре, с «ё»)."""
    return {word for text in texts for word in _WORD.findall(text.lower())}


def _clusters(*groups):
    """Кластеры с заданной раскладкой: у текстов одной группы один и тот же вектор.

    Раскладку задаём сами, чтобы тест проверял слова, а не качество эмбеддера.
    """
    texts, vectors = [], []
    for position, group in enumerate(groups):
        for text in group:
            vector = np.zeros(len(groups), dtype=np.float32)
            vector[position] = 1.0
            texts.append(text)
            vectors.append(vector)
    return _greedy_clusters(np.array(vectors), texts, threshold=0.5)


def _listed_in_conclusion(conclusion):
    match = re.search(r"характеризуется словами: (.+?)\. ", conclusion)
    assert match, conclusion
    return match.group(1).split(", ")


def test_keywords_are_whole_words_from_the_comments():
    texts = [
        "Очень долго ждал оператора, почти час, это ужасно.",
        "Оператора ждал долго, ответа так и не дождался.",
        "Ждал оператора, долго.",
    ]
    (cluster,) = _clusters(texts)
    assert cluster["keywords"], "у кластера должны быть ключевые слова"
    assert set(cluster["keywords"]) <= _words(texts)
    assert "оператора" in cluster["keywords"]


def test_keyword_is_the_most_frequent_form_of_its_word():
    # «оператора» дважды, «оператор» один раз и первым — показываем «оператора»
    texts = ["Оператор не отвечает", "Оператора не дождался", "Ждал оператора полдня"]
    (cluster,) = _clusters(texts)
    assert "оператора" in cluster["keywords"]
    assert "оператор" not in cluster["keywords"]


def test_forms_of_one_short_word_count_as_one_keyword():
    # Пятибуквенные формы шестибуквенным префиксом не сходятся: «линии» и «линию»
    # выходили двумя ключевыми словами подряд. Вместе их четыре — это первое слово.
    (cluster,) = _clusters(["Перекидывают с линии на линию", "С линии на линию, и так весь день"])
    assert len({"линии", "линию"} & set(cluster["keywords"])) == 1
    assert cluster["keywords"][0] in {"линии", "линию"}


def test_keywords_of_a_real_complaint():
    # Равные по частоте слова идут в порядке появления в отзыве
    (cluster,) = _clusters(["Очень долго ждал оператора, почти час, это ужасно."] * 3)
    assert cluster["keywords"] == ["долго", "ждал", "оператора", "час", "ужасно"]


def test_seed_comment_keywords_are_whole_words():
    not_whole = {}
    for comment in NEGATIVE_COMMENTS:
        (cluster,) = _clusters([comment] * 3)
        assert cluster["keywords"], comment
        if extra := set(cluster["keywords"]) - _words([comment]):
            not_whole[comment] = sorted(extra)
    assert not not_whole, f"не целые слова: {not_whole}"


def test_seed_comment_keywords_skip_service_words():
    service = {}
    for comment in NEGATIVE_COMMENTS:
        (cluster,) = _clusters([comment] * 3)
        if found := set(cluster["keywords"]) & SERVICE_WORDS:
            service[comment] = sorted(found)
    assert not service, f"служебные слова в ключевых: {service}"


def test_comment_of_only_service_words_gives_no_keywords():
    # «Так себе» — обычный отзыв, но значимых слов в нём нет
    (cluster,) = _clusters(["Ну так себе, всё было не то."] * 3)
    assert cluster["keywords"] == []


def test_conclusion_names_only_meaningful_whole_words():
    texts = ["Очень долго ждал оператора, почти час, это ужасно."] * 3
    listed = _listed_in_conclusion(_compose_conclusion(SUMMARY, [], _clusters(texts)))
    assert listed
    assert set(listed) <= _words(texts)
    assert not set(listed) & SERVICE_WORDS


def test_conclusion_without_keywords_skips_the_word_list():
    # Пустой список не должен превращаться во фразу «характеризуется словами: .»
    cluster = {"size": 3, "representative": "Так себе.", "keywords": [], "examples": ["Так себе."]}
    conclusion = _compose_conclusion(SUMMARY, [], [cluster])
    assert "характеризуется словами" not in conclusion
    assert "Типичный отзыв: «Так себе.»" in conclusion


def _conclusion_with(cluster_count, top_size):
    cluster = {"size": top_size, "representative": "Долго ждал.", "keywords": ["долго"], "examples": []}
    return _compose_conclusion(SUMMARY, [], [cluster] * cluster_count)


def test_conclusion_counts_one_in_singular():
    # было «Выявлено 1 системных кластеров», «(1 обращений)»
    assert "Выявлен 1 системный кластер негатива; крупнейший (1 обращение)" in _conclusion_with(1, 1)


def test_conclusion_counts_two_to_four_in_genitive_singular():
    assert "Выявлено 2 системных кластера негатива; крупнейший (3 обращения)" in _conclusion_with(2, 3)


def test_conclusion_counts_twenty_one_and_teens():
    # 21 — снова «одно»; 11–14 — «много», хотя кончаются на 1–4
    assert "Выявлен 21 системный кластер негатива; крупнейший (21 обращение)" in _conclusion_with(21, 21)
    assert "Выявлено 12 системных кластеров негатива; крупнейший (11 обращений)" in _conclusion_with(12, 11)
    assert "Выявлено 5 системных кластеров негатива; крупнейший (14 обращений)" in _conclusion_with(5, 14)
