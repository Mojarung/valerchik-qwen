"""Нарезка документа на самодостаточные куски.

Почему нарезка, а не «одна статья на документ»: эмбеддер сжимает текст в один
вектор, и чем длиннее текст, тем более общим и бесполезным для поиска этот
вектор становится — методичка на 40 страниц находилась бы по любому запросу
одинаково плохо. Куски по абзацам держат вектор предметным.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

_MD_HEADING = re.compile(r"^\s{0,3}(#{1,6})\s+(.+?)\s*#*\s*$")
_NUMBERED_HEADING = re.compile(r"^\s*(\d{1,2}(?:\.\d{1,2}){0,3})[.)]?\s+(\S.{2,110})$")
_LIST_ITEM = re.compile(r"^\s*(?:[-*•]|\d{1,2}[.)])\s+")
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")

MAX_CHUNK_CHARS = 900
MIN_CHUNK_CHARS = 120
MAX_HEADING_CHARS = 110


@dataclass
class Section:
    heading: str | None
    body: str
    # Иллюстрации, на которые ссылается именно этот кусок текста
    images: list[dict[str, Any]] = field(default_factory=list)

    @property
    def text(self) -> str:
        return f"{self.heading}\n{self.body}" if self.heading else self.body


# Заголовок — короткая строка: длиннее восьмидесяти символов это уже абзац
_HEADING_MAX_CHARS = 80


def _looks_like_heading(line: str, prev_line: str | None, next_line: str | None) -> str | None:
    """-> текст заголовка или None. Нумерованный и «короткий изолированный» варианты."""
    stripped = line.strip()
    if not stripped or len(stripped) > MAX_HEADING_CHARS:
        return None
    if _LIST_ITEM.match(line):
        return None
    numbered = _NUMBERED_HEADING.match(stripped)
    if numbered and not numbered.group(2).endswith((".", ";", ",")):
        return numbered.group(2).strip()
    # Короткая строка без завершающей пунктуации, отбитая пустыми строками с обеих
    # сторон. Изоляция здесь — главный признак: первая строка обычного абзаца тоже
    # короткая и тоже без точки (перенос), но под ней сразу идёт продолжение текста.
    isolated = (prev_line is None or not prev_line.strip()) and (next_line is None or not next_line.strip())
    if (
        isolated
        and len(stripped) <= _HEADING_MAX_CHARS
        and not stripped.endswith((".", ";", ",", ":"))
        and stripped[:1].isupper()
        and " " in stripped
    ):
        return stripped
    return None


# Подпись к иллюстрации или таблице: «Рисунок 82 - Экспорт оферт», «(см. рисунок 5)»
CAPTION = re.compile(r"^\(?\s*(?P<label>рисунок|таблица|см\.|figure|table)\b", re.I)
# Оглавление методички — строка целиком, а не начало заголовка
_CONTENTS = re.compile(r"^(содержание|оглавление|contents)$", re.I)
# Подпись таблицы ответом бывает: таблицей в методичках оформлен «Перечень
# терминов и сокращений» — единственное место, где расшифрованы УПД, МЧД и КС.
_TABLE_LABELS = frozenset({"таблица", "table"})


def answerless_heading(title: str) -> bool:
    """Заголовок, которым нельзя ответить: подпись к рисунку или оглавление.

    Импорт методичек делает заголовком статьи заголовок раздела, а подпись под
    картинкой — короткая изолированная строка, то есть заголовок по всем
    признакам (_looks_like_heading). Так среди активных статей появились 782
    «Рисунок N - …» и 66 «СОДЕРЖАНИЕ»: ответа в них нет ни в каком виде, но слот
    кандидата и место в топ-5 они занимают наравне с инструкцией.
    """
    text = title.strip()
    if _CONTENTS.match(text):
        return True
    caption = CAPTION.match(text)
    return bool(caption and caption["label"].lower() not in _TABLE_LABELS)


def split_sections(text: str, max_chars: int = MAX_CHUNK_CHARS) -> list[Section]:
    """Документ → куски по заголовкам, длинные разделы дробятся по абзацам."""
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    sections: list[Section] = []
    heading: str | None = None
    buffer: list[str] = []

    def flush() -> None:
        body = "\n".join(buffer).strip()
        if body or heading:
            sections.append(Section(heading=heading, body=body))
        buffer.clear()

    for i, line in enumerate(lines):
        md = _MD_HEADING.match(line)
        prev_line = lines[i - 1] if i > 0 else None
        next_line = lines[i + 1] if i + 1 < len(lines) else None
        found = md.group(2).strip() if md else _looks_like_heading(line, prev_line, next_line)
        if found:
            flush()
            heading = found
            continue
        buffer.append(line)
    flush()

    chunks: list[Section] = []
    for section in sections:
        for body in _split_body(section.body, max_chars):
            chunks.append(Section(heading=section.heading, body=body))
    return _merge_tiny(chunks)


def _by_sentences(paragraph: str, max_chars: int) -> tuple[list[str], str]:
    """Слишком длинный абзац — по предложениям: готовые куски и неполный остаток."""
    pieces: list[str] = []
    buffer = ""
    for sentence in _SENTENCE_END.split(paragraph):
        if len(buffer) + len(sentence) + 1 > max_chars and buffer:
            pieces.append(buffer.strip())
            buffer = ""
        buffer += sentence + " "
    return pieces, buffer.strip()


def _join(out: list[str], current: str, paragraph: str, max_chars: int) -> str:
    """Дописать абзац к текущему куску; не влезает — кусок уходит в out, абзац начинает новый."""
    if not current:
        return paragraph
    if len(current) + len(paragraph) + 2 > max_chars:
        out.append(current)
        return paragraph
    return f"{current}\n\n{paragraph}"


def _split_body(body: str, max_chars: int) -> list[str]:
    """Длинный раздел — по абзацам, слишком длинный абзац — по предложениям."""
    body = body.strip()
    if not body:
        return []
    if len(body) <= max_chars:
        return [body]
    out: list[str] = []
    current = ""
    for paragraph in re.split(r"\n\s*\n", body):
        paragraph = paragraph.strip()
        if not paragraph:
            continue
        if len(paragraph) > max_chars:
            if current:
                out.append(current)
            pieces, current = _by_sentences(paragraph, max_chars)
            out.extend(pieces)
        else:
            current = _join(out, current, paragraph, max_chars)
    if current:
        out.append(current)
    return out


# Номер страницы, колонтитул, обрывок таблицы — куски без единого осмысленного слова.
# Из PDF их приезжают десятки, и каждый стал бы статьёй с вопросом «6: что нужно знать?».
_MEANINGFUL = re.compile(r"[А-Яа-яЁёA-Za-z]{4,}")
MIN_MEANINGFUL_CHARS = 40
MIN_MEANINGFUL_WORDS = 3


def _is_junk(body: str, has_images: bool = False) -> bool:
    """Кусок без смысла: одни цифры, подписи и разделители.

    Кусок с картинкой мусором не считается. В инструкциях шаг «нажмите вот тут»
    состоит из скриншота и двух слов, а в выгрузках поддержки на «где эта кнопка»
    отвечают снимком экрана: выбросить такой кусок значит потерять сам ответ.
    """
    if has_images:
        return False
    text = body.strip()
    return len(text) < MIN_MEANINGFUL_CHARS or len(_MEANINGFUL.findall(text)) < MIN_MEANINGFUL_WORDS


def _merge_tiny(chunks: list[Section]) -> list[Section]:
    """Огрызки короче минимума приклеиваем к предыдущему куску того же раздела."""
    out: list[Section] = []
    for chunk in chunks:
        if not chunk.body.strip():
            continue
        if _is_junk(chunk.body, has_images=bool(chunk.images)):
            # приклеить некуда — предыдущий кусок уже полон; просто выбрасываем
            if (
                out
                and out[-1].heading == chunk.heading
                and len(out[-1].body) + len(chunk.body) < MAX_CHUNK_CHARS * 1.4
            ):
                out[-1] = Section(heading=out[-1].heading, body=f"{out[-1].body}\n{chunk.body}".strip())
            continue
        if (
            out
            and len(chunk.body) < MIN_CHUNK_CHARS
            and out[-1].heading == chunk.heading
            and len(out[-1].body) + len(chunk.body) < MAX_CHUNK_CHARS * 1.4
        ):
            out[-1] = Section(heading=out[-1].heading, body=f"{out[-1].body}\n\n{chunk.body}".strip())
            continue
        out.append(chunk)
    return out


def _normalized_text(text: str) -> str:
    return re.sub(r"[^0-9A-Za-zА-Яа-яЁё]+", " ", text.lower()).strip()
