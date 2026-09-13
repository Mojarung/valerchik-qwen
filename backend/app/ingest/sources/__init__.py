"""Источники данных. Импорт модуля регистрирует источник в реестре.

Модули находятся сами, списком в этом файле их держать не нужно: положил файл в
папку — источник в реестре. Так добавление формата в день выдачи данных не
требует правки общего файла, за который в этот момент держатся все остальные.

Сбой импорта одного источника не отменяет остальные: источник может тянуть
необязательную зависимость (docling, тяжёлый профиль), и падать всем импортом
из-за неподнятого профиля нельзя.
"""

from __future__ import annotations

import importlib
import logging
import pkgutil

log = logging.getLogger(__name__)


def _load_all() -> list[str]:
    loaded: list[str] = []
    for info in pkgutil.iter_modules(__path__):
        if info.name.startswith("_") or info.name == "base":
            continue
        try:
            importlib.import_module(f"{__name__}.{info.name}")
        except Exception:
            log.exception("Источник %s не загружен", info.name)
            continue
        loaded.append(info.name)
    return loaded


LOADED = _load_all()
