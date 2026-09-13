"""Проверка адресов, по которым сервер пойдёт сам.

Адреса внешних систем задаёт администратор — MCP-серверы и базовый URL LLM.
Сервер ходит по ним изнутри контейнерной сети, поэтому такой адрес превращает
настройку в SSRF: можно указать соседний контейнер, localhost или сервис
метаданных облака (169.254.169.254) и получить ответ оттуда. Хуже того, к
запросу приложится API-ключ LLM — то есть настройка становится ещё и способом
вытащить секрет, который приложение специально маскирует в выдаче.

Поэтому по умолчанию пускаем только в публичную сеть. Локальный Ollama или
внутренний MCP — законный сценарий, но включать его должен человек осознанно:
тумблер security.allow_internal_urls в админке.
"""

import asyncio
import ipaddress
import socket
from urllib.parse import urlparse

ALLOWED_SCHEMES = ("http", "https")


def _addresses(host: str) -> list[str]:
    return [str(info[4][0]) for info in socket.getaddrinfo(host, None)]


def _is_internal(address: str) -> bool:
    ip = ipaddress.ip_address(address)
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


async def check_external_url(url: str, allow_internal: bool = False) -> str | None:
    """-> текст ошибки, если по этому адресу ходить нельзя, иначе None."""
    parsed = urlparse(url.strip())
    if parsed.scheme not in ALLOWED_SCHEMES:
        return f"Допустимы только адреса http и https, получено «{parsed.scheme or url[:20]}»"
    if not parsed.hostname:
        return "В адресе нет имени хоста"
    if allow_internal:
        return None
    try:
        # getaddrinfo блокирующий: в event loop его звать нельзя, иначе
        # недоступный DNS подвесит весь процесс на время таймаута резолвера.
        addresses = await asyncio.to_thread(_addresses, parsed.hostname)
    except OSError:
        return f"Имя «{parsed.hostname}» не разрешается в адрес"
    internal = [a for a in addresses if _is_internal(a)]
    if internal:
        return (
            f"Адрес «{parsed.hostname}» ведёт во внутреннюю сеть ({internal[0]}). "
            "Если это намеренно, включите «Разрешить внутренние адреса» в настройках."
        )
    return None
