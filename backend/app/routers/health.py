"""Здоровье сервиса и метаданные для фронтенда."""

import logging

from fastapi import APIRouter, Request

from app.config import get_settings
from app.schemas.health import (
    AudienceOption,
    FeedbackPrompt,
    HealthChecks,
    HealthResponse,
    LiveVoice,
    MetaResponse,
    QueueThresholds,
)
from app.services import audience
from app.uploads import MAX_DOCUMENT_BYTES

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["health"])


@router.get("/health", response_model=HealthResponse)
async def health(request: Request) -> HealthResponse:
    rt = request.app.state.rt
    db_ok = cache_ok = True
    try:
        await rt.db.ping()
    except Exception:
        log.exception("health: БД недоступна")
        db_ok = False
    try:
        await rt.cache.ping()
    except Exception:  # noqa: BLE001
        cache_ok = False
    # Статьи в индексе сверяют с активными статьями базы, точки — просто для
    # сведения: у статьи из выгрузки их по одной на формулировку вопроса
    try:
        vector_index = str(await rt.store.count())
        vector_points = str(await rt.store.points())
    except Exception:  # noqa: BLE001
        vector_index = vector_points = "fail"
    return HealthResponse(
        status="ok" if db_ok else "degraded",
        mode=get_settings().app_mode,
        checks=HealthChecks(
            db="ok" if db_ok else "fail",
            cache="ok" if cache_ok else "fail",
            vector_index=vector_index,
            vector_points=vector_points,
        ),
    )


@router.get("/meta", response_model=MetaResponse)
async def meta(request: Request) -> MetaResponse:
    rt = request.app.state.rt
    cfg = get_settings()
    voice_on = bool(cfg.stt_url) and bool(rt.dyn.get("voice.enabled"))
    return MetaResponse(
        app="Валерчик — ИИ-поддержка Портала поставщиков",
        mode=cfg.app_mode,
        # Демо-доступы на странице входа — только пока сидер заводит демо-пароли:
        # в боевом режиме такая подсказка ведёт в тупик и раскрывает логины.
        demo=cfg.demo_credentials,
        # Предел документа для импорта: интерфейс берёт его отсюда, а не держит
        # свою копию — надпись «до 40 МБ» пережила подъём предела до 128.
        max_document_mb=MAX_DOCUMENT_BYTES // 1024 // 1024,
        voice_available=voice_on,
        # Озвучка ответов: нужен и поднятый профиль voice, и разрешение админа.
        # Фронт спрашивает заранее, чтобы не показывать кнопку, которая ответит 503.
        tts_available=bool(cfg.tts_url) and bool(rt.dyn.get("voice.tts_enabled")),
        live_voice=LiveVoice(
            enabled=voice_on and bool(rt.dyn.get("voice.live_enabled")),
            silence_ms=rt.dyn.get("voice.live_silence_ms"),
        ),
        # Поднят ли профиль docs: от этого зависит, приедут ли из PDF иллюстрации.
        # Админке нужно сказать это до загрузки, а не после часа разбора.
        docs_parsing=bool(cfg.docparse_url),
        llm_configured=bool(rt.dyn.get("llm.base_url") and rt.dyn.get("llm.model")),
        lines=rt.dyn.get("routing.lines"),
        # Переключатель «поставщик / заказчик»: подписи и пояснения приходят с
        # сервера, потому что он же трактует эти значения в поиске и промпте
        audiences=[AudienceOption(**option) for option in audience.options()],
        # Через сколько тишины чат сам предложит оценить консультацию и какими
        # словами — оба значения крутятся в админке без пересборки фронта.
        feedback=FeedbackPrompt(
            idle_prompt_seconds=rt.dyn.get("feedback.idle_prompt_seconds"),
            request_message=rt.dyn.get("feedback.request_message"),
        ),
        # Пороги подсветки времени ожидания в очереди оператора. Здесь же, где
        # остальная настраиваемая обвязка интерфейса: значения не секретные —
        # это лишь то, через сколько минут заявка в очереди краснеет.
        operator_queue=QueueThresholds(
            warn_seconds=rt.dyn.get("operator.queue_warn_seconds"),
            alert_seconds=rt.dyn.get("operator.queue_alert_seconds"),
        ),
    )
