"""Админ-панель: настройки на лету, статистика, аналитика отзывов, пробелы БЗ,
переобучение, выгрузки, аудит."""

from typing import TYPE_CHECKING

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from app.dynamic_settings import SettingsValueError
from app.routers.answer_feedback import router as answer_feedback_router
from app.schemas import GapConvertRequest, McpServerIn, McpServerPatch, SettingsUpdate
from app.schemas.admin import (
    AuditEntry,
    ClassifierInfo,
    EvalProgress,
    EvalReport,
    EvalState,
    FeedbackItem,
    FeedbackReport,
    GapConverted,
    GapDismissed,
    GapOut,
    JobStarted,
    McpCheck,
    McpDeleted,
    McpServerOut,
    McpTool,
    OverviewStats,
    Queued,
    SettingEntryOut,
    SettingsSaved,
    TopicsReport,
    TopicsState,
)
from app.security import AdminUser, StaffUser
from app.services import audit_log, background, feedback_admin, kb_gaps, mcp_registry, quality, topics
from app.services.analytics import overview_stats
from app.services.exports import export_dialogs_csv, export_feedback_csv
from app.services.mcp import refresh_status
from app.services.urlguard import check_external_url
from app.tasks.jobs import feedback_report_task, reindex_kb_task, retrain_classifier_task

if TYPE_CHECKING:
    from app.runtime import AppRuntime

router = APIRouter(prefix="/api/admin", tags=["admin"])
router.include_router(answer_feedback_router)


# --- динамические настройки ---


@router.get("/settings", response_model=list[SettingEntryOut])
async def get_settings_list(request: Request, user: AdminUser) -> list[SettingEntryOut]:
    return [SettingEntryOut.model_validate(entry) for entry in request.app.state.rt.dyn.all()]


@router.put("/settings", response_model=SettingsSaved)
async def update_settings(request: Request, user: AdminUser, body: SettingsUpdate) -> SettingsSaved:
    rt = request.app.state.rt
    # Адрес LLM — это ещё и то, куда уедет API-ключ. Проверяем до записи:
    # подменённый base_url превращает настройку в способ вытащить секрет,
    # который приложение специально маскирует в выдаче.
    if "llm.base_url" in body.updates and str(body.updates["llm.base_url"]).strip():
        await _ensure_url_allowed(rt, str(body.updates["llm.base_url"]))
    try:
        changed = await rt.dyn.set_many(body.updates, actor=user.username)
    except SettingsValueError as exc:
        # 422, а не 500: администратор ошибся значением, и он должен
        # увидеть, каким именно, прямо в форме.
        raise HTTPException(422, str(exc)) from exc
    if not changed:
        raise HTTPException(422, "Ни один из ключей не известен системе")
    await audit_log.record(
        rt.db.session_factory,
        actor=user.username,
        action="settings.update",
        entity="settings",
        payload={k: v for k, v in body.updates.items() if k in changed and "api_key" not in k},
    )
    return SettingsSaved(changed=changed, note="Применено на всех инстансах без перезапуска")


# --- статистика и аналитика ---


@router.get("/stats", response_model=OverviewStats)
async def stats(request: Request, user: StaffUser) -> OverviewStats:
    return OverviewStats.model_validate(await overview_stats(request.app.state.rt))


@router.get("/topics", response_model=TopicsState)
async def topics_report(request: Request, user: StaffUser, days: int = 7) -> TopicsState:
    """Темы обращений и их рост к предыдущему такому же периоду.

    Отдаём сохранённый отчёт, а устаревший пересчитываем в фоне и возвращаем
    прежний с пометкой. Считать синхронно нельзя: расчёт — это эмбеддинги
    тысяч сообщений, то есть десятки секунд, и дашборд повис бы на открытии.
    """
    rt = request.app.state.rt
    if not rt.dyn.get("topics.enabled"):
        raise HTTPException(409, "Группировка по темам отключена в настройках")
    days = max(1, min(days, 90))
    report = await topics.last_report(rt, days)
    stale = topics.is_stale(report, float(rt.dyn.get("topics.refresh_hours")))
    if stale:
        _start_topics_refresh(rt, days)
    return TopicsState(
        report=TopicsReport.model_validate(report) if report else None,
        stale=stale,
        computing=_topics_busy(days),
    )


@router.post("/topics/refresh", response_model=JobStarted)
async def topics_refresh(request: Request, user: AdminUser, days: int = 7) -> JobStarted:
    rt = request.app.state.rt
    days = max(1, min(days, 90))
    started = _start_topics_refresh(rt, days)
    return JobStarted(status="started" if started else "already_running")


_EVAL_JOB = "eval:golden"


def _topics_job(days: int) -> str:
    return f"topics:{days}"


def _topics_busy(days: int) -> bool:
    return background.busy_with(_topics_job(days))


