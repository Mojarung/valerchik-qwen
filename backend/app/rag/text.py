"""Нормализация текста: чистка разметки источников и леммы для лексического поиска."""

import re
import threading
from functools import lru_cache

import pymorphy3

_WORD = re.compile(r"[a-zа-яё0-9]+", re.IGNORECASE)

_COLOR_TAGS = re.compile(r"</?[cm](?:\s+#[0-9a-fA-F]{3,8})?>")
_HTML_TAGS = re.compile(r"</?[a-zA-Z][a-zA-Z0-9]*(?:\s[^<>]{0,200})?/?>")
_MD_LINK = re.compile(r"\[([^\]]{1,300})\]\((?:https?://[^)\s]+)\)")
_MD_ESCAPE = re.compile(r"\\([\\.\-_*()\[\]#+!>|`~{}])")
_MD_EMPH = re.compile(r"\*\*|__")
_SPACES = re.compile(r"[ \t ]+")
_NEWLINES = re.compile(r"\n{3,}")
_SOFT = str.maketrans({"￾": "", "­": "", "​": "", "﻿": ""})

STOPWORDS = frozenset(
    """
    и в во на с со по к ко о об обо от до за из у не ни а но же ли бы то как что это для при или если где
    когда какой который мой ваш наш свой я мы вы он она оно они мне нам вам его её ее их быть этот тот так
    уже ещё еще также можно нужно надо весь всё все там тут здесь чтобы есть нет да ну вот через под над
    здравствуйте добрый день утро вечер пожалуйста подсказать помочь спасибо заранее прошу просьба
    уважаемый коллега какой-то сделать делать хотеть мочь
    """.split()
)


def clean_markup(text: str) -> str:
    """Текст без цветовой разметки тикет-системы, HTML, markdown-экранирования и лишних пробелов."""
    text = text.translate(_SOFT).replace("\r\n", "\n").replace("\r", "\n")
    text = _COLOR_TAGS.sub("", text)
    text = _HTML_TAGS.sub(" ", text)
    text = _MD_LINK.sub(r"\1", text)
    text = _MD_ESCAPE.sub(r"\1", text)
    text = _MD_EMPH.sub("", text)
    text = "\n".join(_SPACES.sub(" ", line).strip() for line in text.split("\n"))
    return _NEWLINES.sub("\n\n", text).strip()


_morph_lock = threading.Lock()
_morph: pymorphy3.MorphAnalyzer | None = None


def _analyzer() -> pymorphy3.MorphAnalyzer:
    global _morph
    if _morph is None:
        with _morph_lock:
            if _morph is None:
                _morph = pymorphy3.MorphAnalyzer()
    return _morph


@lru_cache(maxsize=300_000)
def lemma(word: str) -> str:
    if not word.isalpha() or len(word) < 3:  # noqa: PLR2004 — короткое и цифры не склоняются
        return word
    return str(_analyzer().parse(word)[0].normal_form).replace("ё", "е")


def words(text: str) -> list[str]:
    return [w.lower().replace("ё", "е") for w in _WORD.findall(text)]


def lemmas(text: str) -> list[str]:
    """Леммы значимых слов: предлоги, вежливость и связки отброшены."""
    out = []
    for word in words(text):
        if word in STOPWORDS:
            continue
        base = lemma(word)
        if base not in STOPWORDS and (len(base) > 1 or base.isdigit()):
            out.append(base)
    return out


def is_known(word: str) -> bool:
    """Есть ли слово в словаре русского языка (любая его форма)."""
    return bool(_analyzer().word_is_known(word))


def warm() -> None:
    _analyzer()
    lemma("документы")
