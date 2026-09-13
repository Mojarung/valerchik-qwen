"""Сборка базы знаний из выданных данных одной командой.

    uv run python -m app.tools.kb_build <файл|каталог>... [--status active] [--no-llm]

Тот же конвейер, что у загрузки через админку (app.services.ingest.ingest_file):
формат выбирает реестр источников, запись и индексацию делает pipeline. Название
документа — имя файла без расширения, как его подставляет форма импорта, поэтому
повторная сборка заменяет прошлую версию документа, а не добавляет вторую копию
рядом.

Роль документа угадывается по имени файла: «…для заказчика» — заказчикам,
«…для поставщика» — поставщикам. Остальное — всем; у JSON-выгрузки роль своя у
каждой записи, и общую ей не навязываем.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import time
from pathlib import Path
from typing import Any

from app.ingest.blob import FileBlob

SKIP_PREFIXES = (".", "~$")


def audience_for(name: str) -> str | None:
    """«…для заказчика.pdf» — заказчикам, «…для поставщика.pdf» — поставщикам.

    Ищем именно «для …», а не слово целиком: «Портал поставщиков» есть в имени
    почти любого документа и о его адресате ничего не говорит.
    """
    low = name.casefold()
    if "для заказчик" in low:
        return "customer"
    if "для поставщик" in low:
        return "supplier"
    return None


def collect(paths: list[Path]) -> list[Path]:
    files: list[Path] = []
    for path in paths:
        if path.is_dir():
            files.extend(
                sorted(p for p in path.iterdir() if p.is_file() and not p.name.startswith(SKIP_PREFIXES))
            )
        elif path.is_file():
            files.append(path)
    return files


async def build(
    files: list[Path], *, status: str, use_llm: bool, batch: int | None = None
) -> list[dict[str, Any]]:
    from app.config import get_settings
    from app.runtime import AppRuntime
    from app.services.ingest import ingest_file

    rt = AppRuntime(get_settings())
    # Массовая сборка — не живой трафик: TEI принимает до max-client-batch-size
    # текстов за запрос, и пачки по 4 растягивали сборку базы на час.
    if batch and hasattr(rt.embedder, "batch_size"):
        rt.embedder.batch_size = batch
    await rt.start()
    results: list[dict[str, Any]] = []
    try:
        for path in files:
            params: dict[str, Any] = {"status": status, "use_llm": use_llm, "category": ""}
            audience = audience_for(path.name)
            if audience and path.suffix.lower() != ".json":
                params["audience"] = audience
            started = time.perf_counter()
            result = await ingest_file(
                rt, FileBlob(path.name, path.read_bytes()), title=path.stem, params=params
            )
            summary = {
                "file": path.name,
                "source": result.get("source"),
                "created": result.get("created"),
                "replaced": result.get("replaced"),
                "audience": params.get("audience", "по записям"),
                "seconds": round(time.perf_counter() - started, 1),
            }
            print(summary, flush=True)
            results.append(summary)
    finally:
        await rt.stop()
    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("paths", nargs="+", type=Path)
    parser.add_argument("--status", default="active", choices=("active", "draft"))
    parser.add_argument("--no-llm", action="store_true", help="вопросы к кускам — по заголовкам, без модели")
    parser.add_argument("--batch", type=int, help="текстов в одном запросе к эмбеддеру (TEI принимает до 64)")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    files = collect(args.paths)
    if not files:
        print("нечего собирать", file=sys.stderr)
        return 1
    asyncio.run(build(files, status=args.status, use_llm=not args.no_llm, batch=args.batch))
    return 0


if __name__ == "__main__":
    sys.exit(main())
