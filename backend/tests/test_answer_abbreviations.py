"""Generated acronym explanations must be grounded in the actual source text."""

import pytest

from app.services.answer_abbreviations import strip_unsupported_expansions


@pytest.mark.parametrize(
    "answer,expected",
    [
        ("Подпишите УКЭП (уникальная криптографическая электронная подпись).", "Подпишите УКЭП."),
        ("Подпишите укреплённой квалифицированной электронной подписью (УКЭП).", "Подпишите УКЭП."),
        ("Подпишите **УКЭП** (уникальная криптографическая электронная подпись).", "Подпишите **УКЭП**."),
    ],
)
def test_observed_unsupported_expansions_are_removed(answer, expected):
    assert strip_unsupported_expansions(answer, ["Документ необходимо подписать УКЭП."]) == expected


def test_source_supported_expansion_is_kept_across_inflections():
    source = "УКЭП — усиленная квалифицированная электронная подпись."
    answer = "Подпишите усиленной квалифицированной электронной подписью (УКЭП)."
    assert strip_unsupported_expansions(answer, [source]) == answer


@pytest.mark.parametrize(
    "answer",
    [
        "Подпишите УКЭП (при наличии).",
        "УКЭП (на компьютере сотрудника) потребуется позже.",
        "Откройте документ (подробности в справке).",
        "Проверьте сертификат (УКЭП).",
    ],
)
def test_unrelated_parentheses_are_preserved(answer):
    assert strip_unsupported_expansions(answer, ["Подпишите УКЭП."]) == answer


def test_no_source_acronym_does_not_modify_answer():
    answer = "Используйте АБВ (активная базовая версия)."
    assert strip_unsupported_expansions(answer, ["Другой материал."]) == answer


def test_before_parentheses_never_replaces_suffix_inside_hyphenated_word():
    answer = "Используйте электронно-цифровую подпись (ЦП)."
    assert strip_unsupported_expansions(answer, ["Используйте ЦП."]) == answer
