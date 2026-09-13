"""Папка скриншотов: картинка сама по себе ответ.

В выгрузках поддержки на «где эта кнопка» отвечают снимком экрана, а вопрос живёт
в имени файла — «kak-vygruzit-upd.png». Текста в такой статье нет, и это не брак:
ответ показывается картинкой, отбраковку по пустому телу снимает признак картинки.

Имена такие почти всегда латиницей (так их сохраняют, чтобы не ловить кракозябры в
архивах и на файловых шарах), поэтому подпись возвращается в кириллицу — иначе
поиск по русскому запросу до этой статьи не доберётся.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from pathlib import PurePosixPath

from app.ingest.blob import IMAGE_SUFFIXES, FileBlob
from app.ingest.records import ArticleRecord, AssetRecord, Provenance, Record
from app.ingest.registry import IngestContext, register
from app.ingest.sources.base import SourceMismatch

# Диграфы обязаны идти раньше одиночных букв, иначе «sh» распадётся на «сх».
# «sch» намеренно отсутствует: щ — это «shch», а «schet» должен читаться как «счет».
_TRANSLIT = {
    "shch": "щ",
    "yo": "ё",
    "zh": "ж",
    "kh": "х",
    "ts": "ц",
    "ch": "ч",
    "sh": "ш",
    "yu": "ю",
    "ya": "я",
    "a": "а",
    "b": "б",
    "c": "ц",
    "d": "д",
    "e": "е",
    "f": "ф",
    "g": "г",
    "h": "х",
    "i": "и",
    "j": "й",
    "k": "к",
    "l": "л",
    "m": "м",
    "n": "н",
    "o": "о",
    "p": "п",
    "q": "к",
    "r": "р",
    "s": "с",
    "t": "т",
    "u": "у",
    "v": "в",
    "w": "в",
    "x": "кс",
    "y": "ы",
    "z": "з",
}
_TRANSLIT_RE = re.compile("|".join(sorted(_TRANSLIT, key=len, reverse=True)))

# Слова, которыми файлы называет техника, а не человек: «screenshot» — английское
# слово, а не транслит, и превращать его в «скрееншот» нельзя.
_MACHINE_WORDS = frozenset(
    {
        "screenshot",
        "screen",
        "shot",
        "snapshot",
        "capture",
        "image",
        "img",
        "photo",
        "picture",
        "pic",
        "scan",
        "document",
        "untitled",
        "copy",
        "new",
    }
)

_SEPARATORS = re.compile(r"[_\-.\s]+")
# Только 1–3 цифры: «01_» — это нумерация в папке, а «2026-09-11» — дата в вопросе
_LEADING_INDEX = re.compile(r"^\d{1,3}[_\-.\s]+")
# Хвост от повторной загрузки: «упд (1).png». Скобка ломает проверку на чистую
# латиницу, и вопрос остаётся непереведённым — снимаем её до транслитерации
_DUPLICATE_TAIL = re.compile(r"[\s_\-]*\(\d{1,3}\)$")
_LATIN_ONLY = re.compile(r"[a-z0-9 ]+")
_WORD = re.compile(r"[a-z]+")


# Транслитом считаем имя, где есть слово хотя бы из трёх букв
_MIN_TRANSLIT_TOKEN = 3


def _looks_like_translit(words: str) -> bool:
    """Похоже ли на русскую фразу, записанную латиницей."""
    lowered = words.lower()
    if not _LATIN_ONLY.fullmatch(lowered):
        return False
    tokens = _WORD.findall(lowered)
    # Короткие огрызки вроде «a1» транслитерировать бессмысленно — это не фраза
    if not any(len(token) >= _MIN_TRANSLIT_TOKEN for token in tokens):
        return False
    return not all(token in _MACHINE_WORDS for token in tokens)


def caption_from_name(name: str) -> str:
    """Имя файла → человеческая подпись, она же вопрос статьи."""
    # Берём последний сегмент пути: в siblings имена приезжают вместе с папкой
    stem = _DUPLICATE_TAIL.sub("", PurePosixPath(name.replace("\\", "/")).stem)
    words = _SEPARATORS.sub(" ", _LEADING_INDEX.sub("", stem)).strip()
    # Имя из одной нумерации («01_.png»): пустой вопрос конвейер выбрасывает, и
    # снимок пропадает молча. Номер как подпись бесполезен для поиска, но статья
    # хотя бы доедет до базы и её будет видно в админке
    if not words:
        words = _SEPARATORS.sub(" ", stem).strip() or PurePosixPath(name).name.strip()
    if _looks_like_translit(words):
        words = _TRANSLIT_RE.sub(lambda found: _TRANSLIT[found.group()], words.lower())
    return words[:1].upper() + words[1:]


@register
class ImageFolderSource:
    name = "image_folder"
    # Низкий: картинку у нас может забрать разбор PDF или таблица со ссылками,
    # и отдельным файлом она приходит только тогда, когда больше некому
    priority = 5

    async def sniff(self, blob: FileBlob) -> float:
        # Уверенность высокая, но не предельная: имя файла — слабое основание,
        # и источник с настоящим разбором формата должен уметь перебить
        return 0.8 if blob.suffix in IMAGE_SUFFIXES else 0.0

    async def parse(self, blob: FileBlob, ctx: IngestContext) -> AsyncIterator[Record]:
        if blob.suffix not in IMAGE_SUFFIXES:
            raise SourceMismatch(f"{blob.name} — не картинка")
        # Ответ здесь — сами байты. Нулевой файл (оборванная выгрузка, ярлык на
        # шару) дал бы статью, у которой вместо ответа битая картинка
        if not blob.data:
            raise SourceMismatch(f"{blob.name} — пустой файл")

        caption = caption_from_name(blob.name)
        # authored=True: вопрос сформулировал человек, назвавший файл, а ответ —
        # готовый скриншот поддержки, его можно показывать как есть
        origin = Provenance(file=blob.name, locator="файл целиком", source_name=self.name, authored=True)
        yield AssetRecord(data=blob.data, caption=caption, anchor={"ref": blob.name}, origin=origin)
        # Картинку к статье привяжет конвейер (bind_assets) по тому же имени файла
        yield ArticleRecord(
            question=caption,
            answer="",
            category=ctx.params.get("category", ""),
            line=ctx.params.get("line"),
            audience=ctx.params.get("audience", "all"),
            origin=origin,
        )
