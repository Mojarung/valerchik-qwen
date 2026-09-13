"""Неработающая подпись — техническая линия, а не закупки и контракты.

На проде «позовите оператора, не работает ЭЦП при подписании» уходило на линию 3:
модель темы видела «подписание» и относила вопрос к контрактам, хотя чинить надо
подпись.
"""

from app.services.lines import looks_like_outage


def test_broken_signature_tool_is_technical():
    assert looks_like_outage("позовите оператора, не работает ЭЦП при подписании")
    assert looks_like_outage("портал не видит сертификат электронной подписи")
    assert looks_like_outage("ошибка КриптоПро при входе")
    assert looks_like_outage("не удаётся подключить токен")


def test_contract_signing_question_is_left_to_the_topic_model():
    # Голое «подписание» — чаще вопрос по контракту, не сбой подписи
    assert not looks_like_outage("ошибка при подписании контракта")
    assert not looks_like_outage("как подписать контракт электронной подписью")
