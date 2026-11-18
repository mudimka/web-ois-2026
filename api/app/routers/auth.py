from datetime import datetime, timezone

from fastapi import APIRouter, status, HTTPException, Response, Request
from sqlalchemy import select, func, delete
from sqlalchemy.exc import IntegrityError

from app.auth import CurrentUser
from app.login_limit import (
    lock_email,
    normalize_email,
    raise_too_many,
    record_failure,
    seconds_until_allowed,
)
from app.schemas import ErrorResponse, UserRead, RegisterRequest, LoginRequest, PasswordChange
from app.db import SessionDep, violated_constraint
from app.models import User, AuthSession
from app.security import (
    hash_password,
    verify_password,
    new_session_token,
    token_digest,
    SESSION_TTL, SESSION_COOKIE, COOKIE_SECURE
)

router = APIRouter(prefix="/api/auth", tags=["Вход"])

UNAUTHORIZED = {401: {"model": ErrorResponse, "description": "Не выполнен вход"}}

@router.post(
    "/resister",
    response_model=UserRead,
    status_code=status.HTTP_201_CREATED,
    responses={409: {"model": ErrorResponse, "description": "Email уже зарегистрирован"}},
    summary="Регистрация студента",
)
def register(payload: RegisterRequest, session: SessionDep):
    user = User(
        email=payload.email,
        full_name=payload.full_name,
        password_hash=hash_password(payload.password),
    )
    session.add(user)
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        if violated_constraint(exc) == "users_email_key":
            raise HTTPException(status.HTTP_409_CONFLICT, "Этот email уже зарегистрирован") from exc
        raise
    return user

@router.post(
    "/login",
    response_model=UserRead,
    status_code=status.HTTP_201_CREATED,
    responses={
        401: {"model": ErrorResponse, "description": "Неверный логин или пароль"},
        429: {
            "model": ErrorResponse,
            "description": "Больше 5 неудачных попыток за 15 минут для этого email, "
            "даже если пароль верный",
        },
    },
    summary="Вход",
)
def login(payload: LoginRequest, response: Response, session: SessionDep):
    """Вход по email и паролю. После 5 неудачных попыток за 15 минут вход для этого email
    блокируется на оставшееся время: ответ 429 приходит и при верном пароле, поэтому по
    ответу нельзя узнать, угадан пароль или нет."""
    email = normalize_email(payload.email)
    lock_email(session, email)

    wait = seconds_until_allowed(session, email)
    if wait:
        raise_too_many(wait)  # пароль не проверяем вообще

    user = session.scalars(select(User).where(func.lower(User.email) == email)).first()
    # Пароль проверяется всегда, даже для несуществующего email (на «пустышке»): иначе по времени
    # ответа можно было бы отличить неизвестный email от известного
    password_ok = verify_password(user.password_hash if user else None, payload.password)
    if not password_ok or user is None or not user.is_active:
        record_failure(session, email)
        session.commit()  # без commit запись о неудаче пропала бы вместе с ответом 401
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Неверный email или пароль")

    token = new_session_token()
    session.add(
        AuthSession(
            token_hash=token_digest(token),
            user=user,
            expires_at=datetime.now(timezone.utc) + SESSION_TTL,
        )
    )
    session.commit()

    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=int(SESSION_TTL.total_seconds()),
        httponly=True,
        secure=COOKIE_SECURE,
        samesite="lax"
    )

    return user

@router.post(
    "/loguot",
    status_code=status.HTTP_204_NO_CONTENT,
    responses=UNAUTHORIZED,
    summary="Выход"
)
def logout(request: Request, response: Response, user: CurrentUser, session: SessionDep):
    token = request.cookies.get(SESSION_COOKIE, "")
    session.execute(delete(AuthSession).where(AuthSession.token_hash == token_digest(token)))
    session.commit()
    response.delete_cookie(SESSION_COOKIE, httponly=True, secure=COOKIE_SECURE, samesite="lax")

@router.get("/me", response_model=UserRead, responses=UNAUTHORIZED, summary="Кто я")
def me(user: CurrentUser):
    return user


@router.post(
    "/password",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={
        **UNAUTHORIZED,
        400: {"model": ErrorResponse, "description": "Старый пароль неверен"},
    },
    summary="Сменить пароль",
)
def change_password(payload: PasswordChange, request: Request, user: CurrentUser, session: SessionDep):
    """Меняет пароль и завершает все остальные сессии пользователя (в других браузерах и
    на других устройствах, в том числе вход в админку). Текущая сессия продолжает работать.
    Новый пароль не короче 15 символов (иначе 422)."""
    if not verify_password(user.password_hash, payload.old_password):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Старый пароль неверен")

    current = token_digest(request.cookies.get(SESSION_COOKIE, ""))
    user.password_hash = hash_password(payload.new_password)
    session.execute(
        delete(AuthSession).where(AuthSession.user_id == user.id, AuthSession.token_hash != current)
    )
    session.commit()  # смена пароля и удаление сессий — одной транзакцией
