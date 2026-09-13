"""Модели ответов роутера kb: статьи базы знаний, импорт и задачи разбора."""

from typing import Literal

from app.schemas.base import ApiModel, StoredModel
from app.schemas.chat import ImageRef
from app.services.audience import Audience


class ArticleOrigin(StoredModel):
    """Откуда приехала статья: файл, место в нём, разобравший источник."""

    file: str = ""
    locator: str = ""
    source: str = ""
    # Текст написан человеком (готовая пара «вопрос — ответ»), а не собран из
    # кусков документа: только такой отдаётся пользователю дословно.
    authored: bool = False
    # Чем запись служит (services/kbkind.py): ответ человеку, случай для
    # оператора или справочник. Без поля админка не видела бы, почему статья
    # не находится в чате: случаи ищутся только для оператора.
    kind: str = "answer"
    # Реальные формулировки вопроса (services/phrasings.py): по ним поиск
    # находит статью, когда человек написал не так, как её вопрос. Только для
    # чтения — редактор статьи происхождение не переписывает.
    phrasings: list[str] = []


class ArticleOut(ApiModel):
    id: str
    question: str
    answer: str
    category: str
    keywords: list[str]
    line: int
    status: str
    source: str
    audience: Audience
    images: list[ImageRef]
    # Откуда приехала статья: файл и строка. В день разбора чужих данных это
    # первое, что спрашивают, глядя на странный ответ бота.
    origin: ArticleOrigin | None
    views: int
    helpful: int
    not_helpful: int
    updated_at: str


class ArticlePage(ApiModel):
    total: int
    page: int
    items: list[ArticleOut]


class CategoryCount(ApiModel):
    category: str
    count: int


class ArticleUpdated(ApiModel):
    status: Literal["updated"]


class ArticleRemoved(ApiModel):
    status: Literal["deleted", "archived"]


class BulkResult(ApiModel):
    action: Literal["publish", "archive", "purge"]
    affected: int


class ImportResult(ApiModel):
    imported: int
    indexed: int


class JobQueued(ApiModel):
    job_id: str
    # Сколько фрагментов насчитано сразу; у файла — 0, их считает воркер
    sections: int
    status: Literal["queued"]


#: Состояние задачи импорта; строку из колонки сверяет модель.
JobStatus = Literal["pending", "running", "done", "error"]


class IngestJobOut(ApiModel):
    id: str
    title: str
    filename: str
    status: JobStatus
    total: int
    processed: int
    created_articles: int
    error: str
    actor: str
    created_at: str
    finished_at: str | None


class SectionPreview(ApiModel):
    heading: str | None
    chars: int
    excerpt: str


class IngestPreview(ApiModel):
    sections: int
    preview: list[SectionPreview]


class MediaUploaded(ApiModel):
    url: str
    caption: str
    page: int | None
