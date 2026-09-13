"""Раздача собранного фронтенда самим API — single-origin запуск без nginx (lite-демо)."""

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

DEFAULT_DIST = Path(__file__).resolve().parent.parent.parent / "frontend" / "dist"

# Адреса, которые SPA не обслуживает. Опечатка в адресе API обязана давать 404 с
# JSON, а не index.html с кодом 200: клиент разбирал бы HTML как ответ, а тесты
# вели бы себя по-разному на машине со сборкой фронта и в CI без неё.
_NOT_SPA = ("api/", "ws/")


def mount_spa(app: FastAPI, dist: Path = DEFAULT_DIST) -> None:
    if not dist.exists():
        return
    if (dist / "assets").is_dir():
        app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

    dist_root = dist.resolve()

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa(full_path: str) -> FileResponse:
        if full_path in ("api", "ws") or full_path.startswith(_NOT_SPA):
            raise HTTPException(status_code=404)
        # Путь из URL нормализуем и проверяем, что он остался внутри dist: «..» и
        # абсолютные пути («C:/Windows/win.ini» на Windows отбрасывает левую часть)
        # иначе отдали бы наружу любой файл с диска.
        if full_path:
            target = (dist_root / full_path).resolve()
            if target.is_relative_to(dist_root) and target.is_file():
                return FileResponse(target)
        return FileResponse(dist_root / "index.html")
