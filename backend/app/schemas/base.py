"""Основа моделей ответа API."""

from pydantic import BaseModel, ConfigDict


class ApiModel(BaseModel):
    """Модель ответа API.

    Лишний ключ — ошибка, а не молча отрезанное поле: иначе расхождение ручки
    и модели всплывало бы у фронта пустым экраном, а не в тестах падением. Поле
    с умолчанием в схеме OpenAPI обязательное: в ответе оно есть всегда, и
    сгенерированные типы фронта не требуют проверять его на undefined.
    """

    model_config = ConfigDict(extra="forbid", json_schema_serialization_defaults_required=True)


class SparseModel(ApiModel):
    """Модель ответа, который отдаётся с response_model_exclude_unset: поле с
    умолчанием может отсутствовать, и схема честно помечает его необязательным."""

    model_config = ConfigDict(extra="forbid", json_schema_serialization_defaults_required=False)


class StoredModel(BaseModel):
    """JSON из колонки базы в составе ответа.

    Его формат менялся от версии к версии: известное типизируется, лишний ключ
    старой записи отбрасывается, а не роняет ответ пятисотой.
    """

    model_config = ConfigDict(extra="ignore", json_schema_serialization_defaults_required=True)


class Created(ApiModel):
    """Создана запись — вот её идентификатор."""

    id: str
