"""Инфраструктура проверяется по файлам, а не на словах.

Жюри нашло API под root, пределы памяти у четырёх сервисов из четырнадцати и
доверие к X-Forwarded-For от кого угодно. Тесты держат это исправленным: образы,
compose и конфиг nginx читаются как данные.
"""

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]


def _read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


COMPOSE = yaml.safe_load(_read("docker-compose.yml"))


def test_every_service_has_a_memory_limit():
    """Без предела промах одного сервиса ловит OOM-киллер хоста — и уносит
    соседей, а не виновника."""
    missing = [name for name, service in COMPOSE["services"].items() if "mem_limit" not in service]
    assert missing == []


def test_backend_image_runs_as_unprivileged_user():
    users = [line.split()[1] for line in _read("backend/Dockerfile").splitlines() if line.startswith("USER ")]
    assert users, "в образе api нет USER — процесс работает от root"
    assert users[-1] not in {"root", "0"}


def test_frontend_serves_from_unprivileged_nginx():
    final_stage = _read("frontend/Dockerfile").split("\nFROM ")[-1]
    assert final_stage.startswith("nginxinc/nginx-unprivileged:")
    # Обычной учётке порты ниже 1024 недоступны: nginx слушает 8080, compose пробрасывает его
    assert "listen 8080;" in _read("frontend/nginx.conf")
    assert [port.rsplit(":", 1)[-1] for port in COMPOSE["services"]["frontend"]["ports"]] == ["8080"]


def test_forwarded_headers_trusted_only_from_compose_network():
    """X-Forwarded-For пишет клиент: верить заголовку можно только от своего nginx."""
    subnet = COMPOSE["networks"]["default"]["ipam"]["config"][0]["subnet"]
    assert COMPOSE["x-backend-env"]["FORWARDED_ALLOW_IPS"] == subnet
    assert '"--forwarded-allow-ips", "*"' not in _read("backend/Dockerfile")


def test_nginx_passes_host_with_port_to_api():
    """$host в nginx — имя без порта. Со стенда на :8090 api получал Host: localhost
    при Origin http://localhost:8090 и закрывал сокет оператора с 4403."""
    conf = _read("frontend/nginx.conf")
    lines = [line.split() for line in conf.splitlines()]
    hosts = [words[2] for words in lines if words[:2] == ["proxy_set_header", "Host"]]
    assert hosts, "в nginx.conf нет ни одного proxy_set_header Host"
    assert set(hosts) == {"$http_host;"}, hosts
    assert conf.count("proxy_pass ") == len(hosts), "у каждого проксируемого location свой Host с портом"
