"""Схема API в JSON — источник типов фронта (openapi-typescript).

Запуск из backend/: uv run python -m app.openapi_dump ../frontend/openapi.json
Совпадение файла с приложением проверяет tests/test_openapi_schema.py.
"""

import json
import sys
from pathlib import Path


def render() -> str:
    """Схема текущего приложения тем же текстом, что лежит в репозитории."""
    from app.main import app

    return json.dumps(app.openapi(), ensure_ascii=False, indent=2) + "\n"


if __name__ == "__main__":
    Path(sys.argv[1]).write_text(render(), encoding="utf-8", newline="\n")
