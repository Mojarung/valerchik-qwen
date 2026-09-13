"""Нормализация запроса: опечатки по словарю базы и сокращения предметной области."""

import random

import pytest

from app.services import querytext
from app.services.querytext import (
    Speller,
    align_concepts,
    concept_of,
    concept_spans,
    expand_abbreviations,
    normalize_query,
    phrase_concepts,
    replacing_concepts,
)

ARTICLE_TEXTS = [
    "Как зарегистрироваться на портале поставщиков? Регистрация через Госуслуги.",
    "Портал не видит электронную подпись: проверьте КриптоПро и плагин браузера.",
    "Как загрузить документ в личный кабинет поставщика",
    "Котировочная сессия: подача предложения и срок подписания контракта",
]


@pytest.fixture(scope="module")
def speller() -> Speller:
    built = Speller()
    built.build(ARTICLE_TEXTS)
    return built


def test_typos_are_fixed_towards_kb_words(speller):
    assert (
        speller.correct("зарегестрироватся на партале поставщеков")
        == "зарегистрироваться на портале поставщиков"
    )


def test_dictionary_words_are_never_fixed(speller):
    """«погода» — не опечатка, а вопрос не про портал: исправь его в слово базы, и
    бот уверенно ответит статьёй на вопрос, которого ему не задавали. «загрузит» —
    правильная форма глагола, хотя в базе есть только «загрузить»."""
    for word in ("погода", "вычет", "загрузит", "налоговый"):
        assert speller.fix_word(word) == word


def test_short_words_are_not_fixed():
    """У слова короче пяти букв соседей на одну правку слишком много: «сррк»
    может быть чем угодно, хотя от «срок» его отделяет одна буква."""
    speller = Speller()
    speller.build(["срок подачи заявки"])
    assert speller.fix_word("сррк") == "сррк"
    assert speller.fix_word("подаич") == "подачи"  # то же расстояние, но слово длиннее


def test_numbers_and_latin_are_left_alone():
    """Латиницу словарь русского языка не знает, и защиты «это настоящее слово»
    для неё нет: «browsr» ушёл бы в ближайшее латинское слово статей."""
    speller = Speller()
    speller.build(["yandex browser chrome ошибка 0x80090016 сертификат"])
    assert speller.fix_word("browsr") == "browsr"
    assert speller.correct("ошибка 0x80090017 в chrom, код 12345") == "ошибка 0x80090017 в chrom, код 12345"


def test_empty_vocabulary_changes_nothing():
    assert Speller().correct("зарегестрироватся на партале") == "зарегестрироватся на партале"


def test_new_vocabulary_forgets_old_fixes():
    """Кэш исправлений принадлежит словарю: после переиндексации старое
    исправление в слово, которого в базе больше нет, не должно всплывать."""
    speller = Speller()
    speller.build(["котировочная сессия"])
    assert speller.fix_word("котеровочная") == "котировочная"
    speller.build(["совсем другая статья"])
    assert speller.fix_word("котеровочная") == "котеровочная"


def _reference_distance(a: str, b: str) -> int:
    """Дамерау–Левенштейн (перестановка соседних букв) полным перебором таблицы."""
    d = [[0] * (len(b) + 1) for _ in range(len(a) + 1)]
    for i in range(len(a) + 1):
        d[i][0] = i
    for j in range(len(b) + 1):
        d[0][j] = j
    for i in range(1, len(a) + 1):
        for j in range(1, len(b) + 1):
            d[i][j] = min(d[i - 1][j] + 1, d[i][j - 1] + 1, d[i - 1][j - 1] + (a[i - 1] != b[j - 1]))
            if i > 1 and j > 1 and a[i - 1] == b[j - 2] and a[i - 2] == b[j - 1]:
                d[i][j] = min(d[i][j], d[i - 2][j - 2] + 1)
    return d[len(a)][len(b)]


def _reference_fix(counts: dict[str, int], low: str) -> str:
    """Исправление общим перебором словаря — как было до ускорения."""
    limit = 2 if len(low) >= querytext.TWO_EDITS_FROM else 1
    ranked = [
        (distance, -count, word)
        for word, count in counts.items()
        if word[0] == low[0] and (distance := _reference_distance(low, word)) <= limit
    ]
    return min(ranked)[2] if ranked else low


