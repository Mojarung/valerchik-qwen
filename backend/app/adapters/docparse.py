"""Клиент сервиса разбора PDF (docling) с деградацией до markitdown.

Сервис живёт отдельным контейнером за профилем `docs`, потому что тянет torch и
веса моделей. Здесь — тонкая обёртка: отдать файл, забрать markdown и картинки,
подчистить за собой временный каталог.

Правило то же, что и с MCP: внешний сервис не имеет права ломать импорт. Не
поднят, не ответил, упал — возвращаем None, и вызывающий код разбирает документ
markitdown'ом. Без иллюстраций, но полностью рабочим.
"""

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

import httpx

if TYPE_CHECKING:
    from app.config import Settings

log = logging.getLogger(__name__)

# Разметка страниц идёт секунды на страницу, а документы бывают трёхсотстраничные
PARSE_TIMEOUT_S = 3600.0


@dataclass
class ParsedPicture:
    """Иллюстрация с подписью, привязанной к ней самим документом."""

    data: bytes
    caption: str = ""
    page: int | None = None
    # Где картинка стояла в порядке чтения: заголовок раздела и начало абзаца
    # перед ней. По ним картинка без номера в подписи находит свой кусок текста.
    anchor_heading: str = ""
    anchor_text: str = ""


@dataclass
class ParsedDocument:
    markdown: str
    pictures: list[ParsedPicture] = field(default_factory=list)
    pages: int = 0
    headings: list[str] = field(default_factory=list)
    seconds: float = 0.0


class DocParseClient:
    def __init__(self, base_url: str, spool_dir: Path) -> None:
        self.base_url = base_url.rstrip("/")
        # Каталог, общий с сервисом: картинки передаются через том, а не по сети —
        # на трёхсотстраничной инструкции их шестьсот.
        self.spool_dir = spool_dir

    async def parse(self, filename: str, raw: bytes, *, do_ocr: bool = False) -> ParsedDocument | None:
        try:
            async with httpx.AsyncClient(timeout=PARSE_TIMEOUT_S) as client:
                resp = await client.post(
                    f"{self.base_url}/parse",
                    files={"file": (filename, raw, "application/pdf")},
                    data={"do_ocr": str(do_ocr).lower()},
                )
                resp.raise_for_status()
                payload = resp.json()
        except Exception as exc:  # noqa: BLE001 — импорт важнее иллюстраций
            log.warning("Сервис разбора недоступен (%s: %s), беру markitdown", type(exc).__name__, exc)
            return None

        batch = payload.get("batch") or ""
        pictures: list[ParsedPicture] = []
        batch_dir = self.spool_dir / batch if batch else None
        try:
            for item in payload.get("pictures", []):
                if batch_dir is None:
                    break
                path = batch_dir / str(item.get("file", ""))
                try:
                    data = path.read_bytes()
                except OSError:
                    log.warning("Иллюстрация %s не читается", path)
                    continue
                anchor = item.get("anchor") or {}
                pictures.append(
                    ParsedPicture(
                        data=data,
                        caption=item.get("caption") or "",
                        page=item.get("page"),
                        anchor_heading=str(anchor.get("heading") or ""),
                        anchor_text=str(anchor.get("before") or ""),
                    )
                )
        finally:
            # Каталог убираем в любом случае: на трёхсотстраничной инструкции это
            # сотни мегабайт в общем томе, и падение на середине чтения оставило бы
            # их лежать до перезапуска сервиса.
            await self._drop_batch(batch)

        return ParsedDocument(
            markdown=payload.get("markdown") or "",
            pictures=pictures,
            pages=int(payload.get("pages") or 0),
            headings=list(payload.get("headings") or []),
            seconds=float(payload.get("seconds") or 0.0),
        )

    async def _drop_batch(self, batch: str) -> None:
        if not batch:
            return
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                await client.delete(f"{self.base_url}/batch/{batch}")
        except Exception:  # noqa: BLE001 — сервис сам подчистит по таймауту
            log.debug("Не удалось убрать выгрузку %s", batch)


def build_docparse_client(settings: Settings) -> DocParseClient | None:
    if not settings.docparse_url:
        return None
    return DocParseClient(settings.docparse_url, Path(settings.data_dir) / "docparse")
