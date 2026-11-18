from fastapi import APIRouter
from sqlalchemy import select

from app.auth import CurrentUser
from app.db import SessionDep
from app.models import Event, Registration, Room
from app.schemas import ErrorResponse, MyRegistration

router = APIRouter(prefix="/api/me", tags=["Записи"])


@router.get(
    "/registrations",
    response_model=list[MyRegistration],
    responses={401: {"model": ErrorResponse, "description": "Не выполнен вход"}},
    summary="Мои записи",
)
def my_registrations(user: CurrentUser, session: SessionDep):
    """Активные записи текущего пользователя по возрастанию времени начала мероприятия.

    Запись, мероприятие и аудитория берутся одним запросом с соединениями (JOIN), поэтому число
    запросов к базе не зависит от количества записей."""
    rows = session.execute(
        select(Event, Room)
        .join(Registration, Registration.event_id == Event.id)
        .join(Room, Room.id == Event.room_id)
        .where(Registration.user_id == user.id, Registration.status == "active")
        .order_by(Event.starts_at, Event.id)
    ).all()
    return [
        {"event_id": event.id, "title": event.title, "starts_at": event.starts_at, "room": room}
        for event, room in rows
    ]
