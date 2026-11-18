import json
import os
from datetime import date, datetime, time, timedelta, timezone
from typing import Any

import anyio
from fastapi import FastAPI
from sqladmin import Admin, Flash, ModelView, action
from sqladmin.audit import AuditBackend, AuditEntry
from sqladmin.authentication import AuthenticationBackend, get_current_user_id
from sqlalchemy import func, select
from starlette.requests import Request
from starlette.responses import RedirectResponse
from wtforms import SelectField

from app.db import SessionLocal, engine
from app.login_limit import lock_email, normalize_email, record_failure, seconds_until_allowed
from app.models import AuditLog, AuthSession, Club, Event, Registration, Room, User
from app.security import SESSION_TTL, new_session_token, token_digest, verify_password

SECRET_KEY = os.environ.get("SECRET_KEY", "dev-only-not-a-secret")

TOMSK = timezone(timedelta(hours=7))
STATUSES = [
    ("pending", "на модерации"),
    ("published", "опубликовано"),
    ("rejected", "отклонено"),
    ("cancelled", "отменено"),
]
ROLES = [("student", "студент"), ("organizer", "организатор"), ("admin", "администратор")]



def _to_json(value: Any) -> Any:
    """Приводит значения из форм админки к тому, что умеет хранить jsonb."""
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if hasattr(value, "id"):  # связанная запись из формы (клуб, аудитория): храним её id
        return value.id
    return str(value)


class DatabaseAuditBackend(AuditBackend):
    """Пишет журнал действий админки в таблицу audit_log вместо лога в консоли."""

    def build_row(self, entry: AuditEntry, request: Request) -> AuditLog:
        return AuditLog(
            created_at=entry.timestamp,
            user_id=get_current_user_id(request),
            action=entry.action,
            identity=entry.identity,
            object_id=entry.pk,
            changes=json.loads(json.dumps(entry.changes, default=_to_json)) if entry.changes else None,
        )

    async def log(self, entry: AuditEntry, request: Request) -> None:
        # Сессия синхронная, поэтому пишем в потоке, чтобы не блокировать event loop админки
        await anyio.to_thread.run_sync(self._save, self.build_row(entry, request))

    @staticmethod
    def _save(row: AuditLog) -> None:
        with SessionLocal() as session:
            session.add(row)
            session.commit()


audit_backend = DatabaseAuditBackend()


class AdminAuth(AuthenticationBackend):
    async def login(self, request: Request) -> bool:
        form = await request.form()
        email = normalize_email(str(form.get("username", "")))
        password = str(form.get("password", ""))
        with SessionLocal() as session:
            # Тот же счётчик неудач, что и у POST /api/auth/login: иначе пароль можно было бы
            # перебирать через форму админки в обход ограничения
            lock_email(session, email)
            if seconds_until_allowed(session, email):
                return False
            user = session.scalars(select(User).where(func.lower(User.email) == email)).first()
            password_ok = verify_password(user.password_hash if user else None, password)
            if not password_ok or user is None or not user.is_active or user.role != "admin":
                record_failure(session, email)
                session.commit()
                return False
            token = new_session_token()
            session.add(
                AuthSession(
                    token_hash=token_digest(token),
                    user=user,
                    expires_at=datetime.now(timezone.utc) + SESSION_TTL,
                )
            )
            session.commit()
            request.session.update({"token": token, "user_id": user.id})
        return True

    async def logout(self, request: Request) -> bool:
        token = request.session.get("token", "")
        with SessionLocal() as session:
            auth = session.scalars(
                select(AuthSession).where(AuthSession.token_hash == token_digest(token))
            ).first()
            if auth is not None:
                session.delete(auth)
                session.commit()
        request.session.clear()
        return True

    async def authenticate(self, request: Request) -> bool:
        token = request.session.get("token")
        if not token:
            return False
        with SessionLocal() as session:
            auth = session.scalars(
                select(AuthSession).where(
                    AuthSession.token_hash == token_digest(token),
                    AuthSession.expires_at > datetime.now(timezone.utc),
                    )
            ).first()
            return auth is not None and auth.user.is_active and auth.user.role == "admin"


def _titles(titles: list[str], limit: int = 3) -> str:
    """Названия для сообщения администратору. Показываем не больше limit штук: сообщения хранятся
    в cookie-сессии админки, а cookie браузер не принимает больше ~4 КБ (при отклонении сразу
    десятков мероприятий список названий переполнил бы её)."""
    shown = ", ".join(f"«{t[:60]}»" for t in titles[:limit])
    return shown + (f" и ещё {len(titles) - limit}" if len(titles) > limit else "")


