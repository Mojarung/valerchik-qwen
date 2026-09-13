"""Автоисправление забытой раскладки и связанные с ним guardrails."""

import pytest

from app.services import guardrails
from app.services.layout import convert_layout, detect_wrong_layout


@pytest.mark.parametrize(
    ("typed", "expected"),
    [
        ("Rfr pfhtubcnhbhjdfnmcz yf gjhnfkt", "Как зарегистрироваться на портале"),
        ("Xnj nfrjt rjnbhjdjxyfz ctccbz", "Что такое котировочная сессия"),
        ("gjvjubnt gj;fkeqcnf", "помогите пожалуйста"),
    ],
)
def test_fixes_text_typed_in_english_layout(typed: str, expected: str) -> None:
    assert detect_wrong_layout(typed) == expected


@pytest.mark.parametrize(
    "text",
    [
        "Как зарегистрироваться на портале?",  # уже по-русски
        "hello, how can I register on the portal?",  # осмысленный английский
        "UPD 2024",  # аббревиатура
        "https://zakupki.mos.ru/help",  # ссылка
        "test",  # слишком коротко и по-английски
        "SLA 24/7 support",
    ],
)
def test_leaves_normal_text_alone(text: str) -> None:
    assert detect_wrong_layout(text) is None


def test_conversion_is_reversible_by_eye() -> None:
    assert convert_layout("ghbdtn") == "привет"


def test_threshold_can_be_tightened_from_settings() -> None:
    typed = "rfr ltkf"  # «как дела» — короткая фраза
    assert detect_wrong_layout(typed, threshold=0.4) is not None
    assert detect_wrong_layout(typed, threshold=0.99) is None


# --- guardrails, усиленные вместе с раскладкой ---


def test_profanity_survives_zero_width_obfuscation() -> None:
    assert guardrails.check_profanity("х​уй").hit


def test_profanity_survives_stretched_letters() -> None:
    assert guardrails.check_profanity("хуууууй").hit


def test_profanity_caught_in_english_layout() -> None:
    assert guardrails.check_profanity("[eq nt,t").hit


def test_layout_profanity_check_can_be_disabled() -> None:
    assert not guardrails.check_profanity("[eq nt,t", check_layout=False).hit


def test_polite_russian_question_is_clean() -> None:
    verdict = guardrails.check_profanity("Здравствуйте, подскажите по контракту, команда портала")
    assert not verdict.hit