def test_fast_search_finds_what_full_search_finds(monkeypatch):
    """Отсев по набору букв, полоса вокруг диагонали и поиск сначала на одну
    правку — только ускорение: исправление то же, что у полного перебора."""
    monkeypatch.setattr(querytext, "is_dictionary_word", lambda word: False)
    rng = random.Random(2026)
    alphabet = "абвгдежзийклмнопрстуфхцчшщъыьэюя"
    words = sorted(
        {w for text in ARTICLE_TEXTS for w in text.lower().replace("ё", "е").split() if w.isalpha()}
    )
    words += ["".join(rng.choice(alphabet) for _ in range(rng.randint(4, 14))) for _ in range(300)]
    speller = Speller()
    speller.build([" ".join(words)])
    counts = {w: n for w, n in speller._vocab.counts.items() if w.isalpha()}

    def typo(word: str) -> str:
        chars = list(word)
        for _ in range(rng.randint(1, 3)):
            pos = rng.randrange(1, len(chars))  # первую букву не трогаем, как и поиск
            action = rng.choice("sidt")
            if action == "s":
                chars[pos] = rng.choice(alphabet)
            elif action == "i":
                chars.insert(pos, rng.choice(alphabet))
            elif action == "d" and len(chars) > 2:
                del chars[pos]
            elif action == "t" and pos + 1 < len(chars):
                chars[pos], chars[pos + 1] = chars[pos + 1], chars[pos]
        return "".join(chars)

    checked = 0
    for word in rng.sample(words, 200):
        wrong = typo(word)
        if len(wrong) < querytext.MIN_FIX_LEN or wrong in counts:
            continue
        assert speller.fix_word(wrong) == _reference_fix(counts, wrong), wrong
        checked += 1
    assert checked > 100


def test_abbreviations_are_expanded_once():
    text = expand_abbreviations("Не работает ЭП, а ЭЦП куплена")
    assert text.startswith("Не работает ЭП, а ЭЦП куплена")
    assert text.count("электронная подпись") == 1


@pytest.mark.parametrize(
    ("question", "expansion"),
    [
        ("Как оформить МЧД?", "машиночитаемая доверенность"),
        ("не открывается ЛК", "личный кабинет"),
        ("Когда закончится КС", "котировочная сессия"),
        ("ЭЦП не видна", "электронная подпись"),
    ],
)
def test_domain_abbreviations(question, expansion):
    assert expand_abbreviations(question) == f"{question} {expansion}"


def test_text_without_abbreviations_is_unchanged():
    """Сокращение — только целое слово: «эпизод» не ЭП, «кслота» не КС."""
    assert expand_abbreviations("эпизод с кслотой и погода") == "эпизод с кслотой и погода"


def test_abbreviation_and_full_form_are_one_concept():
    """Индекс видит одно понятие, как бы его ни написали: сокращением, другим
    сокращением или полностью в любом падеже."""
    assert concept_of("эцп") == concept_of("эп") == "эп"
    assert concept_of("окпд2") == "окпд"
    assert concept_of("подпись") is None
    assert phrase_concepts("портал не видит электронную подпись") == ["эп"]
    assert phrase_concepts("вход в личного кабинета нет") == ["лк"]
    assert phrase_concepts("итоги по котировочным сессиям") == ["кс"]
    assert phrase_concepts("закупок малого объема") == ["змо"]


def test_nested_concepts_are_all_found():
    found = phrase_concepts("нужна усиленная квалифицированная электронная подпись")
    assert sorted(found) == ["кэп", "укэп", "эп"]


def test_download_synonyms_are_one_concept():
    """«выгрузить», «скачать», «экспортировать» — одно понятие для лексического
    поиска, иначе бот находит статью только с одним из трёх слов и не с другими:
    на проде запрос «выгрузить УПД» терял статью «Где можно скачать XML-версию
    УПД?» из топ-5 целиком, уступая по kw-score не связанной с УПД статье
    «9.1.1 Выгрузка оферт»."""
    assert phrase_concepts("как выгрузить упд") == ["_download"]
    assert phrase_concepts("хочу скачать упд") == ["_download"]
    assert phrase_concepts("нужно экспортировать упд для бухгалтера") == ["_download"]
    assert phrase_concepts("где выгрузка упд") == ["_download"]
    assert phrase_concepts("заказчик выгружает упд каждый день") == ["_download"]
    assert phrase_concepts("скачивание упд не работает") == ["_download"]
    assert phrase_concepts("экспорт упд в 1с") == ["_download"]


def test_only_action_synonyms_replace_their_words():
    """Сокращение и его расшифровка — одно и то же слово в разных формах, слова
    остаются рядом с ключом. Члены группы синонимов — разные слова, и оставленное
    слово перевешивает ключ вдвое у той статьи, что написала его буквально, —
    такой ключ слова вытесняет (retrieval.index_tokens)."""
    assert replacing_concepts() == frozenset({"_download"})


def test_concept_spans_say_where_the_concept_is_written():
    """Позиции нужны вытеснению: без них ключ можно только дописать."""
    assert concept_spans(["как", "выгрузить", "упд"]) == [("_download", 1, 1)]
    assert concept_spans(["портал", "не", "видит", "электронную", "подпись"]) == [("эп", 3, 2)]
    # Вложенные понятия — пересекающиеся спаны, каждый со своей длиной
    assert concept_spans(["нужна", "усиленная", "квалифицированная", "электронная", "подпись"]) == [
        ("укэп", 1, 4),
        ("кэп", 2, 3),
        ("эп", 3, 2),
    ]


