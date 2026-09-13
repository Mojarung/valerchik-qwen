"""Статическая конфигурация процесса (из ENV). Всё, что можно менять на лету, живёт в dynamic_settings."""

import secrets
from functools import lru_cache
from pathlib import Path

from pydantic import PrivateAttr
from pydantic_settings import BaseSettings, SettingsConfigDict

# Значения, которые кочуют по примерам .env и README. Встретив любое из них,
# считаем, что секрет не задавали: подписывать ими куки сотрудников — это
# отдать вход в операторскую всякому, кто читал репозиторий.
PLACEHOLDER_SECRETS = frozenset(
    {"", "dev-secret-not-for-prod", "change-me-in-env", "change-me-long-random-string"}
)
_MIN_SECRET_LEN = 32
_SECRET_FILE = ".app-secret"


#: Общеизвестные пароли демо-стенда: их подставляет сидер в lite, когда свои не
#: заданы, и их же подсказывает страница входа.
DEMO_ADMIN_PASSWORD = "admin12345"
DEMO_OPERATOR_PASSWORD = "operator12345"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_mode: str = "lite"  # lite | full
    app_secret: str = ""
    data_dir: Path = Path("./data")

    # Кому разрешён кросс-доменный доступ к API: список через запятую.
    # Пусто — только свой origin (плюс расширение, у него отдельный regex в main).
    allowed_origins: str = ""

    database_url: str = ""  # пусто в lite -> sqlite в data_dir
    redis_url: str = ""  # пусто в lite -> fakeredis
    qdrant_url: str = ""  # пусто в lite -> in-memory store
    tei_url: str = ""  # пусто в lite -> hash-эмбеддер
    tei_rerank_url: str = ""  # отдельный TEI с cross-encoder (профиль rerank)
    # Отдельный TEI с классификатором оскорблений. Пусто — нейросеть не спрашиваем,
    # guardrails работают на правилах и словаре.
    toxicity_url: str = ""

    llm_base_url: str = ""
    llm_api_key: str = ""
    llm_model: str = ""

    stt_url: str = ""
    # Синтез речи (тот же профиль voice). Пусто — чат работает молча.
    tts_url: str = ""
    # Разбор PDF с разметкой страниц и иллюстрациями (профиль docs).
    # Пусто — импорт идёт через markitdown: текстом и без картинок.
    docparse_url: str = ""

    # MCP-сервер каталога товаров портала. Адрес известен из compose, поэтому запись
    # в реестре MCP заводится сама на старте: интеграция «из коробки» лучше, чем
    # инструкция «зайдите в админку и добавьте сервер». Пусто — не регистрировать.
    zakupki_mcp_url: str = ""
    # Откуда ручка /api/catalog/image берёт картинки товаров. Портал отдаёт их только
    # на адреса в РФ: сервер за границей ходит через свой прокси картинок в РФ.
    portal_image_base: str = "https://zakupki.mos.ru"

    # Хранилище картинок из методичек. Пусто -> файлы в data_dir (lite, тесты, одиночный
    # инстанс); задан endpoint -> любое S3-совместимое хранилище (MinIO, Yandex Object Storage).
    s3_endpoint: str = ""
    s3_access_key: str = ""
    s3_secret_key: str = ""
    s3_bucket: str = "valerchik-kb"
    s3_region: str = "us-east-1"
    s3_public_url: str = ""  # адрес, по которому картинки видит браузер (если отличается)

    log_level: str = "INFO"
    seed_on_startup: bool = True
    # Пусто: в lite сидер подставит демо-пароли, в full остановит старт.
    admin_password: str = ""
    operator_password: str = ""
    # Итерации PBKDF2 на новый хеш пароля: 600 000 — рекомендация OWASP 2023 для
    # PBKDF2-HMAC-SHA256, меньше ставят только тесты. Число записывается в сам
    # хеш, так что поднять его можно в любой момент: старые хеши перехешируются
    # при следующем входе.
    password_hash_iterations: int = 600_000
    # /metrics в full: пусто — ручки нет (404), задан — только с Bearer-токеном.
    # Счётчики по путям и кодам ответа — карта API для постороннего.
    metrics_token: str = ""
    # Swagger и openapi.json в full выключены; включаются явно, когда документация
    # нужна на стенде.
    api_docs: bool = False

    _secret: str | None = PrivateAttr(default=None)

    @property
    def is_lite(self) -> bool:
        return self.app_mode.lower() != "full"

    @property
    def docs_enabled(self) -> bool:
        return self.is_lite or self.api_docs

    @property
    def demo_credentials(self) -> bool:
        """Сотрудники заведены с общеизвестными демо-паролями, и страница входа
        вправе их подсказать.

        Только в lite: в full сидер демо-паролей не подставляет. Свой пароль
        делает подсказку ложной — она лишь раскрывала бы логины сотрудников.
        """
        admin = self.admin_password.strip() or DEMO_ADMIN_PASSWORD
        operator = self.operator_password.strip() or DEMO_OPERATOR_PASSWORD
        return self.is_lite and (admin, operator) == (DEMO_ADMIN_PASSWORD, DEMO_OPERATOR_PASSWORD)

    @property
    def allowed_origins_list(self) -> list[str]:
        return [origin.strip().rstrip("/") for origin in self.allowed_origins.split(",") if origin.strip()]

    def resolve_secret(self) -> str:
        """Ключ подписи JWT и токенов владения диалогом.

        В full секрет обязан прийти из окружения: молча подставленный дефолт —
        это чужие валидные токены на проде. В lite (демо, тесты) сервис должен
        подниматься одной командой, поэтому секрет генерируется один раз и
        ложится рядом с данными: без файла куки протухали бы на каждом старте.
        """
        if self._secret is not None:
            return self._secret
        raw = self.app_secret.strip()
        if not self.is_lite:
            if raw in PLACEHOLDER_SECRETS or len(raw) < _MIN_SECRET_LEN:
                raise RuntimeError(
                    f"APP_SECRET не задан или слишком короткий: нужна случайная строка "
                    f"от {_MIN_SECRET_LEN} символов. Сгенерировать: python -c "
                    f'"import secrets; print(secrets.token_hex(32))"'
                )
            self._secret = raw
            return raw
        if raw not in PLACEHOLDER_SECRETS:
            self._secret = raw
            return raw
        self._secret = self._read_or_create_secret()
        return self._secret

    def _read_or_create_secret(self) -> str:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        path = self.data_dir / _SECRET_FILE
        if path.exists():
            stored = path.read_text(encoding="utf-8").strip()
            if stored:
                return stored
        generated = secrets.token_hex(32)
        path.write_text(generated, encoding="utf-8")
        return generated

    @property
    def effective_database_url(self) -> str:
        if self.database_url:
            return self.database_url
        self.data_dir.mkdir(parents=True, exist_ok=True)
        return f"sqlite+aiosqlite:///{self.data_dir / 'support.sqlite3'}"


@lru_cache
def get_settings() -> Settings:
    return Settings()
