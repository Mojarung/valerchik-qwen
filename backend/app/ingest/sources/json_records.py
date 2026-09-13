"""JSON и JSONL: массив объектов «вопрос — ответ».

Массив ищем не только в корне: выгрузки делали не для нас, поэтому они приезжают
завёрнутыми — {"items": [...]}, {"data": {"faq": [...]}}. Обходим дерево до первого
списка словарей, у которых распознаются оба поля, и запоминаем путь: провенанс должен
уметь показать, из какого именно элемента какого массива взялся ответ.

Ключи по синонимам, а не по схеме: одна и та же выгрузка в разных системах называет
вопрос "question", "тема" и "текст_обращения", и требовать единый формат означало бы
не импортировать ничего.
"""

from __future__ import annotations

import json
import math
from collections.abc import AsyncIterator
from typing import Any

from app.ingest.blob import FileBlob
from app.ingest.records import ArticleRecord, Provenance
from app.ingest.registry import IngestContext, register
from app.ingest.sources.base import SourceMismatch

QUESTION_KEYS = (
    "question",
    "q",
    "title",
    "вопрос",
    "заголовок",
    "тема",
    "текст_обращения",
    "текст обращения",
    "обращение",
)
ANSWER_KEYS = ("answer", "a", "body", "text", "ответ", "решение", "описание", "response")
CATEGORY_KEYS = ("category", "категория", "раздел")
LINE_KEYS = ("line", "линия")
AUDIENCE_KEYS = ("audience", "аудитория")

# Глубже прячут разве что случайно: шесть уровней покрывают и {"result": {"data": ...}},
# и постраничные обёртки, а дальше обход начинает стоить дороже самого разбора.
MAX_DEPTH = 6
# Смотрим начало списка, а не весь: решение «этот массив — тот самый» принимается по
# первым элементам, гонять стотысячную выгрузку ради него незачем. Двести, а не двадцать:
# выгрузки регулярно начинаются с блока служебных строк, и на коротком окне такой файл
# целиком уезжает в отказ. Проверка одного элемента — два поиска по словарю, окно дешёвое.
SCAN_LIMIT = 200


def _lowered(item: dict[str, Any]) -> dict[str, Any]:
    """Ключи в нижнем регистре: в одной выгрузке живут "Вопрос", "question" и "ВОПРОС"."""
    return {str(key).strip().lower(): value for key, value in item.items()}


def _scalar_text(value: Any) -> str | None:
    """Скаляр → непустая строка. None, если значение текстом не является.

    NaN и Infinity JSON пишет литералами, и Python их молча разбирает: без этой
    проверки пустая ячейка выгрузки превращается в ответ «nan», который бот отдаст
    дословно — authored=True разрешает. Пропустить запись честнее.
    """
    if isinstance(value, bool) or not isinstance(value, str | int | float):
        return None
    if isinstance(value, float) and not math.isfinite(value):
        return None
    text = str(value).strip()
    return text or None


def _pick_text(lowered: dict[str, Any], keys: tuple[str, ...]) -> str | None:
    """Первое непустое текстовое значение по списку синонимов.

    Вложенный объект за текст не считаем: под ключом "text" часто лежит служебная
    структура выгрузки, и её попадание в ответ хуже, чем пропуск записи. А вот список
    строк — это разбитый на абзацы ответ («шаг 1», «шаг 2»), и терять его жалко.
    """
    for key in keys:
        value = lowered.get(key)
        if isinstance(value, list):
            parts = [_scalar_text(part) for part in value]
            if parts and all(parts):
                return "\n".join(parts)  # type: ignore[arg-type]
            continue
        text = _scalar_text(value)
        if text:
            return text
    return None


