"""Pydantic-схемы запросов/ответов (валидация на границе системы)."""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=128)


class TokenResponse(BaseModel):
    token: str
    role: str
    display_name: str
    user_id: str


class PageContext(BaseModel):
    """Раздел портала, с которого задан вопрос (виджет во фрейме).

    Пределы здесь щедрые и нужны лишь чтобы отсечь абсурдный объём: настоящая
    чистка и обрезка — в services/pagectx.py, на границе доверия.
    """

    path: str = Field(default="", max_length=400)
    title: str = Field(default="", max_length=300)


AUDIENCE_PATTERN = "^(all|supplier|customer)$"


class ChatMessageRequest(BaseModel):
    request_id: str | None = Field(default=None, min_length=1, max_length=128)
    text: str = Field(min_length=1, max_length=4000)
    page: PageContext | None = None
    # Положение переключателя «поставщик / заказчик» в момент отправки. Едет с
    # каждым сообщением, как и раздел портала: человек может передумать посреди
    # разговора, и последнее слово за ним, а не за сохранённым у диалога.
    audience: str | None = Field(default=None, pattern=AUDIENCE_PATTERN)


class SessionCreateRequest(BaseModel):
    audience: str | None = Field(default=None, pattern=AUDIENCE_PATTERN)


class AudienceRequest(BaseModel):
    audience: str = Field(pattern=AUDIENCE_PATTERN)


class SpeakRequest(BaseModel):
    text: str = Field(min_length=1, max_length=4000)


# Пятибалльная шкала оценки диалога — одна на форму, чат и отчёт по отзывам
RATING_MIN = 1
RATING_MAX = 5


class FeedbackRequest(BaseModel):
    """Отзыв после обращения: общая оценка и два быстрых вопроса.

    `solved` — «Проблема решена?», `clear` — «Понятно объяснили?». Необязательны:
    старый клиент их не присылает, а человек вправе ответить только на звёзды.
    """

    rating: int = Field(ge=RATING_MIN, le=RATING_MAX)
    comment: str = Field(default="", max_length=2000)
    solved: bool | None = None
    clear: bool | None = None


class MessageRatingRequest(BaseModel):
    """Оценка одного ответа: 1 — помог, -1 — не помог."""

    value: int = Field(ge=-1, le=1)


class ArticleImage(BaseModel):
    """Иллюстрация статьи при импорте: ссылка обязательна, остальное — подпись."""

    url: str = Field(min_length=1, max_length=2000)
    caption: str = Field(default="", max_length=500)
    page: int | None = None
    # Импорт пишет сюда ключ «рис:26» (тип и номер), редактор присылает его
    # обратно как есть. Число из старых выгрузок тоже принимаем.
    figure: str | int | None = None


# Короче пяти символов текст ничего не объясняет — тогда нужна хотя бы картинка
MIN_ANSWER_CHARS = 5


class ArticleIn(BaseModel):
    question: str = Field(min_length=5, max_length=1000)
    # Пустой ответ допустим ровно в одном случае — когда ответ это картинка;
    # проверку делает валидатор ниже, поэтому min_length здесь нет.
    answer: str = Field(default="", max_length=8000)
    category: str = Field(min_length=2, max_length=512)
    keywords: list[str] = Field(default_factory=list, max_length=20)
    line: int = Field(default=1, ge=1, le=9)
    status: str = Field(default="active", pattern="^(active|draft|archived)$")
    audience: str = Field(default="all", pattern=AUDIENCE_PATTERN)
    # Картинки принимаются и при импорте: база знаний портала ссылается на
    # скриншоты интерфейса прямо из текста, без них такая статья — загадка.
    images: list[ArticleImage] = Field(default_factory=list, max_length=10)

    @model_validator(mode="after")
    def _answer_or_picture(self) -> ArticleIn:
        """Ответ обязателен, но им может быть и картинка.

        В выгрузках поддержки на «где эта кнопка» отвечают скриншотом. Пока
        схема требовала пять символов текста, такую статью нельзя было ни
        завести руками, ни отредактировать после импорта — а импорт её создаёт.
        """
        if len(self.answer.strip()) < MIN_ANSWER_CHARS and not self.images:
            raise ValueError(
                f"Нужен текст ответа не короче {MIN_ANSWER_CHARS} символов или хотя бы одна картинка"
            )
        return self


