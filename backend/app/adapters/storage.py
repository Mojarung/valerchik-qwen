"""Хранилище медиа из методичек: S3-совместимое (full) или файлы в data_dir (lite).

Картинки из инструкций — это сотни файлов на документ, и класть их в БД нельзя:
BLOB'ы раздувают дампы и репликацию. В full-режиме они уезжают в S3 (MinIO рядом
в compose, в проде — любое S3-совместимое хранилище), в lite остаются файлами
в томе данных, чтобы демо и тесты поднимались без единого внешнего сервиса.

Оба варианта отдают одинаковую ссылку `/api/kb/media/<key>`: фронт не должен
знать, где физически лежит файл, а S3 может быть закрыт от интернета.
"""

import asyncio
import logging
import mimetypes
import re
from abc import ABC, abstractmethod
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.config import Settings

log = logging.getLogger(__name__)

MEDIA_URL_PREFIX = "/api/kb/media/"

# Всё, кроме букв, цифр, точки, дефиса и подчёркивания, из имени файла вырезаем.
_UNSAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")


def safe_filename(raw: str, fallback: str = "file") -> str:
    """Имя файла от клиента → имя, безопасное как часть ключа хранилища.

    `Path(name).name` для этого недостаточно: на Linux обратный слеш не считается
    разделителем, и «..\\..\\kb\\doc\\0000.png» проходит целиком, а дальше ключ
    попадает в хранилище, где слеши уже нормализуются — и файл уезжает в чужой
    раздел. Поэтому режем по обоим разделителям и вычищаем всё лишнее.
    """
    name = raw.replace("\\", "/").rsplit("/", 1)[-1].strip()
    name = _UNSAFE_NAME.sub("_", name).lstrip(".")
    return name[:120] or fallback


class MediaStorage(ABC):
    @abstractmethod
    async def put(self, key: str, data: bytes, content_type: str | None = None) -> str:
        """Сохраняет объект и возвращает ссылку для фронта."""

    @abstractmethod
    async def get(self, key: str) -> tuple[bytes, str] | None:
        """-> (содержимое, content-type) либо None, если объекта нет."""

    @abstractmethod
    async def delete_prefix(self, prefix: str) -> int:
        """Удаляет все объекты с префиксом (например, все картинки документа)."""


def _guess_type(key: str, content_type: str | None) -> str:
    return content_type or mimetypes.guess_type(key)[0] or "application/octet-stream"


