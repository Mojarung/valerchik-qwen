"""Раздача фронтенда из API не должна подменять ответы самого API.

Когда рядом был собран frontend/dist, любой неизвестный адрес получал
index.html с кодом 200 — в том числе опечатка в /api/…: клиент разбирал HTML
как ответ, а тесты вели себя по-разному на машине со сборкой и в CI без неё.
"""

from fastapi import FastAPI
from starlette.testclient import TestClient

from app.spa import mount_spa


def _spa(tmp_path) -> TestClient:
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html><title>SPA</title>", encoding="utf-8")
    (dist / "widget.html").write_text("widget", encoding="utf-8")
    app = FastAPI()
    mount_spa(app, dist)
    return TestClient(app)


def test_unknown_api_address_is_json_404(tmp_path):
    resp = _spa(tmp_path).get("/api/operator/sessions/x/nope")

    assert resp.status_code == 404
    assert resp.headers["content-type"].startswith("application/json")


def test_unknown_socket_address_is_404(tmp_path):
    assert _spa(tmp_path).get("/ws/nowhere").status_code == 404


def test_client_route_gets_index(tmp_path):
    resp = _spa(tmp_path).get("/admin/kb")

    assert resp.status_code == 200
    assert "<title>SPA</title>" in resp.text


def test_static_file_is_served_as_is(tmp_path):
    assert _spa(tmp_path).get("/widget.html").text == "widget"


def test_path_outside_dist_is_not_served(tmp_path):
    (tmp_path / "secret.txt").write_text("секрет", encoding="utf-8")

    resp = _spa(tmp_path).get("/..%2Fsecret.txt")

    assert "секрет" not in resp.text
