"""SQLAdmin: табличный доступ к БД на /dbadmin (просмотр и правка «сырых» данных).

Логин — учётка администратора приложения (та же, что в основном интерфейсе).
"""

from typing import TYPE_CHECKING

from sqladmin import Admin, ModelView
from sqladmin.authentication import AuthenticationBackend
from starlette.requests import Request

from app.config import get_settings
from app.models import (
    AnalyticsReport,
    AuditLog,
    ChatSession,
    Feedback,
    KBArticle,
    KBGap,
    Message,
    QuickReply,
    SettingEntry,
    User,
)
from app.security import create_token, decode_token
from app.services.credentials import authenticate

if TYPE_CHECKING:
    from fastapi import FastAPI

    from app.runtime import AppRuntime


class AdminAuth(AuthenticationBackend):
    def __init__(self, rt: AppRuntime) -> None:
        super().__init__(secret_key=get_settings().resolve_secret())
        self.rt = rt

    async def login(self, request: Request) -> bool:
        form = await request.form()
        username, password = str(form.get("username", "")), str(form.get("password", ""))
        user = await authenticate(self.rt.db.session_factory, username, password)
        if user is not None and user.role == "admin":
            request.session["token"] = create_token(user.id, user.username, user.role)
            return True
        return False

    async def logout(self, request: Request) -> bool:
        request.session.clear()
        return True

    async def authenticate(self, request: Request) -> bool:
        token = request.session.get("token", "")
        try:
            return decode_token(token).get("role") == "admin"
        except Exception:  # noqa: BLE001
            return False


def mount_dbadmin(app: FastAPI, rt: AppRuntime) -> None:
    admin = Admin(
        app,
        rt.db.engine,
        base_url="/dbadmin",
        authentication_backend=AdminAuth(rt),
        title="Валерчик · база данных",
    )

    class KBArticleAdmin(ModelView, model=KBArticle):
        form_excluded_columns = [KBArticle.source_version]
        column_list = [
            KBArticle.id,
            KBArticle.question,
            KBArticle.category,
            KBArticle.line,
            KBArticle.audience,
            KBArticle.status,
            KBArticle.source,
        ]
        column_searchable_list = [KBArticle.question, KBArticle.answer]
        name_plural = "База знаний"

    class SessionAdmin(ModelView, model=ChatSession):
        column_list = [
            ChatSession.id,
            ChatSession.status,
            ChatSession.line,
            ChatSession.operator_id,
            ChatSession.created_at,
        ]
        name_plural = "Диалоги"

    class MessageAdmin(ModelView, model=Message):
        column_list = [Message.id, Message.session_id, Message.role, Message.content, Message.created_at]
        column_searchable_list = [Message.content]
        name_plural = "Сообщения"

    class FeedbackAdmin(ModelView, model=Feedback):
        column_list = [
            Feedback.id,
            Feedback.rating,
            Feedback.comment,
            Feedback.specialist_kind,
            Feedback.created_at,
        ]
        name_plural = "Отзывы"

    class UserAdmin(ModelView, model=User):
        column_list = [User.id, User.username, User.role, User.display_name, User.line]
        column_details_exclude_list = [User.password_hash]
        name_plural = "Пользователи"

    class GapAdmin(ModelView, model=KBGap):
        column_list = [KBGap.query_example, KBGap.hits, KBGap.best_score, KBGap.status]
        name_plural = "Пробелы БЗ"

    class SettingAdmin(ModelView, model=SettingEntry):
        column_list = [SettingEntry.key, SettingEntry.value, SettingEntry.category]
        name_plural = "Настройки"

    class QuickReplyAdmin(ModelView, model=QuickReply):
        column_list = [QuickReply.title, QuickReply.audience, QuickReply.usage_count]
        name_plural = "Шаблоны"

    class ReportAdmin(ModelView, model=AnalyticsReport):
        column_list = [AnalyticsReport.kind, AnalyticsReport.created_at]
        name_plural = "Отчёты"

    class AuditAdmin(ModelView, model=AuditLog):
        column_list = [AuditLog.actor, AuditLog.action, AuditLog.entity, AuditLog.created_at]
        can_create = False
        can_edit = False
        can_delete = False
        name_plural = "Аудит"

    for view in (
        KBArticleAdmin,
        SessionAdmin,
        MessageAdmin,
        FeedbackAdmin,
        UserAdmin,
        GapAdmin,
        SettingAdmin,
        QuickReplyAdmin,
        ReportAdmin,
        AuditAdmin,
    ):
        admin.add_view(view)
