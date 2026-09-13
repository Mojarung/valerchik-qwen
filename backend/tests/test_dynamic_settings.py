"""Реестр динамических настроек и проверка правок из админки.

Подпись, раздел и значение по умолчанию жили в двух словарях с одними и теми же
ключами — новая настройка без подписи молча показывалась в админке ключом.
Проверка правок решала по типу значения по умолчанию и пропускала `True` в
целочисленную настройку: bool — подкласс int, и ветка для целых его обходила.
"""

import pytest

from app.dynamic_settings import SETTINGS, DynamicSettings, SettingsValueError, validate_updates


def test_every_setting_is_described() -> None:
    assert SETTINGS
    for key, spec in SETTINGS.items():
        assert spec.label and spec.category and spec.description, key


@pytest.mark.parametrize(
    ("key", "value", "message"),
    [
        ("answer.verbatim_enabled", "да", "ожидается да/нет"),
        ("agent.max_history", -1, "ожидается неотрицательное целое число"),
        ("agent.max_history", True, "ожидается неотрицательное целое число"),
        ("agent.max_history", 2.7, "ожидается неотрицательное целое число"),
        ("agent.max_history", float("inf"), "ожидается неотрицательное целое число"),
        ("agent.confidence_threshold", "0.4", "ожидается неотрицательное число"),
        ("agent.confidence_threshold", False, "ожидается неотрицательное число"),
        ("agent.confidence_threshold", float("nan"), "ожидается неотрицательное число"),
        ("chat.chips", "Как подписать?", "ожидается список значений"),
        ("routing.lines", [], "ожидается объект"),
        ("chat.greeting", 42, "ожидается строка"),
        ("chat.no_such_setting", 1, "неизвестная настройка"),
    ],
)
def test_rejects_value_of_wrong_type(key: str, value: object, message: str) -> None:
    with pytest.raises(SettingsValueError, match=f"{key}: {message}"):
        validate_updates({key: value})


def test_coerces_numbers_to_the_setting_type() -> None:
    assert validate_updates({"agent.max_history": 6.0, "agent.confidence_threshold": 1}) == {
        "agent.max_history": 6,
        "agent.confidence_threshold": 1.0,
    }


def test_reports_every_problem_at_once() -> None:
    with pytest.raises(SettingsValueError) as error:
        validate_updates({"chat.greeting": 1, "agent.max_history": -3})
    assert str(error.value) == (
        "chat.greeting: ожидается строка; agent.max_history: ожидается неотрицательное целое число"
    )


async def test_new_setting_gets_code_default_on_existing_database(tmp_path) -> None:
    """Таблица настроек стенда заполнена при первом старте явными значениями.

    Ключ, добавленный позже (порог уверенности в линии), в ней отсутствует: он
    обязан получить умолчание из кода и записаться, а записанное раньше — остаться.
    """
    from app.db import Database
    from app.models import SettingEntry

    new_key = "routing.line_min_confidence"
    db = Database(f"sqlite+aiosqlite:///{tmp_path / 'settings.db'}")
    await db.create_all()
    async with db.session_factory() as session:
        for key, spec in SETTINGS.items():
            if key == new_key:
                continue
            value = 0.3 if key == "routing.topic_min_confidence" else spec.default
            session.add(
                SettingEntry(
                    key=key, value={"v": value}, category=spec.category, description=spec.description
                )
            )
        await session.commit()

    settings = DynamicSettings(db.session_factory, None)
    await settings.load()

    default = SETTINGS[new_key].default
    assert settings.get(new_key) == default
    assert settings.get("routing.topic_min_confidence") == 0.3
    async with db.session_factory() as session:
        row = await session.get(SettingEntry, new_key)
    assert row is not None and row.value == {"v": default}
    await db.dispose()


def test_listing_shows_labels_and_hides_secrets() -> None:
    settings = DynamicSettings(None, None)  # список берётся из памяти, базы не касается
    settings._values["llm.api_key"] = "sk-live-123"
    listing = {row["key"]: row for row in settings.all()}
    assert listing["chat.greeting"]["label"] == "Приветствие"
    assert listing["llm.api_key"]["value"] == "***"
    assert listing["llm.api_key"]["secret"] is True
