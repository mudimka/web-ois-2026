from fastapi import FastAPI

from app.routers import catalog, events, registrations, system

TAGS = [
    {"name": "Мероприятия", "description": "Афиша: создание, просмотр, изменение и удаление."},
    {"name": "Записи", "description": "Запись студентов на мероприятия и её отмена."},
    {"name": "Справочники", "description": "Клубы и аудитории."},
    {"name": "Служебное", "description": "Проверка, что сервис жив."},
]

app = FastAPI(
    title="WEB-ОИС API",
    description="API студенческой афиши мероприятий. Бизнес-правила BR-1…BR-10 — в docs/tz.md.",
    version="0.2.0",
    openapi_tags=TAGS,
)

app.include_router(system.router)
app.include_router(catalog.router)
app.include_router(events.router)
app.include_router(registrations.router)