def _as_line(value: Any) -> int | None:
    """Номер линии поддержки: в выгрузках он приезжает и числом, и строкой «2»."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    text = str(value).strip()
    return int(text) if text.isdigit() else None


def map_fields(item: dict[str, Any]) -> tuple[str, str] | None:
    """Вопрос и ответ объекта или None, если это не пара «вопрос — ответ».

    None — рабочий ответ, а не ошибка: в том же массиве едут строки справочников и
    счётчики, и пропустить их надо молча.
    """
    if not isinstance(item, dict):
        return None
    lowered = _lowered(item)
    question = _pick_text(lowered, QUESTION_KEYS)
    answer = _pick_text(lowered, ANSWER_KEYS)
    if not question or not answer:
        return None
    return question, answer


def _find_records(node: Any, path: str = "$", depth: int = 0) -> tuple[list[Any], str]:
    """Первый список, в котором распознаются пары. Возвращает (сам список, его путь).

    Отдаём список целиком, а не только подходящие элементы: индекс в locator должен
    указывать на настоящую позицию в файле, иначе провенанс приведёт не туда.
    """
    if depth > MAX_DEPTH:
        return [], path
    if isinstance(node, list):
        if any(map_fields(item) for item in node[:SCAN_LIMIT] if isinstance(item, dict)):
            return node, path
        for index, item in enumerate(node[:SCAN_LIMIT]):
            found, found_path = _find_records(item, f"{path}[{index}]", depth + 1)
            if found:
                return found, found_path
    elif isinstance(node, dict):
        for key, value in node.items():
            found, found_path = _find_records(value, f"{path}.{key}", depth + 1)
            if found:
                return found, found_path
    return [], path


def _load(blob: FileBlob) -> Any:
    """Разобрать файл как JSON, а если не вышло — как JSONL.

    По суффиксу судить нельзя до конца: построчная выгрузка регулярно приезжает с
    именем .json, и отказывать ей только из-за расширения жалко.
    """
    raw = blob.decoded().strip()
    if not raw:
        raise ValueError("пустой файл")
    if blob.suffix != ".jsonl":
        try:
            return json.loads(raw)
        except ValueError:
            pass
    return [json.loads(line) for line in raw.splitlines() if line.strip()]


@register
class JsonRecordsSource:
    """Готовые пары «вопрос — ответ» из JSON/JSONL."""

    name = "json_records"
    priority = 20

    async def sniff(self, blob: FileBlob) -> float:
        if blob.suffix not in (".json", ".jsonl"):
            return 0.0
        try:
            items, _ = _find_records(_load(blob))
        except ValueError, TypeError:
            return 0.0
        # Нашли массив пар — сомнений почти нет: формат самоописывающийся
        return 0.9 if items else 0.0

    async def parse(self, blob: FileBlob, ctx: IngestContext) -> AsyncIterator[ArticleRecord]:
        try:
            items, path = _find_records(_load(blob))
        except (ValueError, TypeError) as exc:
            raise SourceMismatch(f"файл не разбирается как JSON: {exc}") from exc
        if not items:
            raise SourceMismatch("в файле нет массива пар «вопрос — ответ»")

        params = getattr(ctx, "params", {}) or {}
        for index, item in enumerate(items):
            mapped = map_fields(item) if isinstance(item, dict) else None
            if not mapped:
                continue
            question, answer = mapped
            lowered = _lowered(item)
            # Отдельной переменной, а не через `or`: линия «0» — это значение, а не
            # пустота, и `or` подменил бы её умолчанием из параметров импорта.
            line = _as_line(_pick_text(lowered, LINE_KEYS))
            if line is None:
                line = _as_line(params.get("line"))
            # Разметка самого объекта важнее общих параметров импорта: в выгрузке она
            # проставлена по каждой записи, а параметры — грубое умолчание на весь файл.
            yield ArticleRecord(
                question=question,
                answer=answer,
                category=_pick_text(lowered, CATEGORY_KEYS) or str(params.get("category") or ""),
                line=line,
                audience=_pick_text(lowered, AUDIENCE_KEYS) or str(params.get("audience") or "all"),
                origin=Provenance(
                    file=blob.name, locator=f"{path}[{index}]", source_name=self.name, authored=True
                ),
            )