class EventAdmin(ModelView, model=Event):
    name = "Мероприятие"
    name_plural = "Мероприятия"
    icon = "fa-solid fa-calendar"
    column_list = [Event.id, Event.title, Event.club, Event.room, Event.starts_at, Event.status]
    column_searchable_list = [Event.title]
    column_sortable_list = [Event.id, Event.starts_at, Event.status]
    column_default_sort = [(Event.starts_at, False)]
    form_excluded_columns = [Event.created_at]
    form_overrides = {"status": SelectField}
    form_args = {
        "status": {"choices": STATUSES},
        "starts_at": {"display_timezone": TOMSK},
        "ends_at": {"display_timezone": TOMSK},
    }
    column_formatters = {
        Event.starts_at: lambda event, _: event.starts_at.astimezone(TOMSK).strftime("%d.%m.%Y %H:%M"),
    }
    column_labels = {
        Event.title: "Название",
        Event.club: "Клуб",
        Event.room: "Аудитория",
        Event.starts_at: "Начало",
        Event.ends_at: "Окончание",
        Event.status: "Статус",
        Event.reject_reason: "Причина отклонения",
    }

    async def on_model_change(self, data: dict, model: Any, is_created: bool, request: Request) -> None:
        # Правило «отклонить только с причиной» не должно обходиться через форму редактирования
        if data.get("status") == "rejected" and not (data.get("reject_reason") or "").strip():
            raise ValueError("Чтобы отклонить мероприятие, укажите причину отклонения")

    async def _moderate(self, request: Request, new_status: str, action_name: str) -> RedirectResponse:
        pks = [pk for pk in request.query_params.get("pks", "").split(",") if pk.isdigit()]
        done: list[str] = []
        no_reason: list[str] = []
        with SessionLocal() as session:
            for pk in pks:
                event = session.get(Event, int(pk))
                if event is None or event.status != "pending":
                    continue
                changes: dict[str, Any] = {"status": new_status}
                if new_status == "rejected":
                    reason = (event.reject_reason or "").strip()
                    if not reason:
                        no_reason.append(event.title)  # остаётся на модерации
                        continue
                    changes["reject_reason"] = reason
                event.status = new_status
                # Строка журнала в той же транзакции, что и смена статуса: либо оба изменения
                # сохранятся, либо ни одно
                session.add(
                    audit_backend.build_row(
                        AuditEntry(action=action_name, identity=self.identity, pk=pk, changes=changes),
                        request,
                    )
                )
                done.append(event.title)
            session.commit()

        if no_reason:
            Flash.error(
                request,
                f"Не указана причина отклонения: {_titles(no_reason)}. Заполните поле «Причина "
                "отклонения» в карточке мероприятия и повторите. Мероприятия остались на модерации.",
                "Не отклонено",
            )
        if done:
            verb = "Опубликовано" if new_status == "published" else "Отклонено"
            Flash.success(request, f"{verb}: {_titles(done)}")
        return RedirectResponse(request.url_for("admin:list", identity=self.identity), status_code=302)

    @action(name="publish", label="Опубликовать", confirmation_message="Опубликовать выбранные мероприятия?")
    async def publish(self, request: Request) -> RedirectResponse:
        return await self._moderate(request, "published", "publish")

    @action(name="reject", label="Отклонить", confirmation_message="Отклонить выбранные мероприятия?")
    async def reject(self, request: Request) -> RedirectResponse:
        return await self._moderate(request, "rejected", "reject")


class ClubAdmin(ModelView, model=Club):
    name = "Клуб"
    name_plural = "Клубы"
    icon = "fa-solid fa-users"
    column_list = [Club.id, Club.name, Club.slug, Club.organizers]
    column_searchable_list = [Club.name]


class RoomAdmin(ModelView, model=Room):
    name = "Аудитория"
    name_plural = "Аудитории"
    icon = "fa-solid fa-door-open"
    column_list = [Room.id, Room.name, Room.building, Room.capacity]


class UserAdmin(ModelView, model=User):
    name = "Пользователь"
    name_plural = "Пользователи"
    icon = "fa-solid fa-user"
    can_create = False
    can_delete = False
    column_list = [User.id, User.email, User.full_name, User.role, User.is_active]
    column_searchable_list = [User.email, User.full_name]
    column_details_exclude_list = [User.password_hash]
    form_columns = [User.full_name, User.role, User.is_active, User.clubs]
    form_overrides = {"role": SelectField}
    form_args = {"role": {"choices": ROLES}}


class RegistrationAdmin(ModelView, model=Registration):
    name = "Запись"
    name_plural = "Записи"
    icon = "fa-solid fa-ticket"
    can_create = False
    can_edit = False
    column_list = [Registration.id, Registration.event, Registration.user, Registration.status, Registration.created_at]


class AuditLogAdmin(ModelView, model=AuditLog):
    name = "Запись журнала"
    name_plural = "Журнал"
    icon = "fa-solid fa-clipboard-list"
    # Только чтение: журнал нельзя ни создавать, ни править, ни удалять руками
    can_create = False
    can_edit = False
    can_delete = False
    column_list = [AuditLog.created_at, AuditLog.user, AuditLog.action, AuditLog.identity, AuditLog.object_id]
    column_details_list = [
        AuditLog.id, AuditLog.created_at, AuditLog.user, AuditLog.action,
        AuditLog.identity, AuditLog.object_id, AuditLog.changes,
    ]
    column_sortable_list = [AuditLog.created_at, AuditLog.action, AuditLog.identity]
    column_default_sort = [(AuditLog.created_at, True)]  # True — по убыванию: новые сверху
    column_formatters = {
        AuditLog.created_at: lambda log, _: log.created_at.astimezone(TOMSK).strftime("%d.%m.%Y %H:%M:%S"),
    }
    column_formatters_detail = column_formatters
    column_labels = {
        AuditLog.created_at: "Время",
        AuditLog.user: "Администратор",
        AuditLog.action: "Действие",
        AuditLog.identity: "Раздел",
        AuditLog.object_id: "ID объекта",
        AuditLog.changes: "Изменения",
    }


def setup_admin(app: FastAPI) -> Admin:
    admin = Admin(
        app,
        engine,
        title="WEB-ОИС: администрирование",
        authentication_backend=AdminAuth(secret_key=SECRET_KEY),
        audit_backend=audit_backend,
    )
    for view in (EventAdmin, ClubAdmin, RoomAdmin, UserAdmin, RegistrationAdmin, AuditLogAdmin):
        admin.add_view(view)
    return admin
