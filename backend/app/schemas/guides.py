"""Модели ответов роутера guides: сценарии по порталу для расширения, виджета и админки.

Шаги и локаторы хранятся компактно — пустые поля не пишутся. Ручки отдают их
с response_model_exclude_unset: расширение получает ровно те ключи, что лежат
в сценарии, а типы и лишние поля проверяет модель.
"""

from typing import Literal

from pydantic import Field

from app.schemas.base import ApiModel, SparseModel


class GuideLocator(SparseModel):
    """Как найти элемент на странице портала: css, текст или ссылка."""

    css: str = ""
    text: str = ""
    contains: bool = False
    in_: str = Field("", alias="in")
    href: str = ""


class GuideStep(SparseModel):
    title: str
    text: str = ""
    url: str = ""
    target: list[GuideLocator] = []
    action: Literal["", "click", "input"] = ""
    optional: bool = False
    # Куда вести после шага; None — движок идёт по url
    go: str | None = None


class GuidePublic(SparseModel):
    """Сценарий для интерфейса: без служебных формулировок подбора."""

    id: str
    title: str
    audience: str
    origin: str
    start: str
    steps: list[GuideStep]


class GuideCatalog(SparseModel):
    origin: str
    guides: list[GuidePublic]


class GuideMatch(SparseModel):
    guide: GuidePublic | None


class GuideAdminItem(SparseModel):
    id: str
    title: str
    audience: str
    questions: list[str]
    steps: list[GuideStep]
    start: str
    enabled: bool
    source: Literal["file", "admin", "recorder", "llm"]
    author: str
    updated_at: str
    # заводской сценарий, переписанный в админке: удаление вернёт исходный
    overridden: bool


class GuideAdminList(SparseModel):
    origin: str
    items: list[GuideAdminItem]


class GuideSaved(ApiModel):
    status: Literal["ok"]
    id: str


class GuideDeleted(ApiModel):
    status: Literal["ok"]
    # у записи был заводской оригинал — он вернулся в каталог
    restored: bool


class GuideEnabled(ApiModel):
    status: Literal["ok"]
    enabled: bool


class GuideDraft(SparseModel):
    """Черновик сценария от нейросети; llm — писала ли тексты модель."""

    llm: bool
    reason: str
    title: str
    questions: list[str]
    steps: list[GuideStep]
