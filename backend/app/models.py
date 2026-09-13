"""ORM-модели. JSON-колонки работают и в PostgreSQL, и в SQLite."""

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, Boolean, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base, UTCDateTime


def utcnow() -> datetime:
    return datetime.now(UTC)


def new_id() -> str:
    return uuid.uuid4().hex


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(256))
    role: Mapped[str] = mapped_column(String(16), default="user")  # user | operator | admin
    display_name: Mapped[str] = mapped_column(String(128), default="")
    line: Mapped[int | None] = mapped_column(Integer, nullable=True)  # линия поддержки оператора
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)


class KBArticle(Base):
    __tablename__ = "kb_articles"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    source_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    question: Mapped[str] = mapped_column(Text)
    answer: Mapped[str] = mapped_column(Text)
    # В импортных выгрузках тема приходит из внешней системы и иногда длиннее
    # 128 символов. Это исходные данные, поэтому сохраняем её целиком, а не
    # обрезаем на входе.
    category: Mapped[str] = mapped_column(Text, index=True)
    keywords: Mapped[list[str]] = mapped_column(JSON, default=list)
    # Иллюстрации из исходной методички: [{url, caption, page, figure}]. Текст
    # инструкций постоянно ссылается «нажмите (Рисунок 26 (2))» — без картинки
    # такой ответ теряет смысл.
    images: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    line: Mapped[int] = mapped_column(Integer, default=1)  # целевая линия поддержки темы
    # Кому адресована статья: all | supplier | customer. У портала две стороны, и
    # «как зарегистрироваться» для поставщика и заказчика — разные инструкции.
    # Nullable ради досинхронизации схемы на живом стенде; пустое = для всех.
    audience: Mapped[str | None] = mapped_column(String(16), nullable=True, default="all", index=True)
    status: Mapped[str] = mapped_column(String(16), default="active", index=True)  # active|draft|archived
    source: Mapped[str] = mapped_column(String(32), default="seed")  # seed|manual|operator|gap|import
    # Откуда приехала статья: {file, locator, source, authored}. `authored` — текст
    # написан человеком, а не собран нами: только такой ответ отдаётся дословно.
    # Nullable ради досинхронизации схемы на живом стенде (см. db._add_missing_columns).
    origin: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True, default=None)
    # Документ, из которого приехала статья (нормализованный заголовок импорта).
    # По нему повторная загрузка того же документа заменяет прошлую версию, а не
    # удваивает базу: до этого один и тот же файл, залитый дважды, давал две
    # одинаковые статьи, и агент цитировал случайную из них.
    doc_key: Mapped[str] = mapped_column(String(64), default="", index=True)
    views: Mapped[int] = mapped_column(Integer, default=0)
    helpful: Mapped[int] = mapped_column(Integer, default=0)
    not_helpful: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, onupdate=utcnow)


class ChatSession(Base):
    __tablename__ = "chat_sessions"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    user_id: Mapped[str | None] = mapped_column(String(32), ForeignKey("users.id"), nullable=True)
    channel: Mapped[str] = mapped_column(String(16), default="web")  # web|voice|api
    # active | waiting_operator | operator_active | resolved | closed_violation
    status: Mapped[str] = mapped_column(String(24), default="active", index=True)
    line: Mapped[int | None] = mapped_column(Integer, nullable=True)
    operator_id: Mapped[str | None] = mapped_column(String(32), ForeignKey("users.id"), nullable=True)
    escalation_reason: Mapped[str] = mapped_column(Text, default="")
    escalated_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    # Сколько раз обращение возвращалось в очередь из-за молчания оператора.
    # Видно в очереди: диалог, который бросили дважды, разбирать надо иначе.
    reclaimed_count: Mapped[int | None] = mapped_column(Integer, nullable=True, default=0)
    # Кто отпустил обращение последним — чтобы диспетчер не вернул его тому же
    # молчащему оператору на следующем же проходе.
    last_operator_id: Mapped[str | None] = mapped_column(
        String(32),
        ForeignKey("users.id"),
        nullable=True,
    )
    topic: Mapped[str] = mapped_column(String(128), default="")
    # Роль собеседника: supplier | customer, пусто — не сказал. Живёт у диалога,
    # а не только в сообщении: сказанное один раз «я заказчик» действует до конца.
    audience: Mapped[str | None] = mapped_column(String(16), nullable=True)
    # Раздел портала, с которого начался диалог. Какие разделы порождают больше
    # всего вопросов — самостоятельный отчёт: он показывает, где документация
    # или сам интерфейс не справляются.
    page_url: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, index=True)
    closed_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    workflow_version: Mapped[int | None] = mapped_column(Integer, default=0, nullable=True)
    assigned_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    waiting_reason: Mapped[str | None] = mapped_column(String(24), nullable=True)
    routing: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    read_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)

    messages: Mapped[list[Message]] = relationship(back_populates="session", cascade="all, delete-orphan")


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    session_id: Mapped[str] = mapped_column(String(32), ForeignKey("chat_sessions.id"), index=True)
    role: Mapped[str] = mapped_column(String(16))  # user | assistant | operator | system
    content: Mapped[str] = mapped_column(Text)
    citations: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON, default=list
    )  # [{article_id, question, score}]
    meta: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=dict
    )  # confidence, tool_trace, flags, latency_ms
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, index=True)

    session: Mapped[ChatSession] = relationship(back_populates="messages")


