"""JWT-аутентификация и роли. Пароли — PBKDF2 (stdlib, без внешних зависимостей)."""

import hashlib
import hmac
import secrets
import time
from collections.abc import Callable
from typing import Annotated, Any
from urllib.parse import urlsplit

import jwt
from fastapi import Depends, HTTPException, Request, Response, WebSocket, status

from app.config import get_settings

_ALGO = "HS256"
_LEGACY_ITERS = 260_000  # формат «pbkdf2$соль$дайджест», итерации в нём не записаны
_HASH_TAG = "pbkdf2_sha256"
TOKEN_TTL_SECONDS = 12 * 3600

# Токен сотрудника живёт в httpOnly-куке: из JS его не достать, поэтому XSS на
# портале не уносит сессию оператора. Bearer остаётся для неинтерактивных
# клиентов (mcp-серверы, curl, тесты), а вот query-параметр убран совсем —
# он утекает в логи прокси, в Referer и в историю браузера.
AUTH_COOKIE = "valerchik_auth"
# Кука ходит сама, поэтому изменяющий запрос обязан доказать, что его послал наш
# фронт: заголовок нельзя выставить простой формой с чужого сайта, а на
# нестандартный заголовок браузер сначала спросит preflight, который CORS не пустит.
CSRF_HEADER = "x-requested-with"
CSRF_VALUE = "valerchik"
_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def _pbkdf2(password: str, salt: str, iterations: int) -> str:
    return hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), iterations).hex()


def hash_password(password: str) -> str:
    """Хеш со всем, что нужно для проверки: алгоритм, число итераций, соль.

    Число итераций записано в самом хеше, поэтому его можно поднять в любой
    момент: старые хеши остаются проверяемыми и перехешируются при входе.
    """
    iterations = get_settings().password_hash_iterations
    salt = secrets.token_hex(16)
    return f"{_HASH_TAG}${iterations}${salt}${_pbkdf2(password, salt, iterations)}"


# Хеш — «тег$итерации$соль$дайджест»; у старых, заведённых до числа итераций
# в самом хеше, — «pbkdf2$соль$дайджест»
_HASH_PARTS = 4
_LEGACY_PARTS = 3


def _parse_hash(stored: str) -> tuple[int, str, str] | None:
    """(итерации, соль, дайджест) из сохранённого хеша или None, если хеш не наш."""
    parts = stored.split("$")
    if len(parts) == _LEGACY_PARTS and parts[0] == "pbkdf2":
        return _LEGACY_ITERS, parts[1], parts[2]
    if len(parts) != _HASH_PARTS or parts[0] != _HASH_TAG or not (parts[1].isascii() and parts[1].isdigit()):
        return None
    iterations = int(parts[1])
    return (iterations, parts[2], parts[3]) if iterations > 0 else None


def verify_password(password: str, stored: str) -> bool:
    parsed = _parse_hash(stored)
    if parsed is None:
        return False
    iterations, salt, digest = parsed
    return hmac.compare_digest(_pbkdf2(password, salt, iterations), digest)


def needs_rehash(stored: str) -> bool:
    """Хеш в прежнем формате или слабее текущей настройки: перехешировать при входе."""
    parsed = _parse_hash(stored)
    if parsed is None or not stored.startswith(f"{_HASH_TAG}$"):
        return True
    return parsed[0] < get_settings().password_hash_iterations


#: Подпротокол гостевого сокета. Токен владельца идёт вторым подпротоколом,
#: `owner.<токен>`: браузерный WebSocket не умеет своих заголовков, а адрес с
#: токеном оседал в логах прокси и в истории браузера.
CHAT_WS_PROTOCOL = "valerchik.v1"
_OWNER_PROTOCOL = "owner."


def owner_from_subprotocols(subprotocols: list[str]) -> str | None:
    """Токен владельца из предложенных клиентом подпротоколов или None."""
    for protocol in subprotocols:
        if protocol.startswith(_OWNER_PROTOCOL):
            return protocol.removeprefix(_OWNER_PROTOCOL)
    return None


def session_owner_token(session_id: str) -> str:
    """Доказательство владения диалогом для анонимного посетителя.

    Учётных записей у чата нет, а «кто знает id — тот и владелец» слишком
    щедро: id уезжает в выгрузку диалогов, в /dbadmin и в куку на общем
    компьютере, после чего посторонний мог писать реплики от имени заявителя
    прямо в живой диалог с оператором.

    Подпись, а не запись в БД: проверяется без похода в базу на каждом кадре
    websocket, и её нельзя подделать, не зная APP_SECRET.
    """
    digest = hmac.new(get_settings().resolve_secret().encode(), session_id.encode(), "sha256")
    return digest.hexdigest()[:32]