def _start_topics_refresh(rt: AppRuntime, days: int) -> bool:
    """Пересчёт в фоне, по одному на окно.

    Без защиты от повторного запуска каждое открытие дашборда плодило бы ещё
    один проход по тем же тысячам сообщений: расчёт долгий, а вкладок у смены
    открыто много.
    """
    if _topics_busy(days):
        return False
    background.spawn(topics.refresh(rt, days)).set_name(_topics_job(days))
    return True


@router.get("/eval", response_model=EvalState)
async def eval_report(request: Request, user: StaffUser) -> EvalState:
    """Последний прогон золотого набора и предыдущий — для сравнения."""
    rt = request.app.state.rt
    reports = await quality.last_reports(rt, limit=2)
    running = background.busy_with(_EVAL_JOB)
    progress = await quality.progress(rt) if running else None
    return EvalState(
        report=EvalReport.model_validate(reports[0]) if reports else None,
        previous=EvalReport.model_validate(reports[1]) if len(reports) > 1 else None,
        running=running,
        progress=EvalProgress.model_validate(progress) if progress else None,
        questions=len(quality.load_questions(rt.dyn)),
    )


@router.post("/eval/run", response_model=JobStarted)
async def eval_run(request: Request, user: AdminUser) -> JobStarted:
    """Запуск прогона в фоне.

    Синхронно нельзя: полсотни вопросов через настоящую LLM — это минуты, и
    запрос отвалился бы по таймауту раньше, чем прогон закончится. Повторный
    запуск отсекаем — два параллельных прогона просто жгут деньги на LLM.
    """
    rt = request.app.state.rt
    if background.busy_with(_EVAL_JOB):
        return JobStarted(status="already_running")
    background.spawn(quality.run(rt)).set_name(_EVAL_JOB)
    return JobStarted(status="started")


@router.get("/feedback", response_model=list[FeedbackItem])
async def feedback_list(request: Request, user: StaffUser, page: int = 1) -> list[FeedbackItem]:
    return await feedback_admin.recent(request.app.state.rt.db.session_factory, page)


@router.post("/feedback/report", response_model=Queued)
async def generate_feedback_report(request: Request, user: AdminUser) -> Queued:
    await feedback_report_task.kiq()
    return Queued(status="queued", note="Отчёт формируется в фоне, обновите страницу отчёта")


@router.get("/feedback/report", response_model=FeedbackReport, response_model_exclude_unset=True)
async def latest_feedback_report(request: Request, user: StaffUser) -> FeedbackReport:
    return await feedback_admin.latest_report(request.app.state.rt.db.session_factory)


# --- пробелы базы знаний (самообучение) ---


@router.get("/gaps", response_model=list[GapOut])
async def gaps(request: Request, user: StaffUser) -> list[GapOut]:
    return await kb_gaps.open_gaps(request.app.state.rt.db.session_factory)


@router.post("/gaps/{gap_id}/convert", response_model=GapConverted)
async def convert_gap(
    request: Request, gap_id: str, user: AdminUser, body: GapConvertRequest
) -> GapConverted:
    """Один клик: вопрос без ответа превращается в статью БЗ и сразу ищется."""
    rt = request.app.state.rt
    article_id = await kb_gaps.convert(rt.db.session_factory, gap_id, body, actor=user.username)
    if article_id is None:
        raise HTTPException(404, "Пробел не найден")
    if body.publish:
        await rt.kb.index_article(article_id)
    return GapConverted(article_id=article_id)


@router.post("/gaps/{gap_id}/dismiss", response_model=GapDismissed)
async def dismiss_gap(request: Request, gap_id: str, user: AdminUser) -> GapDismissed:
    if not await kb_gaps.dismiss(request.app.state.rt.db.session_factory, gap_id):
        raise HTTPException(404, "Пробел не найден")
    return GapDismissed(status="dismissed")


# --- реестр MCP-серверов ---


@router.get("/mcp", response_model=list[McpServerOut])
async def mcp_list(request: Request, user: StaffUser) -> list[McpServerOut]:
    return await mcp_registry.servers(request.app.state.rt.db.session_factory)


async def _ensure_url_allowed(rt: AppRuntime, url: str) -> None:
    """Адрес, по которому сервер пойдёт сам, не должен вести внутрь сети."""
    problem = await check_external_url(
        url,
        allow_internal=bool(rt.dyn.get("security.allow_internal_urls")),
    )
    if problem:
        raise HTTPException(422, problem)


async def _server_or_404(rt: AppRuntime, server_id: str) -> McpServerOut:
    found = await mcp_registry.server(rt.db.session_factory, server_id)
    if found is None:
        raise HTTPException(404, "MCP-сервер не найден")
    return found


