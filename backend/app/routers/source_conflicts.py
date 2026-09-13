"""Staff evidence inspection, admin review and explicit bounded audit launches."""

from fastapi import APIRouter, HTTPException, Query, Request

from app.schemas.source_conflicts import (
    AuditJobOut,
    AutoAuditBackfillRequest,
    AutoAuditBackfillResponse,
    AutoAuditEntry,
    AutoAuditStatus,
    ConflictAuditRequest,
    ConflictGroup,
    ConflictResult,
    ConflictReview,
    Outcome,
)
from app.security import AdminUser, StaffUser
from app.services.source_conflict_auto import SourceConflictAutoWorker
from app.services.source_conflict_runner import AuditConfigurationError, AuditQueueBusy
from app.services.source_conflicts import SourceConflictRegistry

router = APIRouter(prefix="/api/source-conflicts", tags=["source-conflicts"])


def _registry(request: Request) -> SourceConflictRegistry:
    return request.app.state.rt.source_conflict_runner.registry  # type: ignore[no-any-return]


@router.get("/groups", response_model=list[ConflictGroup])
async def groups(
    request: Request,
    user: StaffUser,
    reviewed: bool | None = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> list[ConflictGroup]:
    return await _registry(request).list_groups(reviewed=reviewed, limit=limit, offset=offset)


@router.get("/groups/{group_id}", response_model=ConflictGroup)
async def group(request: Request, group_id: str, user: StaffUser) -> ConflictGroup:
    found = await _registry(request).get_group(group_id)
    if found is None:
        raise HTTPException(404, "Группа расхождений не найдена")
    return found


@router.patch("/groups/{group_id}/review", response_model=ConflictGroup)
async def review_group(
    request: Request, group_id: str, body: ConflictReview, user: AdminUser
) -> ConflictGroup:
    try:
        return await _registry(request).review_group(group_id, body, user.username)
    except LookupError as exc:
        raise HTTPException(404, "Группа расхождений не найдена") from exc
    except ValueError as exc:
        raise HTTPException(409, "Можно проверить только актуальную группу расхождений") from exc


@router.get("/results", response_model=list[ConflictResult])
async def results(  # noqa: PLR0913 — independently optional result filters
    request: Request,
    user: StaffUser,
    article_id: str | None = None,
    outcome: Outcome | None = None,
    reviewed: bool | None = None,
    include_stale: bool = False,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> list[ConflictResult]:
    return await _registry(request).list_results(
        article_id=article_id,
        outcome=outcome,
        reviewed=reviewed,
        include_stale=include_stale,
        limit=limit,
        offset=offset,
    )


@router.get("/results/{result_id}", response_model=ConflictResult)
async def result(request: Request, result_id: str, user: StaffUser) -> ConflictResult:
    found = await _registry(request).get_result(result_id)
    if found is None:
        raise HTTPException(404, "Сравнение не найдено")
    return found


@router.patch("/results/{result_id}/review", response_model=ConflictResult)
async def review(request: Request, result_id: str, body: ConflictReview, user: AdminUser) -> ConflictResult:
    try:
        return await _registry(request).review(result_id, body, user.username)
    except LookupError as exc:
        raise HTTPException(404, "Сравнение не найдено") from exc
    except ValueError as exc:
        raise HTTPException(409, "Можно проверить только актуальное противоречие") from exc


@router.get("/jobs", response_model=list[AuditJobOut])
async def jobs(
    request: Request,
    user: StaffUser,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> list[AuditJobOut]:
    return await _registry(request).list_jobs(limit=limit, offset=offset)


@router.get("/jobs/{job_id}", response_model=AuditJobOut)
async def job(request: Request, job_id: str, user: StaffUser) -> AuditJobOut:
    found = await _registry(request).get_job(job_id)
    if found is None:
        raise HTTPException(404, "Задание не найдено")
    return found


@router.post("/jobs", response_model=AuditJobOut, status_code=202)
async def start(request: Request, body: ConflictAuditRequest, user: AdminUser) -> AuditJobOut:
    rt = request.app.state.rt
    try:
        queued: AuditJobOut = await rt.source_conflict_runner.start(body, user.username)
        return queued
    except AuditQueueBusy as exc:
        raise HTTPException(409, str(exc)) from exc
    except AuditConfigurationError as exc:
        raise HTTPException(503, str(exc)) from exc


@router.post("/jobs/{job_id}/resume", response_model=AuditJobOut, status_code=202)
async def resume(request: Request, job_id: str, user: AdminUser) -> AuditJobOut:
    rt = request.app.state.rt
    try:
        queued: AuditJobOut = await rt.source_conflict_runner.resume(job_id, user.username)
        return queued
    except LookupError as exc:
        raise HTTPException(404, "Задание не найдено") from exc
    except AuditQueueBusy as exc:
        raise HTTPException(409, str(exc)) from exc
    except AuditConfigurationError as exc:
        raise HTTPException(503, str(exc)) from exc


def _automation(request: Request) -> SourceConflictAutoWorker:
    return request.app.state.rt.source_conflict_auto  # type: ignore[no-any-return]


@router.get("/automation", response_model=AutoAuditStatus)
async def automation_status(
    request: Request, user: StaffUser, limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0)
) -> AutoAuditStatus:
    return await _automation(request).status(limit=limit, offset=offset)


@router.post("/automation/backfill", response_model=AutoAuditBackfillResponse, status_code=202)
async def automation_backfill(
    request: Request, body: AutoAuditBackfillRequest, user: AdminUser
) -> AutoAuditBackfillResponse:
    return await _automation(request).backfill()


@router.post("/automation/{item_id}/retry", response_model=AutoAuditEntry, status_code=202)
async def automation_retry(request: Request, item_id: str, user: AdminUser) -> AutoAuditEntry:
    try:
        return await _automation(request).retry(item_id)
    except LookupError as exc:
        raise HTTPException(404, "Задание автоматической проверки не найдено") from exc
    except ValueError as exc:
        raise HTTPException(
            409, "Повтор доступен только для актуальной статьи с остановленной проверкой"
        ) from exc