class SupportEvent(Base):
    """Durable notification outbox; an event is retried with the same ID."""

    __tablename__ = "support_events"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    session_id: Mapped[str] = mapped_column(String(32), ForeignKey("chat_sessions.id"), index=True)
    room: Mapped[str] = mapped_column(String(96))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    delivered_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True, index=True)


class SupportCommand(Base):
    """Idempotency receipt, scoped to an authenticated actor and a session."""

    __tablename__ = "support_commands"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    session_id: Mapped[str] = mapped_column(String(32), ForeignKey("chat_sessions.id"), index=True)
    fingerprint: Mapped[str] = mapped_column(String(64))
    result: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class CopilotRun(Base):
    __tablename__ = "copilot_runs"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    session_id: Mapped[str] = mapped_column(String(32), ForeignKey("chat_sessions.id"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)


class Feedback(Base):
    __tablename__ = "feedback"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    session_id: Mapped[str] = mapped_column(String(32), ForeignKey("chat_sessions.id"), index=True)
    specialist_kind: Mapped[str] = mapped_column(String(16), default="ai")  # ai | operator
    operator_id: Mapped[str | None] = mapped_column(String(32), ForeignKey("users.id"), nullable=True)
    rating: Mapped[int] = mapped_column(Integer)  # 1..5
    comment: Mapped[str] = mapped_column(Text, default="")
    sentiment: Mapped[float | None] = mapped_column(Float, nullable=True)  # -1..1
    # Два измерения из задания: «работа специалиста» (решил ли проблему) и
    # «качество донесения информации» (понятно ли объяснил). Одна оценка 1–5 их
    # смешивает: «3» ставят и за вежливый, но бесполезный ответ, и за верный, но
    # непонятный — а лечатся эти беды по-разному. None — человек вопрос
    # пропустил или отзыв пришёл от старого клиента; nullable ещё и ради
    # досинхронизации схемы на живом стенде (db._add_missing_columns).
    solved: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    clear: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, index=True)


class AnswerFeedback(Base):
    """Одна актуальная оценка ответа и связанное с ней замечание редактору."""

    __tablename__ = "answer_feedback"

    message_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("messages.id", ondelete="CASCADE"), primary_key=True
    )
    session_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("chat_sessions.id", ondelete="CASCADE"), index=True
    )
    value: Mapped[int] = mapped_column(Integer, default=0)
    reason: Mapped[str] = mapped_column(String(32), default="")
    comment: Mapped[str] = mapped_column(Text, default="")
    snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(24), default="open", index=True)
    resolution_note: Mapped[str] = mapped_column(Text, default="")
    resolution_fingerprint: Mapped[str] = mapped_column(String(64), default="")
    article_id: Mapped[str | None] = mapped_column(
        String(32), ForeignKey("kb_articles.id", ondelete="SET NULL"), nullable=True
    )
    recovery: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, onupdate=utcnow)


class SettingEntry(Base):
    __tablename__ = "settings_entries"

    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    value: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)  # {"v": <значение>}
    category: Mapped[str] = mapped_column(String(64), default="general")
    description: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, onupdate=utcnow)


class KBGap(Base):
    """Вопросы, на которые база знаний не ответила, — кандидаты на новые статьи."""

    __tablename__ = "kb_gaps"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    query_norm: Mapped[str] = mapped_column(String(512), index=True)
    query_example: Mapped[str] = mapped_column(Text)
    hits: Mapped[int] = mapped_column(Integer, default=1)
    best_score: Mapped[float] = mapped_column(Float, default=0.0)
    status: Mapped[str] = mapped_column(String(16), default="open")  # open|converted|dismissed
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    last_seen: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)


