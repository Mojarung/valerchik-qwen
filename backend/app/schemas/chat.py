"""Модели роутера chat: запросы и ответы."""

from typing import Any, Literal

from pydantic import BaseModel, Field

from app.schemas.base import ApiModel, StoredModel
from app.services.audience import Audience


class SessionCreated(ApiModel):
    """Новый диалог: приветствие, подсказки и токен владельца."""

    session_id: str
    greeting: str
    chips: list[str]
    audience: Audience
    # Токен владельца: фронт хранит его рядом с id и предъявляет при каждом
    # обращении к диалогу — заголовком в REST и подпротоколом в сокете.
    owner: str


class SessionState(ApiModel):
    """Состояние существующего диалога — чтобы вернуть человека туда, где он был."""

    session_id: str
    chips: list[str]
    status: str
    line: int | None
    escalated: bool
    audience: Audience
    # Кому передано обращение — словами, для шапки чата: название нашей линии и,
    # если вопрос решает не она, адресат по регламенту. Пусто — не передано.
    line_label: str = ""
    addressee_label: str = ""
    version: int = 0
    waiting_reason: str | None = None
    operator_id: str | None = None


class SessionOwnerPair(BaseModel):
    """Пара (id диалога, токен владельца), как их хранит браузер посетителя.

    Учётных записей у чата нет: владение диалогом подтверждает не логин, а
    подписанный токен рядом с id (см. session_owner_token в security.py).
    Список копится в localStorage по мере того, как посетитель открывает
    новые обращения — отсюда и множественное число в запросе.
    """

    session_id: str = Field(min_length=1, max_length=64)
    owner: str = Field(min_length=1, max_length=64)


# Сколько диалогов может накопить один браузер: щедрый запас на всякий
# случай, а не ожидаемое число — но неограниченный список означал бы, что
# один браузер может заказать сколь угодно большой запрос за раз.
MAX_HISTORY_SESSIONS = 200


class SessionHistoryRequest(BaseModel):
    """Тело запроса «Моих обращений»: все пары, что накопил браузер."""

    sessions: list[SessionOwnerPair] = Field(default_factory=list, max_length=MAX_HISTORY_SESSIONS)


class SessionSummary(ApiModel):
    """Карточка одного прошлого обращения в списке «Мои обращения».

    Только то, что нужно показать в списке до открытия диалога: полная
    переписка приходит потом отдельным вызовом get_messages по session_id.
    """

    session_id: str
    unread_count: int = 0
    status: str
    line: int | None
    # Первый вопрос человека: по нему обращение узнают в списке.
    title: str
    created_at: str
    # Время последней реплики: по нему список сортируется, а браузер понимает,
    # что после его последнего визита в обращении кто-то ответил.
    updated_at: str
    closed_at: str | None
    last_message_preview: str
    # Чья последняя реплика: user | assistant | operator | system.
    last_message_role: str
    # Кому передано обращение — те же подписи, что в шапке чата; пусто — не передавали.
    line_label: str = ""
    addressee_label: str = ""


class ImageRef(StoredModel):
    """Иллюстрация статьи: из разбора методички или загруженная в редакторе.

    figure — ключ рисунка вида «рис:26», по которому текст инструкции на него
    ссылается; в старых выгрузках — просто номер.
    """

    url: str = ""
    caption: str = ""
    page: int | None = None
    figure: str | int | None = None


class Citation(StoredModel):
    """Статья, на которую опирается ответ."""

    article_id: str
    question: str
    score: float
    category: str | None = None
    images: list[ImageRef] = []


class ChatMessageOut(ApiModel):
    """Сообщение ленты. meta — набор меток ответа (уверенность, варианты
    уточнения, карточки товаров, вложение), у каждого вида сообщения свой."""

    id: str
    role: Literal["user", "assistant", "operator", "system"]
    content: str
    citations: list[Citation]
    meta: dict[str, Any]
    created_at: str


class ClarifyOption(ApiModel):
    """Кнопка уточнения: тема (статья) или роль собеседника."""

    text: str
    article_id: str | None = None
    # Кнопка уточнения роли: нажатие ещё и переводит ползунок интерфейса
    audience: Literal["supplier", "customer"] | None = None


class ChatReply(ApiModel):
    """Итог обработки сообщения.

    Поля, кроме status, есть не у всех исходов: у переадресации оператору и
    у предупреждения модерации текста ответа нет.
    """

    status: Literal[
        "accepted",
        "answered",
        "no_answer",
        "clarify",
        "forwarded_to_operator",
        "warned",
        "closed_violation",
        "resolved",
    ]
    message_id: str | None = None
    answer: str | None = None
    citations: list[Citation] | None = None
    options: list[ClarifyOption] | None = None
    confidence: float | None = None
    escalated: bool | None = None
    latency_ms: int | None = None


class AudienceState(ApiModel):
    audience: Audience
    chips: list[str]


class AttachmentSent(ApiModel):
    status: Literal["ok"]
    message_id: str
    url: str


class ContractSummarySent(ApiModel):
    """Итог загрузки контракта: выжимка уже сохранена в диалог как ответ ассистента."""

    status: Literal["ok"]
    message_id: str
    summary: str


class RatingSaved(ApiModel):
    status: Literal["ok"]
    value: int


class FeedbackSaved(ApiModel):
    status: Literal["ok"]


class SourceArticle(ApiModel):
    """Статья-источник для просмотра из цитаты в чате."""

    id: str
    question: str
    answer: str
    category: str | None
    line: int | None
    images: list[ImageRef]
    updated_at: str
