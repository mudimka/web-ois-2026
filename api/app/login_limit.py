"""Ограничение неудачных попыток входа: не больше MAX_FAILED за WINDOW для одного email.

Попытки хранятся в таблице login_attempt, а не в памяти процесса, поэтому счётчик общий
для всех копий API и не сбрасывается при перезапуске. Время считает сама база (now()),
а не приложение: часы разных копий API могут расходиться.
"""
import math
from datetime import timedelta

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import LoginAttempt

MAX_FAILED = 5
WINDOW = timedelta(minutes=15)


def normalize_email(email: str) -> str:
    return email.strip().lower()


def lock_email(session: Session, email: str) -> None:
    """Блокировка на время транзакции: параллельные попытки для одного email идут по очереди.

    Без неё сто одновременных запросов успели бы все прочитать «неудач: 0», прежде чем
    хоть один запишет свою неудачу, и ограничение обошлось бы. Блокировка снимается сама
    при commit или rollback. Попытки для разных email друг другу не мешают."""
    session.execute(select(func.pg_advisory_xact_lock(func.hashtext(email))))


def seconds_until_allowed(session: Session, email: str) -> int:
    """0 — можно пробовать; иначе сколько секунд осталось ждать."""
    failed, oldest, now = session.execute(
        select(func.count(), func.min(LoginAttempt.created_at), func.now()).where(
            LoginAttempt.email == email,
            LoginAttempt.created_at > func.now() - WINDOW,
        )
    ).one()
    if failed < MAX_FAILED:
        return 0
    # Снова можно, когда самая старая неудача выйдет из окна в 15 минут
    return max(1, math.ceil((oldest + WINDOW - now).total_seconds()))


def record_failure(session: Session, email: str) -> None:
    session.add(LoginAttempt(email=email))


def minutes_word(n: int) -> str:
    if n % 10 == 1 and n % 100 != 11:
        return f"{n} минуту"
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return f"{n} минуты"
    return f"{n} минут"


def raise_too_many(seconds: int) -> None:
    minutes = math.ceil(seconds / 60)
    raise HTTPException(
        status.HTTP_429_TOO_MANY_REQUESTS,
        f"Слишком много попыток, попробуйте через {minutes_word(minutes)}",
        headers={"Retry-After": str(seconds)},
    )
