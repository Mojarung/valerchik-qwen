"""Файл на входе конвейера: имя, байты и ленивые представления.

Ленивые намеренно. `sniff` спрашивают у всех источников подряд, и если бы каждый
дёргал markitdown, разбор PDF шёл бы десять раз вместо одного.
"""

from __future__ import annotations

import io
import logging
from functools import cached_property
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp")
SHEET_SUFFIXES = (".xlsx", ".xls", ".xlsm")
# Кодировки по убыванию вероятности: выгрузки из старых систем приезжают в 1251
ENCODINGS = ("utf-8-sig", "utf-8", "cp1251", "koi8-r")


class FileBlob:
    def __init__(self, name: str, data: bytes) -> None:
        self.name = name
        self.data = data

    def __repr__(self) -> str:  # pragma: no cover — для логов и отладки
        return f"FileBlob({self.name!r}, {len(self.data)} байт)"

    @property
    def suffix(self) -> str:
        return Path(self.name).suffix.lower()

    @property
    def is_binary(self) -> bool:
        """Двоичный файл: разбирать как текст его нельзя."""
        if self.suffix in IMAGE_SUFFIXES:
            return True
        return b"\x00" in self.data[:1024]

    def decoded(self) -> str:
        """Байты → строка перебором кодировок. Последняя попытка — с заменой."""
        for encoding in ENCODINGS:
            try:
                return self.data.decode(encoding)
            except UnicodeDecodeError:
                continue
        return self.data.decode("utf-8", errors="replace")

    @cached_property
    def text(self) -> str:
        """Markdown-представление файла. Простой текст не гоняем через конвертер.

        Конвертер отказывается от незнакомого расширения ещё до того, как
        посмотрит внутрь, — и «расширение не приговор» оставалось лозунгом:
        выгрузка с именем `dump.dat` не разбиралась никем, хотя внутри лежала
        обычная таблица. Поэтому его отказ на нечитаемом расширении не конец
        разбора: если файл не двоичный, читаем его как текст и отдаём дальше.
        Источники решат сами, их это формат или нет.
        """
        if self.suffix in (".txt", ".md", ".markdown"):
            return self.decoded()
        from app.ingest.documents import extract_text_from_file

        try:
            return extract_text_from_file(self.name, self.data)
        except ValueError:
            if self.is_binary:
                raise
            log.info("Файл %s конвертер не взял — читаем как обычный текст", self.name)
            return self.decoded()

    @cached_property
    def tables(self) -> list[tuple[str, Any]]:
        """[(имя листа, DataFrame)] для книг Excel; для остального — пусто.

        Полистно, а не одной простынёй: в одной книге приезжают «Вопросы»,
        «Отзывы» и справочник организаций, и склейка превращает три набора
        данных в кашу, из которой не собрать ни один.
        """
        if self.suffix not in SHEET_SUFFIXES:
            return []
        import pandas as pd

        book = pd.read_excel(io.BytesIO(self.data), sheet_name=None, dtype=str)
        return [(str(name), frame.fillna("")) for name, frame in book.items()]
