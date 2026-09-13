"""Сборка базы знаний из каталога с нуля: `python -m app.rag.build /data/kb`.

Всё, что было в базе не из этого конвейера, уходит в архив; каждый файл каталога
разбирается заново, затем индекс пересобирается целиком в новую коллекцию.
"""

import argparse
import asyncio
import logging
import time
from pathlib import Path

from sqlalchemy import select, update

from app.config import get_settings
from app.models import KBArticle
from app.rag.ingest import IngestOptions, ingest_bytes
from app.rag.passages import PIPELINE
from app.runtime import AppRuntime

log = logging.getLogger("rag.build")
SUPPORTED = {".pdf", ".xlsx", ".xlsm", ".json", ".docx", ".md", ".txt", ".html", ".pptx"}


async def _archive_foreign(rt: AppRuntime) -> int:
    async with rt.db.session_factory() as db:
        rows = (
            await db.execute(select(KBArticle.id, KBArticle.origin).where(KBArticle.status != "archived"))
        ).all()
        foreign = [article_id for article_id, origin in rows if (origin or {}).get("pipeline") != PIPELINE]
        if foreign:
            await db.execute(update(KBArticle).where(KBArticle.id.in_(foreign)).values(status="archived"))
            await db.commit()
    return len(foreign)


async def build(folder: Path, *, keep_foreign: bool = False, add: bool = False) -> None:
    """С нуля: всё прежнее в архив, файлы каталога заново, индекс целиком. add — только
    дозагрузить файлы каталога (прошлые версии этих же документов заменяются), индекс
    дописывается их фрагментами без полной переиндексации."""
    rt = AppRuntime(get_settings())
    await rt.db.create_all()
    await rt.dyn.load()
    if add:
        await _add(rt, folder)
        await rt.stop()
        return
    if not keep_foreign:
        log.info("В архив из старой базы: %d", await _archive_foreign(rt))
    paths = await asyncio.to_thread(
        lambda: sorted(p for p in folder.iterdir() if p.suffix.lower() in SUPPORTED)
    )
    for path in paths:
        started = time.perf_counter()
        data = await asyncio.to_thread(path.read_bytes)
        result = await ingest_bytes(rt, path.name, data, IngestOptions(index=False))
        log.info("%s: %s за %.1f с", path.name, result, time.perf_counter() - started)
    started = time.perf_counter()
    count = await rt.kb.index_all()
    log.info("Индекс: %d фрагментов за %.1f с", count, time.perf_counter() - started)
    await rt.stop()


async def _add(rt: AppRuntime, folder: Path) -> None:
    paths = await asyncio.to_thread(
        lambda: sorted(p for p in folder.iterdir() if p.suffix.lower() in SUPPORTED)
    )
    for path in paths:
        started = time.perf_counter()
        data = await asyncio.to_thread(path.read_bytes)
        result = await ingest_bytes(rt, path.name, data, IngestOptions(index=True))
        log.info("%s: %s за %.1f с", path.name, result, time.perf_counter() - started)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("folder", type=Path)
    parser.add_argument("--keep-foreign", action="store_true")
    parser.add_argument("--add", action="store_true", help="дозагрузить файлы без сборки с нуля")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    asyncio.run(build(args.folder, keep_foreign=args.keep_foreign, add=args.add))


if __name__ == "__main__":
    main()