class LocalMediaStorage(MediaStorage):
    """Файлы в data_dir. Все инстансы API монтируют один том, поэтому этого хватает
    и при `--scale api=3` — S3 нужен, когда инстансы разъезжаются по машинам."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        """Ключ → путь внутри корня. Всё, что уводит наружу, отвергаем.

        Отсечь «..» недостаточно: `Path('data') / 'C:/Windows/win.ini'` на Windows
        отбрасывает левую часть и даёт абсолютный путь. Поэтому проверяем не строку,
        а итоговый путь после нормализации — он обязан лежать внутри корня.

        Но и проверки границы хранилища мало. Ключ `chat/<id>/../../kb/doc/0000.png`
        из корня не выходит — и потому проходил, — а попадает в чужой раздел и
        перезаписывает иллюстрацию методички. Поэтому «..» среди сегментов
        отвергаем отдельно: внутри хранилища разделы тоже граница.
        """
        candidate = key.replace("\\", "/").lstrip("/")
        if not candidate:
            raise ValueError("пустой ключ")
        if any(segment == ".." for segment in candidate.split("/")):
            raise ValueError(f"ключ выходит за пределы своего раздела: {key}")
        resolved = (self.root / candidate).resolve()
        root = self.root.resolve()
        if not resolved.is_relative_to(root):
            raise ValueError(f"ключ уводит за пределы хранилища: {key}")
        return resolved

    async def put(self, key: str, data: bytes, content_type: str | None = None) -> str:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(path.write_bytes, data)
        return MEDIA_URL_PREFIX + key

    async def get(self, key: str) -> tuple[bytes, str] | None:
        try:
            path = self._path(key)
        except ValueError:
            return None
        if not path.is_file():
            return None
        data = await asyncio.to_thread(path.read_bytes)
        return data, _guess_type(key, None)

    async def delete_prefix(self, prefix: str) -> int:
        base = self._path(prefix)
        if not base.exists():
            return 0
        removed = 0
        for path in sorted(base.rglob("*"), reverse=True):
            if path.is_file():
                path.unlink()
                removed += 1
            else:
                path.rmdir()
        base.rmdir()
        return removed


class S3MediaStorage(MediaStorage):
    """S3-совместимое хранилище через boto3.

    boto3 синхронный, поэтому каждый вызов уходит в поток: заливка идёт при импорте
    (сотни файлов подряд), и блокировать на ней цикл событий нельзя.
    """

    def __init__(self, *, endpoint: str, access_key: str, secret_key: str, bucket: str, region: str) -> None:
        import boto3
        from botocore.config import Config

        self.bucket = bucket
        self._client = boto3.client(
            "s3",
            endpoint_url=endpoint,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            region_name=region,
            # path-style: у MinIO по умолчанию нет wildcard-DNS под виртуальные хосты бакетов
            config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
        )
        self._ensure_bucket()

    def _ensure_bucket(self) -> None:
        """Пытается создать бакет, но не решает судьбу адаптера.

        Ошибка здесь ничего не значит для выбора хранилища: MinIO может ещё
        подниматься. Ронять конструктор нельзя — иначе API и воркер, стартовавшие
        в разные секунды, разъедутся по разным хранилищам, и картинки, записанные
        одним, второй не найдёт.
        """
        try:
            self._client.head_bucket(Bucket=self.bucket)
        except Exception:  # noqa: BLE001 — включая обрыв соединения, не только ClientError
            try:
                self._client.create_bucket(Bucket=self.bucket)
                log.info("Создан бакет %s", self.bucket)
            except Exception:  # noqa: BLE001
                log.warning("Бакет %s пока недоступен — попробуем при первой записи", self.bucket)

    async def put(self, key: str, data: bytes, content_type: str | None = None) -> str:
        await asyncio.to_thread(
            self._client.put_object,
            Bucket=self.bucket,
            Key=key,
            Body=data,
            ContentType=_guess_type(key, content_type),
        )
        return MEDIA_URL_PREFIX + key

    async def get(self, key: str) -> tuple[bytes, str] | None:
        from botocore.exceptions import ClientError

        def _fetch() -> tuple[bytes, str] | None:
            try:
                resp = self._client.get_object(Bucket=self.bucket, Key=key)
            except ClientError:
                return None
            return resp["Body"].read(), resp.get("ContentType") or _guess_type(key, None)

        return await asyncio.to_thread(_fetch)

    async def delete_prefix(self, prefix: str) -> int:
        def _delete() -> int:
            paginator = self._client.get_paginator("list_objects_v2")
            removed = 0
            for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
                keys = [{"Key": obj["Key"]} for obj in page.get("Contents", [])]
                if keys:
                    self._client.delete_objects(Bucket=self.bucket, Delete={"Objects": keys})
                    removed += len(keys)
            return removed

        return await asyncio.to_thread(_delete)


def build_media_storage(settings: Settings) -> MediaStorage:
    """S3, если задан endpoint; иначе файлы в data_dir.

    Молчаливого отката на локальный том тут нет намеренно: API и воркер стартуют
    в разные моменты, и если один откатится, а другой нет, картинки, записанные
    одним, второй просто не найдёт. Раз администратор настроил S3 — работаем с
    S3, а недоступность лечится перезапуском хранилища, а не сменой адаптера.
    """
    if settings.s3_endpoint:
        return S3MediaStorage(
            endpoint=settings.s3_endpoint,
            access_key=settings.s3_access_key,
            secret_key=settings.s3_secret_key,
            bucket=settings.s3_bucket,
            region=settings.s3_region,
        )
    return LocalMediaStorage(Path(settings.data_dir) / "media")