class ArticleImport(BaseModel):
    items: list[ArticleIn] = Field(max_length=5000)


class ArticleBulkFilter(BaseModel):
    """Те же фильтры, что у списка статей: действие применяется ровно к тому,
    что администратор видит в таблице."""

    query: str = Field(default="", max_length=200)
    category: str = Field(default="", max_length=512)
    status: str = Field(default="", pattern="^(active|draft|archived|)$")
    audience: str = Field(default="", pattern="^(all|supplier|customer|)$")


class ArticleBulkAction(BaseModel):
    """Массовое действие: publish — опубликовать (статус active),
    archive — снять с публикации, purge — удалить насовсем.

    Цель задаётся либо перечнем ids (отмеченное на странице), либо фильтром —
    «все черновики» перечислением не выразить: их может быть больше, чем
    влезает на страницу.
    """

    action: Literal["publish", "archive", "purge"]
    ids: list[str] = Field(default_factory=list, max_length=500)
    filter: ArticleBulkFilter | None = None

    @model_validator(mode="after")
    def _exactly_one_target(self) -> ArticleBulkAction:
        if bool(self.ids) is (self.filter is not None):
            raise ValueError("Нужно указать либо ids, либо filter")
        return self


class DocumentIngest(BaseModel):
    """Неформализованный документ (методичка, регламент) для разбора в статьи БЗ."""

    title: str = Field(min_length=1, max_length=256)
    text: str = Field(min_length=20, max_length=800_000)
    category: str | None = Field(default=None, max_length=512)
    line: int | None = None
    # «Инструкция для заказчика.pdf» целиком адресована заказчикам: роль задаётся
    # на весь документ, чтобы поставщику она в выдачу не попадала.
    audience: str = Field(default="all", pattern=AUDIENCE_PATTERN)
    # Черновики по умолчанию: формулировки, придуманные машиной, публикует человек.
    status: str = Field(default="draft", pattern="^(draft|active)$")
    use_llm: bool = True
    max_chunk_chars: int = Field(default=900, ge=300, le=4000)


class McpServerIn(BaseModel):
    """Внешний MCP-сервер: http(s)-эндпоинт или путь к stdio-скрипту."""

    name: str = Field(min_length=1, max_length=64)
    url: str = Field(min_length=1, max_length=1000)
    description: str = Field(default="", max_length=1000)
    enabled: bool = True
    headers: dict[str, str] = Field(default_factory=dict)
    timeout_s: float = Field(default=20.0, ge=1.0, le=120.0)


class McpServerPatch(BaseModel):
    """Частичное изменение — тумблер в списке шлёт только enabled."""

    name: str | None = Field(default=None, min_length=1, max_length=64)
    url: str | None = Field(default=None, min_length=1, max_length=1000)
    description: str | None = Field(default=None, max_length=1000)
    enabled: bool | None = None
    headers: dict[str, str] | None = None
    timeout_s: float | None = Field(default=None, ge=1.0, le=120.0)


class SettingsUpdate(BaseModel):
    updates: dict[str, Any] = Field(min_length=1)


class QuickReplyIn(BaseModel):
    title: str = Field(min_length=1, max_length=128)
    text: str = Field(min_length=1, max_length=2000)
    audience: str = Field(default="operator", pattern="^(operator|user)$")
    line: int | None = None


class OperatorMessageRequest(BaseModel):
    request_id: str | None = Field(default=None, min_length=1, max_length=128)
    text: str = Field(min_length=1, max_length=4000)


class GapConvertRequest(BaseModel):
    answer: str = Field(min_length=5, max_length=8000)
    category: str = Field(default="Новые темы", max_length=128)
    line: int = Field(default=1, ge=1, le=9)
    audience: str = Field(default="all", pattern=AUDIENCE_PATTERN)
    publish: bool = True


class ToKBRequest(BaseModel):
    question: str = Field(min_length=5, max_length=1000)
    answer: str = Field(min_length=5, max_length=8000)
    category: str = Field(default="Из диалогов", max_length=128)
    line: int = Field(default=1, ge=1, le=9)
    audience: str = Field(default="all", pattern=AUDIENCE_PATTERN)


