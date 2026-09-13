from fastapi import APIRouter, HTTPException, Request, Response, status

from app.middleware import check_rate_limit
from app.schemas import LoginRequest, TokenResponse
from app.schemas.auth import MeResponse
from app.security import (
    clear_auth_cookie,
    cookie_is_secure,
    create_token,
    get_current_user,
    set_auth_cookie,
)
from app.services.credentials import authenticate, staff_profile

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/login", response_model=TokenResponse)
async def login(request: Request, response: Response, body: LoginRequest) -> TokenResponse:
    # Вход — самая дешёвая точка для подбора пароля: PBKDF2 стоит дорого нам, а
    # злоумышленнику ничего. Лимит на адрес превращает перебор в бессмысленный.
    await check_rate_limit(request, scope="login")
    user = await authenticate(request.app.state.rt.db.session_factory, body.username, body.password)
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Неверный логин или пароль")
    token = create_token(user.id, user.username, user.role)
    # Кука — для браузера, тело с токеном — для клиентов без куки (mcp, curl, тесты).
    set_auth_cookie(response, token, secure=cookie_is_secure(request))
    return TokenResponse(
        token=token,
        role=user.role,
        display_name=user.display_name,
        user_id=user.id,
    )


@router.get("/me", response_model=MeResponse)
async def me(request: Request) -> MeResponse:
    """Текущая сессия сотрудника. Аноним получает пустую роль, а не 401."""
    try:
        user = get_current_user(request)
    except HTTPException:
        return MeResponse()
    profile = await staff_profile(request.app.state.rt.db.session_factory, user.id)
    return MeResponse(
        user_id=user.id,
        username=user.username,
        role=user.role,
        display_name=(profile.display_name if profile else None) or user.username,
        line=profile.line if profile else None,
    )


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout() -> Response:
    """Выход не требует живого токена: протухшую куку тоже надо погасить, иначе
    она лежит в браузере до истечения Max-Age и фронт не может её убрать."""
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    clear_auth_cookie(response)
    return response