def is_session_owner(session_id: str, token: str | None) -> bool:
    if not token:
        return False
    return hmac.compare_digest(session_owner_token(session_id), token)


def create_token(user_id: str, username: str, role: str) -> str:
    now = int(time.time())
    payload = {"sub": user_id, "username": username, "role": role, "iat": now, "exp": now + TOKEN_TTL_SECONDS}
    return jwt.encode(payload, get_settings().resolve_secret(), algorithm=_ALGO)


def decode_token(token: str) -> dict[str, Any]:
    return jwt.decode(token, get_settings().resolve_secret(), algorithms=[_ALGO])


class AuthUser:
    def __init__(self, user_id: str, username: str, role: str) -> None:
        self.id = user_id
        self.username = username
        self.role = role


def cookie_is_secure(request: Request) -> bool:
    """Ставить ли флаг Secure.

    В боевом режиме — всегда: full по определению живёт за TLS, и свойство
    безопасности куки не должно зависеть от настроек прокси. В остальных случаях
    решает схема запроса: за доверенным прокси её выставляет uvicorn по
    X-Forwarded-Proto (FORWARDED_ALLOW_IPS).
    """
    return not get_settings().is_lite or request.url.scheme == "https"


def set_auth_cookie(response: Response, token: str, *, secure: bool) -> None:
    response.set_cookie(
        AUTH_COOKIE,
        token,
        max_age=TOKEN_TTL_SECONDS,
        httponly=True,
        samesite="lax",
        secure=secure,
        path="/",
    )


def clear_auth_cookie(response: Response) -> None:
    response.delete_cookie(AUTH_COOKIE, path="/")


def token_from_request(request: Request) -> tuple[str | None, str]:
    """-> (токен, источник: "bearer" | "cookie" | ""). Источник решает, нужен ли CSRF-заголовок."""
    header = request.headers.get("authorization", "")
    if header.lower().startswith("bearer "):
        return header[7:].strip() or None, "bearer"
    cookie = request.cookies.get(AUTH_COOKIE)
    if cookie:
        return cookie, "cookie"
    return None, ""


def client_ip(connection: Request | WebSocket) -> str:
    """Адрес клиента для лимитов.

    X-Forwarded-For здесь не читается: левый адрес в нём пишет сам клиент, а
    nginx лишь дописывает настоящий справа — брать первый значило дать клиенту
    выбирать себе ведро лимита. Цепочку разбирает uvicorn: справа налево,
    пропуская доверенные прокси (FORWARDED_ALLOW_IPS — подсеть compose), и
    кладёт итог в client.
    """
    return connection.client.host if connection.client else "unknown"


def origin_allowed(origin: str | None, host: str, allowed: list[str]) -> bool:
    """Свой ли origin у соединения с кукой.

    Origin шлёт только браузер, поэтому его отсутствие — это не-браузерный
    клиент (curl, mcp-сервер), которому CSRF не грозит: подделать запрос чужой
    вкладкой там нечем. Пустой список `allowed` означает «только свой хост».
    """
    if not origin:
        return True
    normalized = origin.rstrip("/")
    if normalized in allowed:
        return True
    netloc = urlsplit(normalized).netloc
    return bool(netloc) and bool(host) and netloc == host


def get_current_user(request: Request) -> AuthUser:
    token, source = token_from_request(request)
    if not token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Не авторизован")
    if source == "cookie" and request.method.upper() not in _SAFE_METHODS:
        if request.headers.get(CSRF_HEADER, "").strip().lower() != CSRF_VALUE:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Запрос без подтверждения происхождения")
    try:
        payload = decode_token(token)
    except jwt.PyJWTError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Недействительный токен") from exc
    return AuthUser(payload["sub"], payload["username"], payload["role"])


def require_role(*roles: str) -> Callable[[AuthUser], AuthUser]:
    def checker(user: Annotated[AuthUser, Depends(get_current_user)]) -> AuthUser:
        if user.role not in roles:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Недостаточно прав")
        return user

    return checker


CurrentUser = Annotated[AuthUser, Depends(get_current_user)]
AdminUser = Annotated[AuthUser, Depends(require_role("admin"))]
StaffUser = Annotated[AuthUser, Depends(require_role("admin", "operator"))]
