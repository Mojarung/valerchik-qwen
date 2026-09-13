"""Модели ответов роутера voice."""

from app.schemas.base import ApiModel


class Transcription(ApiModel):
    """Распознанная реплика."""

    text: str
    duration: float
    language: str | None = None