class QuickReply(Base):
    """Шаблоны/чипсы для операторов и пользовательского чата."""

    __tablename__ = "quick_replies"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    title: Mapped[str] = mapped_column(String(128))
    text: Mapped[str] = mapped_column(Text)
    audience: Mapped[str] = mapped_column(String(16), default="operator")  # operator | user
    line: Mapped[int | None] = mapped_column(Integer, nullable=True)
    usage_count: Mapped[int] = mapped_column(Integer, default=0)


class AnalyticsReport(Base):
    """Сохранённые отчёты фоновой аналитики (кластеры проблем, заключения)."""

    __tablename__ = "analytics_reports"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    kind: Mapped[str] = mapped_column(String(32), index=True)  # feedback_summary
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, index=True)


class IngestJob(Base):
    """Импорт методички: долгая работа, которую нельзя держать в HTTP-запросе.

    Документ на 300 страниц даёт полторы тысячи кусков, и на каждый идёт запрос к
    LLM за формулировкой вопроса — это десятки минут. Поэтому запрос только ставит
    задачу, а прогресс копится здесь: администратор видит, сколько сделано, и
    может уйти со страницы.
    """

    __tablename__ = "ingest_jobs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    title: Mapped[str] = mapped_column(String(256))
    filename: Mapped[str] = mapped_column(String(256), default="")
    # Документ передаём воркеру через хранилище, а не через эту строку: разбор PDF
    # с разметкой страниц идёт минуты и обязан происходить в воркере, значит ему
    # нужен исходный файл, а не готовый текст. Для импорта текстом остаётся `text`.
    source_key: Mapped[str] = mapped_column(String(512), default="")
    text: Mapped[str] = mapped_column(Text, default="")
    params: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    # Отпечаток содержимого: в логе видно, залили тот же файл заново или новую версию.
    content_sha256: Mapped[str] = mapped_column(String(64), default="")
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    # pending | running | done | error | cancelled
    total: Mapped[int] = mapped_column(Integer, default=0)
    processed: Mapped[int] = mapped_column(Integer, default=0)
    created_articles: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str] = mapped_column(Text, default="")
    actor: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, onupdate=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)


class McpServer(Base):
    """Внешний MCP-сервер: его инструменты подмешиваются в ReAct-агента.

    Реестр живёт в БД, а не в переменных окружения, потому что подключать и
    отключать источники нужно из админки на лету — как и всё остальное в сервисе.
    """

    __tablename__ = "mcp_servers"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    url: Mapped[str] = mapped_column(Text)  # http(s)-эндпоинт MCP или путь к stdio-скрипту
    description: Mapped[str] = mapped_column(Text, default="")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    headers: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)  # авторизация и прочие заголовки
    timeout_s: Mapped[float] = mapped_column(Float, default=20.0)
    # Результат последней проверки: админке нужно показывать, жив ли сервер и что он умеет
    last_status: Mapped[str] = mapped_column(String(16), default="unknown")  # ok|error|unknown
    last_error: Mapped[str] = mapped_column(Text, default="")
    tools: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)  # [{name, description}]
    checked_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, onupdate=utcnow)


class GuideRecord(Base):
    """Пошаговый сценарий по порталу, заведённый из админки.

    Заводской набор — файл app/guides/portal.json, его правит разработчик.
    Здесь то, что записали администраторы: запись с тем же идентификатором
    перекрывает заводскую, выключенная — прячет её. Идентификатор человеческий
    (слаг), потому что он же ключ наложения и по нему сценарий узнают в логах,
    тестах и адресной строке админки.
    """

    __tablename__ = "guides"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    title: Mapped[str] = mapped_column(String(200), default="")
    audience: Mapped[str] = mapped_column(String(16), default="all", index=True)
    questions: Mapped[list[str]] = mapped_column(JSON, default=list)  # формулировки для подбора
    steps: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)  # шаги с локаторами портала
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    source: Mapped[str] = mapped_column(String(16), default="admin")  # admin|recorder|llm
    author: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, onupdate=utcnow)


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    actor: Mapped[str] = mapped_column(String(64), default="system")
    action: Mapped[str] = mapped_column(String(64), index=True)
    entity: Mapped[str] = mapped_column(String(64), default="")
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, index=True)


# Register the source lifecycle for every ORM writer, including imports and seeds.
from app.services import source_conflict_lifecycle as _source_conflict_lifecycle  # noqa: E402, F401
