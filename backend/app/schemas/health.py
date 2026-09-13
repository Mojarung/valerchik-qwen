"""Модели ответов роутера health: здоровье сервиса и настройки для фронтенда."""

from typing import Literal

from app.schemas.base import ApiModel


class HealthChecks(ApiModel):
    db: Literal["ok", "fail"]
    cache: Literal["ok", "fail"]
    # Число статей в векторном индексе строкой — или "fail". Сходится с числом
    # активных статей базы; точек больше (у статьи бывают формулировки).
    vector_index: str
    # Число точек векторного индекса строкой — или "fail"
    vector_points: str


class HealthResponse(ApiModel):
    status: Literal["ok", "degraded"]
    mode: str
    checks: HealthChecks


class LiveVoice(ApiModel):
    enabled: bool
    silence_ms: int


class LineInfo(ApiModel):
    label: str
    desc: str


class AudienceOption(ApiModel):
    key: Literal["supplier", "customer"]
    label: str
    hint: str


class FeedbackPrompt(ApiModel):
    idle_prompt_seconds: int
    request_message: str


class QueueThresholds(ApiModel):
    warn_seconds: int
    alert_seconds: int


class MetaResponse(ApiModel):
    """Всё, что фронту нужно знать о сервере до первого действия."""

    app: str
    mode: str
    demo: bool
    max_document_mb: int
    voice_available: bool
    tts_available: bool
    live_voice: LiveVoice
    docs_parsing: bool
    llm_configured: bool
    lines: dict[str, LineInfo]
    audiences: list[AudienceOption]
    feedback: FeedbackPrompt
    operator_queue: QueueThresholds
