"""Модели ответов роутера admin."""

from typing import Any, Literal

from pydantic import Field

from app.schemas.base import ApiModel, SparseModel, StoredModel


class SettingEntryOut(ApiModel):
    """Одна настройка в админке.

    `label` — человеческое название (подпись поля), `key` остаётся вторичной
    подписью для тех, кто читает логи; `secret` говорит интерфейсу, что значение
    замаскировано и показывать его как обычный текст нельзя.
    """

    key: str
    value: Any = None
    category: str
    label: str = Field(min_length=1)
    description: str
    secret: bool = False


class SettingsSaved(ApiModel):
    changed: list[str]
    note: str


class SessionStats(ApiModel):
    total: int
    by_status: dict[str, int]
    # Ключ — линия поддержки строкой: у JSON не бывает числовых ключей
    escalated_by_line: dict[str, int]


class QualityStats(ApiModel):
    """Качество ответов; None — данных для метрики ещё нет."""

    auto_answer_rate: float | None
    avg_confidence: float | None
    median_latency_ms: int | None
    avg_rating: float | None
    feedback_count: int
    # Удовлетворённость (доля 4–5) и ответы на два вопроса после обращения
    csat: float | None = None
    solved_rate: float | None = None
    clear_rate: float | None = None


class KbStats(ApiModel):
    active_articles: int
    open_gaps: int


class DayStats(ApiModel):
    day: str
    sessions: int
    escalated: int
    violations: int
    avg_rating: float | None


class OverviewStats(ApiModel):
    sessions: SessionStats
    quality: QualityStats
    kb: KbStats
    series: list[DayStats]


class TopicRow(StoredModel):
    """Тема обращений: группа переформулировок одной проблемы."""

    label: str
    count: int
    previous: int
    delta: int
    # Сглаженное отношение к прошлому периоду: (сейчас + 1) / (было + 1)
    growth: float
    escalated: int
    examples: list[str] = []


class TopicsReport(StoredModel):
    days: int
    generated_at: str
    messages: int
    topics: list[TopicRow] = []


class TopicsState(ApiModel):
    """Отчёт по темам обращений; stale — устарел, и пересчёт уже идёт в фоне."""

    report: TopicsReport | None
    stale: bool
    computing: bool


class JobStarted(ApiModel):
    status: Literal["started", "already_running"]


class EvalMetrics(StoredModel):
    """Метрики прогона; у пустого прогона их нет вовсе."""

    accuracy: float | None = None
    answer_rate: float | None = None
    # Главная метрика риска: ответил уверенно и мимо
    confident_wrong_rate: float | None = None
    avg_confidence: float | None = None
    median_latency_ms: int | None = None


class TopicScore(StoredModel):
    topic: str
    total: int
    hits: int


class EvalMiss(StoredModel):
    """Промах прогона — то, с чем идут править базу знаний."""

    question: str
    topic: str
    confidence: float
    answer: str
    confident_wrong: bool


class EvalReport(StoredModel):
    """Отчёт прогона золотого набора: измеримое «стало лучше или хуже»."""

    generated_at: str
    total: int
    metrics: EvalMetrics
    by_topic: list[TopicScore] = []
    misses: list[EvalMiss] = []


class EvalProgress(StoredModel):
    done: int
    total: int


class EvalState(ApiModel):
    """Последний прогон золотого набора, предыдущий — для сравнения, и ход текущего."""

    report: EvalReport | None
    previous: EvalReport | None
    running: bool
    progress: EvalProgress | None
    questions: int


class FeedbackItem(ApiModel):
    id: str
    session_id: str
    rating: int
    comment: str
    sentiment: float | None
    specialist_kind: str
    # Кто вёл диалог: имя оператора или «ИИ-ассистент»
    operator: str
    created_at: str
    # Ответы на «Проблема решена?» и «Понятно объяснили?»; None — вопрос пропущен
    solved: bool | None = None
    clear: bool | None = None


