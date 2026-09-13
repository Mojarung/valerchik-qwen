"""Подготовка ответа к озвучке.

Ответы форматируются в Markdown — так требует промпт агента, и на экране это
правильно. Вслух же «звёздочка звёздочка срок звёздочка звёздочка» превращает
help в абсурд, а прочитанный URL звучит как набор букв. Поэтому перед синтезом
разметку снимаем.

Отдельным модулем, а не внутри роутера: правила чистки — это то, что придётся
подкручивать по живым ответам, и им нужны свои тесты.
"""

import re

# Порядок важен: сначала блоки кода целиком, иначе их содержимое попадёт под
# остальные правила и будет прочитано вслух построчно.
_CODE_BLOCK = re.compile(r"```.*?```", re.DOTALL)
_INLINE_CODE = re.compile(r"`([^`]*)`")
_IMAGE = re.compile(r"!\[([^\]]*)\]\([^)]*\)")
_LINK = re.compile(r"\[([^\]]+)\]\([^)]*\)")
_BARE_URL = re.compile(r"https?://\S+")
_HEADING = re.compile(r"^\s{0,3}#{1,6}\s*", re.MULTILINE)
_QUOTE = re.compile(r"^\s{0,3}>\s?", re.MULTILINE)
_BULLET = re.compile(r"^\s*[-*+]\s+", re.MULTILINE)
_EMPHASIS = re.compile(r"(\*\*|__|\*|_)")
_TABLE_PIPE = re.compile(r"\s*\|\s*")
_RULE = re.compile(r"^\s*([-*_])\s*(?:\1\s*){2,}$", re.MULTILINE)
_SPACES = re.compile(r"[ \t]+")
_BLANKS = re.compile(r"\n{3,}")

_SENTENCE_END = ".!?…"


def strip_markdown(text: str) -> str:
    """Markdown → простой текст для синтезатора."""
    out = _CODE_BLOCK.sub(" ", text)
    out = _INLINE_CODE.sub(r"\1", out)
    out = _IMAGE.sub(r"\1", out)
    out = _LINK.sub(r"\1", out)  # ссылку читаем словами, адрес не произносим
    out = _BARE_URL.sub("ссылка", out)
    out = _RULE.sub(" ", out)
    out = _HEADING.sub("", out)
    out = _QUOTE.sub("", out)
    out = _BULLET.sub("", out)
    out = _EMPHASIS.sub("", out)
    out = _TABLE_PIPE.sub(" ", out)
    out = _SPACES.sub(" ", out)
    out = _BLANKS.sub("\n\n", out)
    return out.strip()


def trim_for_speech(text: str, limit: int) -> str:
    """Обрезает по границе предложения.

    Длинные инструкции слушать никто не станет, а задержка до первого звука
    растёт с длиной текста линейно. Полный ответ человек видит на экране, так
    что потери нет — но обрывать на полуслове нельзя, это звучит как сбой.
    """
    if limit <= 0 or len(text) <= limit:
        return text
    head = text[:limit]
    cut = max(head.rfind(char) for char in _SENTENCE_END)
    # Слишком ранняя точка (сокращение в начале) хуже, чем обрез по пробелу
    if cut > limit // 2:
        return head[: cut + 1].strip()
    space = head.rfind(" ")
    return (head[:space] if space > 0 else head).strip() + "…"


def prepare(text: str, limit: int) -> str:
    return trim_for_speech(strip_markdown(text), limit)
