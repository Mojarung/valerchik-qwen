"""Записи — промежуточный формат между источником и базой.

Источник разбирает файл и отдаёт записи; сохранением занимается конвейер. Провенанс
едет с каждой записью: на защите нужно уметь показать, из какой строки какого файла
взялся ответ, а `authored` вдобавок решает, можно ли отдать этот ответ дословно.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass(slots=True)
class Provenance:
    """Откуда приехала запись."""

    file: str = ""
    locator: str = ""  # «лист 2, строка 145», «стр. 7», «$.items[3]»
    source_name: str = ""
    # Текст написан человеком (готовая пара «вопрос — ответ», ответ оператора), а не
    # собран нами из кусков документа. Только такой можно отдавать пользователю
    # дословно: остальное писалось как абзац инструкции, а не как ответ на вопрос.
    authored: bool = False
    # Что это за запись для поиска (см. services/kbkind.py): ответ человеку,
    # случай для оператора (ответ-отписка про одну заявку — верный для того
    # заявителя и ложный для всех остальных) или справочник без ответа вовсе.
    kind: str = "answer"

    def as_dict(self) -> dict[str, Any]:
        return {
            "file": self.file,
            "locator": self.locator,
            "source": self.source_name,
            "authored": self.authored,
            "kind": self.kind,
        }


@dataclass(slots=True)
class ImageRef:
    """Картинка, уже сохранённая в хранилище: то, что ложится в KBArticle.images."""

    url: str
    caption: str = ""
    page: int | None = None
    figure: str = ""
    # Имя файла, под которым картинка приехала («screens/a.png»). Ключ привязки, а
    # не данные статьи: таблица ссылается на исходное имя, а в хранилище картинка
    # лежит под порядковым номером. В as_dict не идёт — в базе статьи ему делать
    # нечего, а без него привязка по явной ссылке не находит ни одной картинки.
    ref: str = ""
    # Место картинки в документе — заголовок раздела и начало абзаца перед ней.
    # Тоже ключ привязки, а не данные статьи: по нему картинка без номера в
    # подписи находит свой кусок текста. В as_dict не идут.
    heading: str = ""
    before: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {"url": self.url, "caption": self.caption, "page": self.page, "figure": self.figure}


@dataclass(slots=True)
class ArticleRecord:
    """Статья базы знаний: вопрос, ответ и всё, что к ним прилагается."""

    question: str
    answer: str
    category: str = ""
    line: int | None = None
    audience: str = "all"
    keywords: list[str] = field(default_factory=list)
    images: list[ImageRef] = field(default_factory=list)
    origin: Provenance = field(default_factory=Provenance)
    # Реальные формулировки вопроса (services/phrasings.py): у ответа из выгрузки
    # поддержки — обращения, на которые его давал оператор. В базе ложатся в
    # происхождение статьи, в поиске — отдельными точками векторного индекса.
    phrasings: list[str] = field(default_factory=list)


@dataclass(slots=True)
class FeedbackRecord:
    """Отзыв о работе поддержки: топливо для оценки специалистов."""

    rating: int
    comment: str = ""
    operator: str = ""
    created_at: datetime | None = None
    origin: Provenance = field(default_factory=Provenance)


@dataclass(slots=True)
class DialogRecord:
    """Лог чужого обращения: топливо для классификатора линий и регресса."""

    messages: list[tuple[str, str]] = field(default_factory=list)
    line: int | None = None
    resolved: bool | None = None
    origin: Provenance = field(default_factory=Provenance)


@dataclass(slots=True)
class AssetRecord:
    """Картинка или файл, который надо сохранить и привязать к записи."""

    data: bytes
    caption: str = ""
    # Как привязать: {figure: "26", page: 7, ref: "screens/a.png", order: 3}
    anchor: dict[str, Any] = field(default_factory=dict)
    origin: Provenance = field(default_factory=Provenance)


Record = ArticleRecord | FeedbackRecord | DialogRecord | AssetRecord
