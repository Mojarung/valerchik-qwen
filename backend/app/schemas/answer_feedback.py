"""Оценки ответов, продолжение после неудачи и очередь редактора."""

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from app.schemas.base import ApiModel
from app.schemas.common import ArticleIn

FeedbackReason = Literal["", "unclear", "irrelevant", "incorrect", "not_working", "other"]
ReviewStatus = Literal["open", "in_progress", "fixed", "verified", "dismissed", "withdrawn"]


class AnswerRatingRequest(BaseModel):
    value: Literal[-1, 0, 1]
    reason: FeedbackReason = ""
    comment: str = Field(default="", max_length=2000)


class AnswerRatingSaved(ApiModel):
    status: Literal["ok"] = "ok"
    value: int
    reason: FeedbackReason = ""
    comment: str = ""


class AnswerRecoveryRequest(BaseModel):
    action: Literal["explain", "clarify", "operator"]
    detail: str = Field(default="", max_length=2000)

    @model_validator(mode="after")
    def require_detail(self) -> AnswerRecoveryRequest:
        if self.action == "clarify" and not self.detail.strip():
            raise ValueError("Уточните вопрос или шаг, на котором возникла проблема")
        return self


class AnswerReviewItem(ApiModel):
    message_id: str
    session_id: str
    value: int
    reason: str
    comment: str
    snapshot: dict[str, Any]
    status: str
    resolution_note: str
    article_id: str | None
    recovery: dict[str, Any]
    created_at: str
    updated_at: str


class AnswerReviewPage(ApiModel):
    items: list[AnswerReviewItem]
    total: int
    positive: int
    negative: int
    open_count: int


class AnswerReviewUpdate(BaseModel):
    status: ReviewStatus
    note: str = Field(default="", max_length=2000)


class AnswerReviewResolution(BaseModel):
    article_id: str | None = None
    article: ArticleIn
    note: str = Field(min_length=3, max_length=2000)
    expected_updated_at: str | None = None

    @field_validator("note", mode="before")
    @classmethod
    def trim_note(cls, value: Any) -> Any:
        return value.strip() if isinstance(value, str) else value


class AnswerReviewResolved(ApiModel):
    article_id: str
    status: Literal["fixed", "verified"] = "fixed"