class FeedbackSummary(StoredModel):
    """Сводка отзывов; при пустой выборке есть только count."""

    count: int
    avg_rating: float | None = None
    positive_share: float | None = None
    negative_count: int = 0
    ai_count: int = 0
    operator_count: int = 0
    # Доли «решено» и «понятно» среди тех, кто ответил на вопрос; total — сколько ответили
    solved_rate: float | None = None
    solved_total: int = 0
    clear_rate: float | None = None
    clear_total: int = 0


class WeakSpot(StoredModel):
    """Слабое место специалиста по одному измерению: не «плохой», а «что именно»."""

    dimension: str  # solved | clear | csat | speed | abandoned | refusal
    bad: int
    total: int
    text: str
    advice: str = ""


class SpecialistScore(StoredModel):
    """Разрез отзывов по тому, кто вёл диалог; ИИ — тоже специалист.

    Новые поля необязательны: отчёт хранится JSON-ом, и сформированный прежней
    версией не должен ронять страницу.
    """

    id: str
    name: str
    kind: Literal["ai", "operator"]
    count: int
    avg_rating: float
    negative_share: float
    avg_sentiment: float | None = None
    # Сколько диалогов по каждой линии поддержки: ключ — номер линии строкой
    lines: dict[str, int] = {}
    sample_negative: str | None = None
    cases: int | None = None
    # Средняя оценка, сглаженная к средней по службе (Байес, 5 «средних» отзывов)
    smoothed_rating: float | None = None
    csat: float | None = None
    solved_rate: float | None = None
    solved_total: int = 0
    clear_rate: float | None = None
    clear_total: int = 0
    median_first_reply_s: float | None = None
    median_duration_s: float | None = None
    abandoned: int = 0
    auto_resolved_rate: float | None = None
    refusal_rate: float | None = None
    thumbs_up: int = 0
    thumbs_down: int = 0
    # Из thumbs_down — с причиной «Непонятно»: сигнал о донесении, а не о правильности
    thumbs_unclear: int = 0
    thumbs_up_share: float | None = None
    # Повторные обращения: доля и по скольким обращениям её удалось проверить
    repeat_rate: float | None = None
    repeat_checked: int = 0
    # Составляющие индекса 0..1 (solved, clear, csat, speed); None — нет данных
    components: dict[str, float | None] = {}
    index: int | None = None
    # Две части индекса отдельно: результат работы (решено, звёзды, скорость)
    # и качество донесения (понятно объяснили)
    result_index: int | None = None
    clarity_index: int | None = None
    low_data: bool = False
    weak_spots: list[WeakSpot] = []


class FeedbackCluster(StoredModel):
    """Группа похожих негативных комментариев — одна системная проблема."""

    size: int
    representative: str
    keywords: list[str] = []
    examples: list[str] = []


class TopicQuality(StoredModel):
    """Тема обращения × доля нерешённых и непонятных."""

    topic: str
    count: int
    unsolved: int = 0
    solved_total: int = 0
    unsolved_share: float | None = None
    unclear: int = 0
    clear_total: int = 0
    unclear_share: float | None = None
    negative_share: float | None = None
    flagged: bool = False


class TrendWindow(StoredModel):
    count: int
    negative: int = 0
    negative_share: float | None = None
    unsolved_share: float | None = None
    unclear_share: float | None = None


class NegativeTrend(StoredModel):
    """Негатив за последние дни против таких же дней до них."""

    days: int
    current: TrendWindow
    previous: TrendWindow
    growing: bool = False


class SystemProblem(StoredModel):
    """Одна системная проблема простыми словами: что, в чём видно, что делать."""

    kind: str  # specialist | topic | article | trend | complaint
    title: str
    detail: str
    advice: str = ""
    # Кому адресован совет: специалист, «тема «…»», «статья «…»»; пусто — всей службе
    target: str = ""


class Methodology(StoredModel):
    """Параметры, с которыми считался отчёт: веса индекса и пороги."""

    weights: dict[str, float] = {}
    prior_weight: int = 5
    # Средние по службе (0..1: solved, clear, csat), к которым тянется оценка при малом числе отзывов
    baseline: dict[str, float] = {}
    min_reviews: int = 5
    reply_target_seconds: float = 120
    repeat_window_hours: int = 48