def test_download_synonym_key_survives_alignment():
    """После вытеснения слов у синонима нет полной формы ни с одной стороны, и
    align_concepts идёт веткой сокращения: ключ остаётся. Снять его значило бы
    оборвать единственную связь между вопросом «выгрузить» и заголовком «скачать»."""
    assert align_concepts({"_download", "упд"}, {"_download", "упд", "версию"}) == (
        {"_download", "упд"},
        {"_download", "упд", "версию"},
    )


def test_abbreviation_against_full_form_is_one_key():
    """«ЭП» против «электронной подписи»: обе стороны сводятся к одному ключу.
    Понятие весит как «ЭП» против «ЭП», а не как два-три совпавших слова."""
    question, title = align_concepts({"где", "получи", "эп"}, {"портал", "видит", "электр", "подпис", "эп"})
    assert question == {"где", "получи", "эп"}
    assert title == {"портал", "видит", "эп"}
    # Сокращение в заголовке, полная форма в вопросе — то же самое
    question, title = align_concepts({"где", "получи", "электр", "подпис", "эп"}, {"где", "получи", "эп"})
    assert question == title == {"где", "получи", "эп"}


def test_full_form_on_both_sides_counts_once():
    """Слова полной формы уже несут понятие: ключ поверх них — лишнее совпадение,
    и заголовок «Какая электронная подпись нужна» обходил «Как подписать контракт»
    на вопрос «как подписать контракт электронной подписью»."""
    question, title = align_concepts(
        {"подпис", "контра", "электр", "эп"}, {"электр", "подпис", "эп", "нужна", "портал"}
    )
    assert question == {"подпис", "контра", "электр"}
    assert title == {"электр", "подпис", "нужна", "портал"}


def test_abbreviation_against_abbreviation_is_one_token():
    """«УПД» против «УПД» — одно понятие, а не четыре слова."""
    assert align_concepts({"принял", "упд"}, {"принят", "упд"}) == ({"принял", "упд"}, {"принят", "упд"})
    # Понятия нет с одной из сторон — «документ» не становится «УПД»
    assert align_concepts({"упд"}, {"докуме"}) == ({"упд"}, {"докуме"})
    # Полная форма без пары — только слова, как было до понятий
    assert align_concepts({"электр", "подпис", "эп"}, {"подпис", "работа"}) == (
        {"электр", "подпис"},
        {"подпис", "работа"},
    )


def test_near_words_do_not_make_a_concept():
    """«Электронной почте» — не подпись, «электронной подачей» — тоже: короткие
    основы не укорачиваются, иначе «под» совпало бы с чем угодно."""
    assert phrase_concepts("пришло на электронную почту") == []
    assert phrase_concepts("электронной подачей заявки") == []
    assert phrase_concepts("") == []


def test_normalize_query_fixes_then_expands(speller):
    assert normalize_query("зарегестрироватся в ЛК", speller) == "зарегистрироваться в ЛК личный кабинет"
    assert normalize_query("не видит ЭП", None) == "не видит ЭП электронная подпись"


def test_warm_loads_morphology():
    """Прогрев грузит настоящий словарь, а не запоминает его отсутствие: в кэше
    мог бы лежать и None — тогда опечатки не исправлялись бы вовсе."""
    querytext.warm()
    assert querytext._morph() is not None
    assert querytext.is_dictionary_word("погода")


def test_morphology_is_shared_with_guardrails():
    """Один морфоанализатор на процесс: словари занимают память и грузятся секунду."""
    from app.services import guardrails

    assert querytext._morph() is guardrails.morph()


def test_without_morphology_nothing_is_fixed(speller, monkeypatch):
    """Без словаря русского языка не отличить опечатку от настоящего слова:
    «погода» ушла бы в ближайшее слово базы. Тогда не исправляем ничего."""
    monkeypatch.setattr(querytext, "_morph", lambda: None)
    speller._vocab.fixes.clear()
    assert speller.correct("зарегестрироватся на партале") == "зарегестрироватся на партале"
    speller._vocab.fixes.clear()


def test_colloquial_words_are_not_fixed():
    """«учетку» — не опечатка «учету», «принт» — не «принят»: разговорных слов
    нет ни в словаре, ни в статьях, и ближайшее слово базы у них всегда чужое."""
    speller = Speller()
    speller.build(["по учету документов заказ принят и подписала заказчик"])
    for word in ("учетку", "принт", "подвисала", "перезайти"):
        assert speller.fix_word(word) == word


def test_two_edits_only_for_long_words():
    """Две правки в слове из девяти-десяти букв на выгрузке СТП чаще давали другое
    слово, чем исправление: «нерешение» — не «нарушение»."""
    speller = Speller()
    speller.build(["нарушение сроков по заявке зарегистрироваться"])
    assert speller.fix_word("нерешение") == "нерешение"
    assert speller.fix_word("зарегестрироватся") == "зарегистрироваться"
