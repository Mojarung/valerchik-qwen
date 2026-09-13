"""Последний в очереди: модель сама размечает непонятный файл.

Дорого и недетерминированно, поэтому и последний — приоритет отрицательный,
уверенность чуть выше порога отсечения. Берёт первые 40 КБ текста: если формат
не распознался, нам нужно понять его устройство, а не перемолоть весь объём.
Записи помечаются authored=False — дословно такой ответ отдавать нельзя.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any

from app.ingest.blob import FileBlob
from app.ingest.records import ArticleRecord, Provenance
from app.ingest.registry import IngestContext, register
from app.ingest.sources.base import SourceMismatch

if TYPE_CHECKING:
    from app.dynamic_settings import DynamicSettings

log = logging.getLogger(__name__)

MAX_SAMPLE_CHARS = 40_000
# Разбор одного файла не должен вешать загрузку: локальная модель на длинном
# фрагменте уходит думать надолго, а иногда не отвечает вовсе.
DEFAULT_TIMEOUT_S = 60.0
PROMPT = (
    "Ниже фрагмент файла неизвестного формата из службы поддержки. Извлеки из него "
    "пары «вопрос пользователя — ответ поддержки». Верни JSON-массив объектов с "
    "ключами question и answer, без пояснений. Если пар нет, верни пустой массив."
)


def _as_text(value: object) -> str:
    """Значение поля → строка, но только если это вообще текст.

    Модель периодически кладёт в question вложенный объект или список абзацев.
    str() от них дал бы в базу знаний питоновский repr («{'text': ...}»), и такой
    мусор хуже отсутствующей записи: он находится поиском и уезжает в ответ.
    """
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, bool) or value is None:
        return ""
    if isinstance(value, int | float):
        return str(value)
    return ""


def records_from_payload(payload: list[Any], *, file: str, source_name: str) -> list[ArticleRecord]:
    """Ответ модели → записи. Отдельно от вызова, чтобы разбор проверялся без сети.

    Половинчатые пары выкидываем молча: модель регулярно возвращает объект с одним
    заполненным полем, и статья без вопроса или без ответа в базе бесполезна.
    """
    records: list[ArticleRecord] = []
    for index, item in enumerate(payload):
        if not isinstance(item, dict):
            continue
        question = _as_text(item.get("question"))
        answer = _as_text(item.get("answer"))
        if not question or not answer:
            continue
        records.append(
            ArticleRecord(
                question=question,
                answer=answer,
                # Провенанс без строки файла: модель читала текст целиком и не умеет
                # показать, откуда взяла пару, — честнее пронумеровать сами пары.
                origin=Provenance(
                    file=file, locator=f"фрагмент {index + 1}", source_name=source_name, authored=False
                ),
            )
        )
    return records


def _strip_fence(text: str) -> str:
    """Снять ```-обрамление: модели заворачивают JSON в блок кода даже когда просят не надо."""
    stripped = text.strip()
    if not stripped.startswith("```"):
        return stripped
    stripped = stripped.removeprefix("```")
    # После открывающих кавычек часто стоит имя языка — оно до первого перевода строки
    head, _, rest = stripped.partition("\n")
    if head.strip().isalpha():
        stripped = rest
    return stripped.removesuffix("```").strip()


def _bracketed(text: str) -> str | None:
    """Кусок от первой «[» до последней «]» — когда модель предварила ответ фразой."""
    start, end = text.find("["), text.rfind("]")
    return text[start : end + 1] if 0 <= start < end else None


def _json_array(text: str) -> list[Any] | None:
    """Ответ модели → список пар; None, если массива в нём нет.

    Терпим к обёрткам намеренно: ответ уже оплачен временем и токенами, а мелкие
    модели то заворачивают массив в объект («{"pairs": [...]}»), то приписывают
    перед ним «Вот пары:». Выбрасывать из-за этого весь разбор — расточительно.
    """
    cleaned = _strip_fence(text)
    for candidate in (cleaned, _bracketed(cleaned)):
        if not candidate:
            continue
        try:
            data = json.loads(candidate)
        except ValueError:
            continue
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            for value in data.values():
                if isinstance(value, list):
                    return value
    return None


