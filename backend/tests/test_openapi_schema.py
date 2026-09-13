"""Схема API, из которой генерируются типы фронта, совпадает с приложением.

Поменял ручку или модель — перегенерируй схему и типы:
    cd backend  && uv run python -m app.openapi_dump ../frontend/openapi.json
    cd frontend && npm run api:types
Иначе фронт продолжит собираться со старыми типами и узнает о переименованном
поле только от пользователя.
"""

from pathlib import Path

from app.openapi_dump import render

SCHEMA = Path(__file__).resolve().parents[2] / "frontend" / "openapi.json"


def test_frontend_schema_matches_the_app():
    assert SCHEMA.read_text(encoding="utf-8") == render(), "схема устарела — команды в docstring модуля"
