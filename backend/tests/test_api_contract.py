"""Контракт API: у каждой ручки — модель ответа, у роутеров — ни строчки SQL.

Жюри нашло 76 ручек из 81 без модели ответа: форма JSON жила только в коде
обработчика, и фронт узнавал о переименованном поле из пустого экрана. SQL
прямо в роутерах смешивал HTTP с запросами к базе — ни переиспользовать, ни
проверить без поднятия приложения.
"""

import re
from pathlib import Path
from typing import get_args, get_origin

import pytest
from fastapi.routing import APIRoute
from pydantic import BaseModel

from app.routers import admin, auth, catalog, chat, guides, health, kb, operator, voice

MODULES = {
    m.__name__.rsplit(".", 1)[-1]: m
    for m in (admin, auth, catalog, chat, guides, health, kb, operator, voice)
}
SQL = re.compile(r"\bselect\(|\.execute\(|session_factory\(|\bdb\.(?:get|add|delete|commit|refresh)\(")
PRIMITIVES = (str, int, float, bool)


def _routes(module: object) -> list[APIRoute]:
    routers = [getattr(module, attr) for attr in ("router", "admin_router") if hasattr(module, attr)]
    return [route for r in routers for route in r.routes if isinstance(route, APIRoute)]


def _has_contract(route: APIRoute) -> bool:
    if route.status_code == 204:
        return True
    model = route.response_model
    if model is not None:
        inner = get_args(model)[0] if get_origin(model) is list else model
        return inner in PRIMITIVES or (isinstance(inner, type) and issubclass(inner, BaseModel))
    # Не JSON (картинка, звук, выгрузка): тип содержимого объявлен в документации
    return bool((route.responses.get(200) or {}).get("content"))


@pytest.mark.parametrize("name", sorted(MODULES))
def test_every_endpoint_declares_its_response(name):
    loose = [f"{sorted(r.methods)[0]} {r.path}" for r in _routes(MODULES[name]) if not _has_contract(r)]
    assert loose == []


@pytest.mark.parametrize("name", sorted(MODULES))
def test_router_has_no_sql(name):
    source = Path(MODULES[name].__file__).read_text(encoding="utf-8")
    hits = [f"{n}: {line.strip()}" for n, line in enumerate(source.splitlines(), 1) if SQL.search(line)]
    assert hits == []
