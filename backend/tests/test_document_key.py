"""Ключ документа не зависит от юникод-формы заголовка.

Имя файла из браузера на macOS несёт «й» буквой и отдельным значком краткости
(NFD). Выглядит так же, но ключ получался другим, и на стенде переимпорт
«Темы_подтемы_обращений» не заменил прошлую версию, а лёг рядом с ней.
"""

import unicodedata

from app.ingest.pipeline import document_key


def test_nfd_and_nfc_titles_give_one_key():
    nfc = "Темы_подтемы_обращений"
    nfd = unicodedata.normalize("NFD", nfc)
    assert nfd != nfc  # «й» разложена на букву и значок
    assert document_key(nfd) == document_key(nfc)


def test_key_still_ignores_case_and_spaces():
    assert document_key("  Выгрузка   СТП за 2026 ") == document_key("выгрузка стп за 2026")


def test_upload_form_title_and_file_stem_give_one_key():
    """Кнопка загрузки называет документ «Темы подтемы обращений», сборка из каталога —
    «Темы_подтемы_обращений». Это один документ, и новая версия должна заменить старую."""
    assert document_key("Темы подтемы обращений") == document_key("Темы_подтемы_обращений")
    assert document_key("Инструкция-по-YML") == document_key("Инструкция по YML")


def test_key_is_idempotent():
    """Ключ, сохранённый прошлой версией кода, приводится к нынешнему тем же вызовом."""
    for title in ("Темы_подтемы_обращений", "Регламент  информационного взаимодействия"):
        assert document_key(document_key(title)) == document_key(title)