@router.post("/mcp", response_model=McpServerOut)
async def mcp_create(request: Request, user: AdminUser, body: McpServerIn) -> McpServerOut:
    rt = request.app.state.rt
    # Адрес MCP-сервера сервер дёргает сам — та же проверка, что и для LLM.
    # Путь к stdio-скрипту (без схемы) проверку не проходит: такие серверы
    # заводятся файлом на диске, и подставить туда чужой адрес нельзя.
    if "://" in body.url:
        await _ensure_url_allowed(rt, body.url)
    server_id = await mcp_registry.create(rt.db.session_factory, body, actor=user.username)
    if server_id is None:
        raise HTTPException(409, f"MCP-сервер «{body.name}» уже есть")
    # Сразу проверяем подключение: администратор должен увидеть результат, а не «unknown»
    await refresh_status(rt.db.session_factory, server_id)
    return await _server_or_404(rt, server_id)


@router.patch("/mcp/{server_id}", response_model=McpServerOut)
async def mcp_update(request: Request, user: AdminUser, server_id: str, body: McpServerPatch) -> McpServerOut:
    rt = request.app.state.rt
    changes = body.model_dump(exclude_unset=True)
    if changes.get("url") and "://" in str(changes["url"]):
        await _ensure_url_allowed(rt, str(changes["url"]))
    if not await mcp_registry.update(rt.db.session_factory, server_id, changes, actor=user.username):
        raise HTTPException(404, "MCP-сервер не найден")
    # Изменили адрес или заголовки — прежний статус больше ничего не значит
    if {"url", "headers", "timeout_s"} & changes.keys():
        await refresh_status(rt.db.session_factory, server_id)
    return await _server_or_404(rt, server_id)


@router.delete("/mcp/{server_id}", response_model=McpDeleted)
async def mcp_delete(request: Request, user: AdminUser, server_id: str) -> McpDeleted:
    if not await mcp_registry.delete(request.app.state.rt.db.session_factory, server_id, actor=user.username):
        raise HTTPException(404, "MCP-сервер не найден")
    return McpDeleted(status="deleted")


@router.post("/mcp/{server_id}/check", response_model=McpCheck)
async def mcp_check(request: Request, user: AdminUser, server_id: str) -> McpCheck:
    """Проверить подключение и обновить список инструментов."""
    result = await refresh_status(request.app.state.rt.db.session_factory, server_id)
    if result["error"] == "сервер не найден":
        raise HTTPException(404, "MCP-сервер не найден")
    return McpCheck(
        status=result["status"],
        tools=[McpTool.model_validate(tool) for tool in result["tools"]],
        error=result["error"],
    )


# --- обслуживание моделей и индекса ---


@router.post("/reindex", response_model=Queued)
async def reindex(request: Request, user: AdminUser) -> Queued:
    await reindex_kb_task.kiq()
    return Queued(status="queued", note="Blue/green переиндексация: поиск не прерывается")


@router.post("/retrain", response_model=Queued)
async def retrain(request: Request, user: AdminUser) -> Queued:
    await retrain_classifier_task.kiq()
    return Queued(status="queued", note="Классификатор линий дообучается на статьях и диалогах")


@router.get("/classifier", response_model=ClassifierInfo, response_model_exclude_unset=True)
async def classifier_info(request: Request, user: StaffUser) -> ClassifierInfo:
    """Состояние классификатора линий; необученный отвечает trained=false и без метрик."""
    meta = request.app.state.rt.classifier.meta()
    if not meta:
        return ClassifierInfo(trained=False)
    return ClassifierInfo(
        trained=True,
        labels=meta.get("labels"),
        accuracy=meta.get("accuracy"),
        samples=meta.get("samples"),
        embedder=meta.get("embedder"),
        trained_at=meta.get("trained_at"),
    )


# --- выгрузки и аудит ---


@router.get(
    "/export/dialogs.csv",
    response_class=StreamingResponse,
    responses={200: {"content": {"text/csv": {}}, "description": "Диалоги в CSV"}},
)
async def export_dialogs(request: Request, user: StaffUser) -> StreamingResponse:
    return StreamingResponse(
        export_dialogs_csv(request.app.state.rt),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=dialogs.csv"},
    )


@router.get(
    "/export/feedback.csv",
    response_class=StreamingResponse,
    responses={200: {"content": {"text/csv": {}}, "description": "Отзывы в CSV"}},
)
async def export_feedback(request: Request, user: StaffUser) -> StreamingResponse:
    return StreamingResponse(
        export_feedback_csv(request.app.state.rt),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=feedback.csv"},
    )


@router.get("/audit", response_model=list[AuditEntry])
async def audit(request: Request, user: AdminUser, page: int = 1) -> list[AuditEntry]:
    return await audit_log.recent(request.app.state.rt.db.session_factory, page)
