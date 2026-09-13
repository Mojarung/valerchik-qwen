"""Remove invented expansions from generated text, without rewriting source evidence."""

import re
from collections.abc import Iterable
from functools import lru_cache

from app.services.guardrails import morph

_WORD = re.compile(r"[а-яёa-z]+", re.IGNORECASE)
_ACRONYM = r"[А-ЯЁA-Z]{2,10}"
_AFTER = re.compile(rf"\b(?P<abbr>{_ACRONYM})(?P<marks>\*{{0,2}})[ \t]*\((?P<phrase>[^()\n]+)\)")
_BEFORE = re.compile(
    rf"(?P<phrase>(?:[А-ЯЁа-яёA-Za-z-]+[ \t]+){{1,10}}[А-ЯЁа-яёA-Za-z-]+)[ \t]*"
    rf"\((?P<abbr>{_ACRONYM})\)"
)
_MIN_EXPANSION_WORDS = 2


@lru_cache(maxsize=20000)
def _lemma(word: str) -> str:
    return str(morph().parse(word)[0].normal_form).replace("ё", "е")


def _normalized(text: str) -> str:
    return " " + " ".join(_lemma(word.casefold()) for word in _WORD.findall(text)) + " "


def _is_expansion(acronym: str, phrase: str) -> bool:
    words = _WORD.findall(phrase)
    return len(words) >= _MIN_EXPANSION_WORDS and "".join(word[0] for word in words).upper() == acronym


def strip_unsupported_expansions(answer: str, sources: Iterable[str]) -> str:
    """Only an exact acronym/initials match identifies an explanation; other parentheses survive."""
    if not (_AFTER.search(answer) or _BEFORE.search(answer)):
        return answer
    evidence = list(sources)
    normalized = [_normalized(source) for source in evidence]

    def unsupported(acronym: str, phrase: str) -> bool:
        known = any(re.search(rf"\b{re.escape(acronym)}\b", source) for source in evidence)
        return known and not any(_normalized(phrase) in source for source in normalized)

    def after(match: re.Match[str]) -> str:
        acronym, phrase = match["abbr"], match["phrase"]
        if _is_expansion(acronym, phrase) and unsupported(acronym, phrase):
            return acronym + match["marks"]
        return match[0]

    def before(match: re.Match[str]) -> str:
        acronym, prefix = match["abbr"], match["phrase"]
        words = list(_WORD.finditer(prefix))
        if len(words) < len(acronym):
            return match[0]
        start = words[-len(acronym)].start()
        if start and prefix[start - 1] == "-":
            return match[0]
        phrase = prefix[start:]
        if _is_expansion(acronym, phrase) and unsupported(acronym, phrase):
            return prefix[:start] + acronym
        return match[0]

    return _BEFORE.sub(before, _AFTER.sub(after, answer))
