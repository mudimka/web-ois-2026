from fastapi import APIRouter, HTTPException, status

from app import storage
from app.schemas import Club, ClubCreate, ClubUpdate, ErrorResponse, Room

router = APIRouter(prefix="/api", tags=["Справочники"])

NOT_FOUND = {404: {"model": ErrorResponse, "description": "Клуб не найден"}}
NAME_TAKEN = {409: {"model": ErrorResponse, "description": "Клуб с таким названием уже есть"}}


def get_club_or_404(club_id: int) -> dict:
    club = storage.CLUBS.get(club_id)
    if club is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Клуб не найден")
    return club


def check_name_is_free(name: str, club_id: int | None = None) -> None:
    """Название уникально без учёта регистра. При переименовании сам клуб не считается."""
    wanted = name.casefold()
    for club in storage.CLUBS.values():
        if club["id"] != club_id and club["name"].casefold() == wanted:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                f"Клуб с названием «{club['name']}» уже есть",
            )


@router.get("/clubs", response_model=list[Club], summary="Клубы")
def list_clubs():
    return list(storage.CLUBS.values())


@router.get(
    "/clubs/{club_id}",
    response_model=Club,
    responses=NOT_FOUND,
    summary="Один клуб",
)
def get_club(club_id: int):
    return get_club_or_404(club_id)


@router.post(
    "/clubs",
    response_model=Club,
    status_code=status.HTTP_201_CREATED,
    responses=NAME_TAKEN,
    summary="Создать клуб",
)
def create_club(payload: ClubCreate):
    """Создаёт клуб. Название должно быть уникальным без учёта регистра:
    «IT-клуб» и «it-клуб» считаются одним и тем же названием."""
    check_name_is_free(payload.name)
    club = {"id": storage.next_club_id(), "name": payload.name}
    storage.CLUBS[club["id"]] = club
    return club


@router.patch(
    "/clubs/{club_id}",
    response_model=Club,
    responses={**NOT_FOUND, **NAME_TAKEN},
    summary="Переименовать клуб",
)
def rename_club(club_id: int, payload: ClubUpdate):
    """Меняет название клуба. Новое название не должно совпадать (без учёта регистра)
    с названием другого клуба. Изменить только регистр букв своего названия можно."""
    club = get_club_or_404(club_id)
    check_name_is_free(payload.name, club_id=club_id)
    club["name"] = payload.name
    return club


@router.delete(
    "/clubs/{club_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={
        **NOT_FOUND,
        409: {"model": ErrorResponse, "description": "У клуба есть мероприятия, удалить нельзя"},
    },
    summary="Удалить клуб",
)
def delete_club(club_id: int):
    """Удаляет клуб. Если у клуба есть мероприятия, возвращает 409 и количество мероприятий."""
    club = get_club_or_404(club_id)
    events_count = sum(1 for e in storage.EVENTS.values() if e["club_id"] == club_id)
    if events_count:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Нельзя удалить клуб «{club['name']}»: у него есть мероприятия ({events_count})",
        )
    del storage.CLUBS[club_id]


@router.get("/rooms", response_model=list[Room], summary="Аудитории")
def list_rooms():
    return list(storage.ROOMS.values())
