"""Рабочая очередь замечаний к ответам; изменение БЗ только администратором."""

from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Request

from app.schemas.answer_feedback import (
    AnswerReviewItem,
    AnswerReviewPage,
    AnswerReviewResolution,
    AnswerReviewResolved,
    AnswerReviewUpdate,
)
from app.schemas.kb import ArticleOut
from app.security import AdminUser, StaffUser
from app.services import answer_feedback, articles

router = APIRouter(prefix="/answer-feedback", tags=["answer-feedback"])


@router.get("", response_model=AnswerReviewPage)
async def listing(
    request: Request,
    user: StaffUser,
    status: Literal["pending", "all", "open", "in_progress", "fixed", "verified", "dismissed"] = "pending",
    page: int = Query(default=1, ge=1),
) -> AnswerReviewPage:
    return await answer_feedback.listing(request.app.state.rt, status, page)


@router.get("/articles/{article_id}", response_model=ArticleOut)
async def get_article(request: Request, article_id: str, user: StaffUser) -> ArticleOut:
    result = await articles.detail(request.app.state.rt.db.session_factory, article_id)
    if result is None:
        raise HTTPException(404, "Статья не найдена")
    return result


@router.patch("/{message_id}", response_model=AnswerReviewItem)
async def review(
    request: Request,
    message_id: str,
    body: AnswerReviewUpdate,
    user: AdminUser,
) -> AnswerReviewItem:
    return await answer_feedback.set_status(request.app.state.rt, message_id, body, user.username)


@router.post("/{message_id}/resolve", response_model=AnswerReviewResolved)
async def resolve(
    request: Request,
    message_id: str,
    body: AnswerReviewResolution,
    user: AdminUser,
) -> AnswerReviewResolved:
    return await answer_feedback.resolve(request.app.state.rt, message_id, body, user.username)