# --- пошаговые сценарии по порталу ---

GUIDE_ID_PATTERN = "^[a-z0-9][a-z0-9-]{1,63}$"


def portal_path(value: str, field: str) -> str:
    """Путь портала и ничего больше.

    Расширение переходит по этому адресу само (location.assign), поэтому чужой
    домен здесь означал бы увод человека с портала по кнопке «Далее». Отдельно
    ловим «//host»: это протокол-относительный адрес, он тоже ведёт наружу.
    """
    path = (value or "").strip()
    if not path:
        return ""
    if not path.startswith("/") or path.startswith("//"):
        raise ValueError(f"{field}: нужен путь портала, например /profile/user")
    return path


class GuideLocatorIn(BaseModel):
    """Как найти элемент на странице: css, текст или ссылка."""

    model_config = ConfigDict(populate_by_name=True)

    css: str = Field("", max_length=400)
    text: str = Field("", max_length=200)
    contains: bool = False
    in_: str = Field("", alias="in", max_length=40)
    href: str = Field("", max_length=300)

    @model_validator(mode="after")
    def _needs_a_way_to_find(self) -> GuideLocatorIn:
        if not (self.css or self.text or self.href):
            raise ValueError("локатор пустой: нужен css, text или href")
        return self

    def compact(self) -> dict[str, Any]:
        """Только заполненные поля — каталог читают и глазами."""
        data: dict[str, object] = {
            "css": self.css,
            "text": self.text,
            "in": self.in_,
            "href": self.href,
        }
        out = {key: value for key, value in data.items() if value}
        if self.contains:
            out["contains"] = True
        return out


class GuideStepIn(BaseModel):
    title: str = Field(min_length=1, max_length=120)
    text: str = Field("", max_length=800)
    url: str = Field("", max_length=200)
    target: list[GuideLocatorIn] = Field(default_factory=list, max_length=6)
    action: str = Field("", pattern="^(|click|input)$")
    optional: bool = False
    # None — «переходить некуда, шаг доступен только с предыдущего»; поле,
    # которое не прислали, оставляем незаданным: тогда движок идёт по url
    go: str | None = None

    @model_validator(mode="after")
    def _check(self) -> GuideStepIn:
        if not self.target:
            raise ValueError("у шага нет ни одного локатора")
        self.url = portal_path(self.url, "url")
        if self.go is not None:
            self.go = portal_path(self.go, "go")
        return self

    def compact(self) -> dict[str, Any]:
        step: dict[str, Any] = {}
        if self.url:
            step["url"] = self.url
        step["title"] = self.title
        step["text"] = self.text
        step["target"] = [locator.compact() for locator in self.target]
        if self.action:
            step["action"] = self.action
        if self.optional:
            step["optional"] = True
        if "go" in self.model_fields_set:
            step["go"] = self.go or None
        return step


# Формулировка вопроса — строка поиска, а не абзац
MAX_GUIDE_QUESTION_CHARS = 200


class GuideIn(BaseModel):
    id: str = Field(pattern=GUIDE_ID_PATTERN)
    title: str = Field(min_length=1, max_length=200)
    audience: str = Field("all", pattern=AUDIENCE_PATTERN)
    questions: list[str] = Field(min_length=1, max_length=12)
    steps: list[GuideStepIn] = Field(min_length=1, max_length=12)
    enabled: bool = True
    source: str = Field("admin", pattern="^(admin|recorder|llm)$")

    @model_validator(mode="after")
    def _clean_questions(self) -> GuideIn:
        cleaned = [q.strip() for q in self.questions if q and q.strip()]
        if not cleaned:
            raise ValueError("нужна хотя бы одна формулировка вопроса")
        if any(len(q) > MAX_GUIDE_QUESTION_CHARS for q in cleaned):
            raise ValueError(f"формулировка длиннее {MAX_GUIDE_QUESTION_CHARS} символов")
        self.questions = cleaned
        return self


class GuideDraftRequest(BaseModel):
    """Черновик сценария нейросетью: что показать и по каким шагам."""

    goal: str = Field(min_length=3, max_length=200)
    audience: str = Field("all", pattern=AUDIENCE_PATTERN)
    steps: list[GuideStepIn] = Field(default_factory=list, max_length=12)
