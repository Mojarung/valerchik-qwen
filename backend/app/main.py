"""Точка входа FastAPI: сборка приложения, middleware, роутеры, SPA, метрики, SQLAdmin."""

import hmac
import logging
import sys
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from app.chat.errors import ChatError
from app.config import get_settings
from app.console import use_utf8
from app.middleware import prometheus_middleware
from app.routers import (
    admin,
    auth,
    catalog,
    chat,
    guides,
    health,
    kb,
    operator,
    source_conflicts,
    support,
    voice,
)
from app.runtime import AppRuntime
from app.spa import mount_spa
from app.tasks.broker import broker
from app.tasks.jobs import set_runtime
from app.uploads import MAX_REQUEST_BYTES, BodyLimitMiddleware

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

log = logging.getLogger(__name__)


def create_app() -> FastAPI:
    settings = get_settings()
    # Секрет разрешаем до всего остального: в full пустой или дефолтный APP_SECRET
    # обязан уронить старт с понятным текстом, а не всплыть на первом же логине,
    # когда сервис уже принимает трафик и выдаёт подделываемые токены.
    settings.resolve_secret()
    # До настройки логов: иначе на Windows lite пишет их в cp1251.
    use_utf8(sys.stdout, sys.stderr)
    logging.basicConfig(
        level=settings.log_level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    rt = AppRuntime(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        await rt.start()
        set_runtime(rt)
        if not broker.is_worker_process:
            await broker.startup()
        log.info("Сервис запущен в режиме %s", settings.app_mode)
        yield
        if not broker.is_worker_process:
            await broker.shutdown()
        await rt.stop()

    app = FastAPI(
        title="Валерчик — ИИ-поддержка Портала поставщиков",
        version="1.0.0",
        lifespan=lifespan,
        # Swagger в боевом режиме — карта API для постороннего: только в lite или
        # по явному API_DOCS=true. ReDoc не нужен ни там, ни там.
        docs_url="/api/docs" if settings.docs_enabled else None,
        openapi_url="/api/openapi.json" if settings.docs_enabled else None,
        redoc_url=None,
    )
    app.state.rt = rt

    # Потолок тела запроса. Добавлен первым, значит работает внутри CORS и
    # счётчиков: отказ 413 уходит с CORS-заголовками и попадает в метрики.
    app.add_middleware(BodyLimitMiddleware, max_bytes=MAX_REQUEST_BYTES)

    # Список закрыт: с `*` любой сайт читал бы ответы API от имени посетителя.
    # Пусто — значит только свой origin (фронт и API живут за одним nginx).
    # Расширению origin заранее не известен: он свой у каждой установки, поэтому
    # для него regex по схеме. Credentials не нужны — кука работает same-origin.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.allowed_origins_list,
        allow_origin_regex=r"^(chrome|moz)-extension://.*$",
        allow_credentials=False,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-Requested-With", "X-Session-Owner"],
    )
    app.middleware("http")(prometheus_middleware)

    @app.exception_handler(ChatError)
    async def chat_error_handler(request: Request, exc: ChatError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})

    for module in (
        auth,
        chat,
        operator,
        kb,
        admin,
        voice,
        health,
        catalog,
        guides,
        source_conflicts,
        support,
    ):
        app.include_router(module.router)
    app.include_router(guides.admin_router)
    app.include_router(chat.ws_router)
    app.include_router(operator.ws_router)

    @app.get("/metrics", include_in_schema=False)
    async def metrics(request: Request) -> Response:
        """Метрики Prometheus. В боевом режиме — только с токеном: счётчики по
        путям и кодам ответа — карта API для постороннего."""
        cfg = get_settings()
        if cfg.metrics_token:
            given = request.headers.get("authorization", "")
            if not hmac.compare_digest(given.encode(), f"Bearer {cfg.metrics_token}".encode()):
                raise HTTPException(401, "Нужен токен метрик", headers={"WWW-Authenticate": "Bearer"})
        elif not cfg.is_lite:
            raise HTTPException(404)
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

    _mount_dbadmin(app, rt)
    mount_spa(app)
    return app


def _mount_dbadmin(app: FastAPI, rt: AppRuntime) -> None:
    """SQLAdmin на /dbadmin — быстрый просмотр «что сейчас в базе» (логин администратора)."""
    try:
        from app.dbadmin import mount_dbadmin

        mount_dbadmin(app, rt)
    except Exception:
        log.exception("SQLAdmin не подключён")


app = create_app()
