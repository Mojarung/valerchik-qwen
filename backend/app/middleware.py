"""Rate limiting и метрики. Лимиты читаются из динамических настроек — меняются на лету."""

import logging
import time
from collections.abc import Awaitable, Callable

from fastapi import HTTPException, Request, Response, WebSocket, status
from prometheus_client import Counter, Histogram

from app.security import client_ip

log = logging.getLogger(__name__)

REQUESTS = Counter("app_requests_total", "HTTP-запросы", ["method", "path", "status"])
LATENCY = Histogram("app_request_seconds", "Длительность запросов", ["path"])
CHAT_MESSAGES = Counter("chat_messages_total", "Сообщения в чатах", ["outcome"])

# У входа лимит свой: перебор пароля и поток вопросов в чате — разные истории,
# и резать их одним числом значит либо пустить подбор, либо мешать разговору.
_LIMIT_SETTING = {"login": "rate_limit.login_per_minute"}
_DENIED_TEXT = {
    "login": "Слишком много попыток входа. Лимит {limit} в минуту — попробуйте через минуту.",
}
_DEFAULT_DENIED = "Слишком много сообщений. Лимит {limit} в минуту — попробуйте чуть позже."


def rate_limit_key(connection: Request | WebSocket, scope: str) -> str:
    """Ключ окна лимита. Вынесен, чтобы тесты строили его тем же способом, что и код."""
    return f"rl:{scope}:{client_ip(connection)}:{int(time.time() // 60)}"


async def rate_limit_exceeded(connection: Request | WebSocket, scope: str = "chat") -> str | None:
    """Скользящее окно на минуту через INCR+EXPIRE (Redis/fakeredis).

    -> текст отказа, если лимит исчерпан, иначе None.

    Принимает и Request, и WebSocket: у обоих есть `app` и `client`, а лимит
    обязан быть общим. Иначе достаточно перейти с REST на сокет, чтобы
    ограничение перестало действовать — а сообщение стоит одинаково в обоих
    случаях, включая работу guardrails и вызов LLM.
    """
    rt = connection.app.state.rt
    if not rt.dyn.get("rate_limit.enabled"):
        return None
    limit = int(rt.dyn.get(_LIMIT_SETTING.get(scope, "rate_limit.chat_per_minute")))
    key = rate_limit_key(connection, scope)
    try:
        current = await rt.cache.incr(key)
        if current == 1:
            await rt.cache.expire(key, 65)
    except Exception:
        log.exception("Rate limit backend недоступен")
        return None
    if current > limit:
        return _DENIED_TEXT.get(scope, _DEFAULT_DENIED).format(limit=limit)
    return None


async def check_rate_limit(request: Request, scope: str = "chat") -> None:
    """Тот же лимит для REST-обработчиков: превышение — это 429."""
    denied = await rate_limit_exceeded(request, scope)
    if denied:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, denied)


async def prometheus_middleware(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    started = time.perf_counter()
    response = await call_next(request)
    path = request.url.path
    if path.startswith("/api"):
        route = request.scope.get("route")
        template = getattr(route, "path", path)
        REQUESTS.labels(request.method, template, str(response.status_code)).inc()
        LATENCY.labels(template).observe(time.perf_counter() - started)
    return response
