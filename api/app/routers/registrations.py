from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from app.db import SessionDep
from app.models import Event, Registration
from app.schemas import ErrorResponse

router = APIRouter(prefix="/api/events/{event_id}/registrations", tags=["Записи"])


@router.delete(
    "/{user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={
        404: {"model": ErrorResponse, "description": "Мероприятия или активной записи нет"},
        409: {"model": ErrorResponse, "description": "Мероприятие уже началось (BR-5)"},
    },
    summary="Отменить запись",
)
def cancel_registration(event_id: int, user_id: int, session: SessionDep):
    """Отменяет запись студента на мероприятие (BR-5): место освобождается сразу.

    Запись не удаляется, а получает статус `cancelled`, поэтому история сохраняется,
    а студент может записаться снова. Отменить можно только до начала мероприятия.

    Путь временный: `user_id` в адресе нужен, пока в системе нет входа. Когда он появится,
    адрес станет `/api/events/{event_id}/registrations/me`.
    """
    event = session.get(Event, event_id)
    if event is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Мероприятие не найдено")

    # FOR UPDATE: если одну запись отменяют двумя запросами сразу, второй дождётся первого,
    # увидит, что активной записи уже нет, и получит 404, а не второй «успешный» ответ.
    registration = session.scalars(
        select(Registration)
        .where(
            Registration.event_id == event_id,
            Registration.user_id == user_id,
            Registration.status == "active",
        )
        .with_for_update()
    ).first()
    if registration is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Активной записи на это мероприятие нет")

    if event.starts_at <= datetime.now(timezone.utc):
        raise HTTPException(
            status.HTTP_409_CONFLICT, "Мероприятие уже началось, отменить запись нельзя"
        )

    registration.status = "cancelled"
    session.commit()
