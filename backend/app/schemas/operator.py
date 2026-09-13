"""Модели ответов роутера operator: очередь, переписка, копайлот, шаблоны."""

from typing import Any, Literal

from pydantic import Field

from app.schemas.base import ApiModel


class QueueItem(ApiModel):
    """Обращение в очереди оператора."""

    session_id: str
    version: int = 0
    waiting_reason: str | None = None
    routing: dict[str, Any] = Field(default_factory=dict)
    audience: str = "all"
    assigned_at: str | None = None
    available_actions: list[str] = Field(default_factory=list)
    status: str
    line: int | None
    operator_id: str | None
    reason: str | None
    created_at: str
    escalated_at: str | None
    # Сколько человек уже ждёт. Считаем на сервере: у операторов разные часовые
    # пояса и разъезжающиеся часы, а решение «за это браться первым»
    # принимается именно по этому числу.
    waiting_seconds: int | None
    reclaimed_count: int
    last_message: str
    last_role: str


class KbHit(ApiModel):
    """Статья базы знаний в выдаче для оператора."""

    article_id: str
    question: str
    answer: str
    category: str | None
    score: float
    # Вид записи (services/kbkind.py): answer — инструкция, её можно вставить
    # клиенту; operator_case — как решали похожее обращение, только подсказка.
    kind: str = "answer"


class CopilotDraft(ApiModel):
    reply_to: str = ""
    """Черновик ответа по запросу оператора; generated — писала ли его модель."""

    draft: str
    sources: list[KbHit]
    generated: bool
    status: str = "ready"
    error: str = ""
    summary: str = ""
    checks: list[str] = Field(default_factory=list)
    missing: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    source_ids: list[str] = Field(default_factory=list)
    mode: str = "clarify"
    run_id: str = ""
    version: int = 0


class QuickReplyOut(ApiModel):
    id: str
    title: str
    text: str
    usage_count: int


class Ack(ApiModel):
    """Подтверждение действия без данных: взял, отправил, закрыл, учёл."""

    status: Literal["ok", "taken", "sent", "closed"]


class DraftArticle(ApiModel):
    """Черновик статьи из удачного ответа оператора."""

    article_id: str
    status: Literal["draft"]
    note: str