def _sample_text(blob: FileBlob) -> str:
    """Первые MAX_SAMPLE_CHARS символов файла — конвертером, а не вышло, так байтами.

    Ради этого шага источник и существует. `blob.text` зовёт markitdown, а тот
    отказывается от незнакомого расширения («dump.dat», «faq.jsonl», выгрузка без
    расширения вовсе) — то есть ровно от тех файлов, за которыми сюда и приходят.
    Двоичные не трогаем: раскодированный PDF даст модели мусор вместо текста.
    """
    try:
        sample = blob.text[:MAX_SAMPLE_CHARS]
    except Exception as exc:  # noqa: BLE001 — отказ конвертера это не сбой конвейера
        log.info("Конвертер не взял %s (%s), пробуем прочитать байтами", blob.name, exc)
        sample = ""
    if sample.strip() or blob.is_binary:
        return sample
    return blob.decoded()[:MAX_SAMPLE_CHARS]


def _timeout_s(dyn: DynamicSettings) -> float:
    """Таймаут берём общий с агентом: у оператора одна ручка на «сколько ждать модель»."""
    try:
        return float(dyn.get("agent.timeout_s") or DEFAULT_TIMEOUT_S)
    except TypeError, ValueError:
        return DEFAULT_TIMEOUT_S


def _require_model(ctx: IngestContext) -> Any:
    """Рантайм с включённой и настроенной моделью — или отказ источника с причиной."""
    if ctx.rt is None:
        raise SourceMismatch("нет рантайма — модель недоступна")
    if not ctx.rt.dyn.get("ingest.llm_fallback_enabled"):
        raise SourceMismatch("разбор моделью выключен в настройках")

    from app.services.agent import llm_configured

    # Спрашиваем заранее: без настроенной модели build_model отдаст скриптованную
    # заглушку под ReAct-цикл агента, и та вернёт нам вызов чужого инструмента.
    if not llm_configured(ctx.rt.dyn):
        raise SourceMismatch("LLM не настроена")
    return ctx.rt


async def _ask_model(dyn: DynamicSettings, sample: str) -> list[Any]:
    """Пары «вопрос — ответ» от модели; не ответила или ответила не JSON — отказ."""
    from app.services.agent import build_model

    timeout = _timeout_s(dyn)
    try:
        from pydantic_ai import Agent

        agent = Agent(build_model(dyn), system_prompt=PROMPT)
        result = await asyncio.wait_for(agent.run(sample), timeout)
    except TimeoutError as exc:
        # Без своего таймаута зависшая модель держит загрузку файла бесконечно
        raise SourceMismatch(f"модель не ответила за {timeout:g} с") from exc
    except Exception as exc:
        raise SourceMismatch(f"модель недоступна: {exc}") from exc

    payload = _json_array(str(result.output))
    if payload is None:
        raise SourceMismatch("модель вернула не JSON-массив")
    if not payload:
        raise SourceMismatch("модель не нашла пар «вопрос — ответ»")
    return payload


@register
class LlmFallbackSource:
    name = "llm_fallback"
    # Отрицательный: при равной уверенности реестр всегда предпочтёт настоящий парсер
    priority = -100

    async def sniff(self, blob: FileBlob) -> float:
        # Чуть выше MIN_CONFIDENCE: берётся только когда не взялся никто другой
        return 0.16

    async def parse(self, blob: FileBlob, ctx: IngestContext) -> AsyncIterator[ArticleRecord]:
        rt = _require_model(ctx)
        sample = _sample_text(blob)
        if not sample.strip():
            raise SourceMismatch("из файла не вышло текста")
        payload = await _ask_model(rt.dyn, sample)
        records = records_from_payload(payload, file=blob.name, source_name=self.name)
        if not records:
            raise SourceMismatch("в ответе модели не нашлось целых пар")
        log.info("Запасной разбор %s моделью: пар %d", blob.name, len(records))
        for record in records:
            yield record