class SupportProblem(StoredModel):
    """Повторяющаяся проблема по выгрузке СТП: подтема и слова обращений."""

    topic: str
    subtopic: str
    count: int
    brush_off_share: float = 0
    mass_share: float = 0
    keywords: list[str] = []
    example: str = ""


class SupportTopic(StoredModel):
    """Тема выгрузки СТП: как по ней отвечали операторы."""

    topic: str
    count: int
    share: float = 0
    explained_share: float = 0
    brush_off_share: float = 0
    redirect_share: float = 0
    ask_share: float = 0
    status_share: float = 0
    short_share: float = 0
    mass_share: float = 0
    incident_share: float = 0
    median_answer_chars: int = 0
    top_subtopics: list[SupportProblem] = []


class MassIncident(StoredModel):
    """Массовая задача (сбой), на которую ссылались ответы операторов."""

    number: str
    count: int
    topics: list[str] = []
    example: str = ""


class SupportExportReport(StoredModel):
    """Качество ответов по реальной выгрузке СТП (офлайн-анализ текстов ответов)."""

    generated_at: str
    source: str = ""
    total: int
    answer_kinds: dict[str, int] = {}
    explained_share: float = 0
    brush_off_share: float = 0
    mass_share: float = 0
    incident_share: float = 0
    median_answer_chars: int = 0
    topics: list[SupportTopic] = []
    problems: list[SupportProblem] = []
    mass_incidents: list[MassIncident] = []
    # Сколько разных массовых задач упомянуто в ответах (в списке — только крупнейшие)
    mass_task_count: int = 0
    conclusion: str = ""
    notes: list[str] = []


class FeedbackReport(SparseModel):
    """Последний сводный отчёт по отзывам; exists=false — его ещё не формировали.

    `support_export` — отчёт по реальной выгрузке СТП; живёт отдельно от отзывов
    и может быть и тогда, когда отзывов ещё нет.
    """

    exists: bool
    created_at: str | None = None
    summary: FeedbackSummary | None = None
    specialists: list[SpecialistScore] | None = None
    clusters: list[FeedbackCluster] | None = None
    topics: list[TopicQuality] | None = None
    trend: NegativeTrend | None = None
    problems: list[SystemProblem] | None = None
    recommendations: list[str] | None = None
    methodology: Methodology | None = None
    conclusion: str | None = None
    generated_at: str | None = None
    support_export: SupportExportReport | None = None


class Queued(ApiModel):
    """Задача ушла в фоновую очередь."""

    status: Literal["queued"]
    note: str


class GapOut(ApiModel):
    """Вопрос, на который база знаний не ответила."""

    id: str
    query: str
    hits: int
    best_score: float
    last_seen: str


class GapConverted(ApiModel):
    article_id: str


class GapDismissed(ApiModel):
    status: Literal["dismissed"]


#: Результат последней проверки MCP-сервера; строку из колонки сверяет модель.
McpStatus = Literal["ok", "error", "unknown"]


class McpTool(StoredModel):
    name: str
    description: str = ""


class McpServerOut(ApiModel):
    """MCP-сервер в реестре. Значения заголовков не отдаются никому, включая
    админа: там боевые ключи внешних систем, — только имена под «***»."""

    id: str
    name: str
    url: str
    description: str
    enabled: bool
    headers: dict[str, str]
    timeout_s: float
    last_status: McpStatus
    last_error: str
    tools: list[McpTool]
    checked_at: str | None


class McpCheck(ApiModel):
    status: Literal["ok", "error"]
    tools: list[McpTool]
    error: str


class McpDeleted(ApiModel):
    status: Literal["deleted"]


class ClassifierInfo(SparseModel):
    """Состояние классификатора линий; необученный — trained=false и без метрик."""

    trained: bool
    labels: list[int] | None = None
    accuracy: float | None = None
    samples: int | None = None
    embedder: str | None = None
    trained_at: float | None = None


class AuditEntry(ApiModel):
    actor: str
    action: str
    entity: str
    payload: dict[str, Any] | None
    created_at: str
