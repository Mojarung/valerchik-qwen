"""Черновик сценария нейросетью: тексты шагов и формулировки вопроса.

Разделение обязанностей намеренное. Селекторы приносит запись с портала —
модель их не видит и придумывать не должна, иначе подсказка покажет на
несуществующую кнопку. Модель пишет только слова: заголовок сценария, короткие
пояснения к шагам и формулировки, по которым сценарий будут находить.

Без настроенного LLM (демо-режим, офлайн) сервис не падает и не выдумывает:
возвращает записанные заголовки как есть и честно сообщает, что текстов нет.
"""

import json
import logging
import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.runtime import AppRuntime

log = logging.getLogger(__name__)

MAX_QUESTIONS = 5
MAX_TITLE = 120
MAX_TEXT = 600

SYSTEM = (
    "Ты пишешь пошаговые подсказки для помощника на портале поставщиков Москвы "
    "(zakupki.mos.ru). Тебе дают цель сценария и шаги, записанные кликами по "
    "настоящему интерфейсу. Твоя работа — слова, а не разметка: заголовки и "
    "пояснения к шагам плюс формулировки вопроса, по которым сценарий найдут.\n"
    "Правила: по-русски, без Markdown и эмодзи; заголовок шага — повелительное "
    "предложение до 80 знаков («Нажмите «Зарегистрироваться»»); пояснение — одно-два "
    "предложения о том, что произойдёт и на что смотреть; не выдумывай кнопок и "
    "разделов, которых нет в шагах; формулировки вопроса — строчными, без знаков "
    "препинания, так, как спрашивает живой человек.\n"
    "Ответ — строго JSON вида "
    '{"title": "...", "questions": ["..."], "steps": [{"title": "...", "text": "..."}]} '
    "и ничего кроме него. Шагов в ответе ровно столько же, сколько во входных данных."
)


def _payload(goal: str, audience: str, steps: list[dict[str, Any]]) -> str:
    role = {"supplier": "поставщик", "customer": "заказчик"}.get(audience, "любой пользователь")
    lines = [f"Цель сценария: {goal}", f"Кому адресован: {role}", "Шаги (записаны кликами):"]
    for i, step in enumerate(steps, 1):
        where = step.get("url") or "текущая страница"
        lines.append(f"{i}. {step.get('title') or 'без подписи'} — страница {where}")
    return "\n".join(lines)


def _clean_json(raw: str) -> dict[str, Any]:
    """Ответ модели в объект: срезаем ограждения кода и мусор вокруг JSON."""
    text = raw.strip()
    text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.MULTILINE).strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("в ответе модели нет JSON")
    parsed: dict[str, Any] = json.loads(text[start : end + 1])
    return parsed


def merge(steps: list[dict[str, Any]], generated: dict[str, Any]) -> dict[str, Any]:
    """Слова модели поверх записанных шагов: локаторы и переходы не трогаем."""
    written = generated.get("steps") or []
    merged = []
    for index, step in enumerate(steps):
        extra = written[index] if index < len(written) and isinstance(written[index], dict) else {}
        merged.append(
            {
                **step,
                "title": (str(extra.get("title") or step.get("title") or "").strip())[:MAX_TITLE],
                "text": (str(extra.get("text") or step.get("text") or "").strip())[:MAX_TEXT],
            }
        )
    questions = [str(q).strip().lower() for q in (generated.get("questions") or []) if str(q).strip()]
    return {
        "title": str(generated.get("title") or "").strip()[:200],
        "questions": questions[:MAX_QUESTIONS],
        "steps": merged,
    }


async def draft(rt: AppRuntime, goal: str, audience: str, steps: list[dict[str, Any]]) -> dict[str, Any]:
    """Черновик сценария. Ключ `llm` говорит, писала ли тексты модель."""
    base_url = str(rt.dyn.get("llm.base_url") or "").rstrip("/")
    if not base_url or not steps:
        return {
            "llm": False,
            "reason": "Модель не настроена — тексты нужно написать самому",
            **merge(steps, {}),
        }
    try:
        import httpx

        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                f"{base_url}/chat/completions",
                headers={"Authorization": f"Bearer {rt.dyn.get('llm.api_key')}"},
                json={
                    "model": rt.dyn.get("llm.model"),
                    "messages": [
                        {"role": "system", "content": SYSTEM},
                        {"role": "user", "content": _payload(goal, audience, steps)},
                    ],
                    "temperature": 0.4,
                },
            )
            resp.raise_for_status()
            answer = resp.json()["choices"][0]["message"]["content"]
        return {"llm": True, "reason": "", **merge(steps, _clean_json(answer))}
    except Exception:
        log.exception("Нейросеть не сочинила черновик сценария")
        return {
            "llm": False,
            "reason": "Модель не ответила — тексты нужно написать самому",
            **merge(steps, {}),
        }
