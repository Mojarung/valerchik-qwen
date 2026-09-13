"""Ссылка в жирном шрифте Markdown — та же ссылка, что в статье.

На замере по реальным обращениям модель выделила адрес жирным
(«**https://support.kontur.ru/diadoc/kontragenty**»), и сверка фактов сочла его
выдуманным: звёздочки прилипали к адресу. Верный ответ заменялся сырой статьёй.
"""

from app.services.answer_guard import grounding

SOURCE = "Проверьте контрагента в Диадоке: https://support.kontur.ru/diadoc/kontragenty"


def test_bold_link_is_the_same_link():
    answer = "Инструкция по связи с контрагентом: **https://support.kontur.ru/diadoc/kontragenty**"
    assert grounding(answer, [SOURCE]) == []


def test_underscored_and_quoted_link_is_the_same_link():
    answer = "Ссылка: _https://support.kontur.ru/diadoc/kontragenty_ и «https://support.kontur.ru/diadoc/kontragenty»"
    assert grounding(answer, [SOURCE]) == []


def test_other_link_is_still_unconfirmed():
    answer = "Инструкция: **https://example.org/diadoc**"
    assert grounding(answer, [SOURCE]) != []
