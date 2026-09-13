"""Качество ответов службы поддержки по реальной выгрузке СТП — офлайн-отчёт.

В выгрузке нет ни оценок, ни времени, ни имён операторов, а заявители
обезличены. Остаётся текст ответа — по нему и меряем качество донесения
информации: объяснил ли оператор, что делать, или отписался. Виды ответа:

* instruction — объяснили, что делать (инструкция годится и следующему);
* status — сообщили состояние заявки или что сделали за человека
  («На момент проверки УПД в статусе…», «Заказчику направлено приглашение»);
* short — короткая отметка без объяснения («Решено», «Выдано полномочие»);
* redirect — отправили в другую службу («необходимо обратиться в СТП ЕАИСТ»);
* ask — попросили прислать данные или скриншот вместо ответа;
* mass — массовый сбой: «заведена массовая задача, ведутся работы».

«Отписки» — status, short, redirect и ask: человек получил ответ, но не
объяснение, и в следующий раз напишет снова. Отдельно — массовые задачи: одна
причина сотен обращений, самый прямой признак системной проблемы.

Запуск из backend/:
    uv run python -m app.tools.support_quality "Выгрузка СТП за 2026.xlsx" --out report.json
    ... --save  — сохранить отчёт в базу (AnalyticsReport, kind=support_export_quality)
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import statistics
import sys
from collections import Counter, defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.ingest.sources.support_export import (
    classify_answer,
    clean_answer,
    clean_topic,
    pick_question,
    split_description,
)
from app.services import kbkind
from app.services.guardrails import mask_pii
from app.services.wordforms import plural, top_words

KIND = "support_export_quality"

# Виды ответа — в порядке показа
INSTRUCTION, STATUS, SHORT, REDIRECT, ASK, MASS = "instruction", "status", "short", "redirect", "ask", "mass"
KINDS = (INSTRUCTION, STATUS, SHORT, REDIRECT, ASK, MASS)
BRUSH_OFF = frozenset({STATUS, SHORT, REDIRECT, ASK})
LABELS = {
    INSTRUCTION: "объяснили, что делать",
    STATUS: "сообщили статус заявки",
    SHORT: "короткая отметка («Решено»)",
    REDIRECT: "отправили в другую службу",
    ASK: "попросили прислать данные",
    MASS: "массовый сбой",
}

# Короче — отметка о закрытии, а не ответ: тот же порог, что у импорта выгрузки
SHORT_ANSWER_CHARS = 60
# Инструкция вокруг переадресации должна быть инструкцией, а не хвостом фразы
MIN_INSTRUCTION_CHARS = 80
# Тема с меньшим числом обращений — шум выгрузки («ДОП», «М», INTEGRTP-…)
MIN_TOPIC_ROWS = 100
OTHER_TOPIC = "Прочее"
NO_SUBTOPIC = "без подтемы"
TOP_SUBTOPICS = 3
TOP_PROBLEMS = 10
TOP_MASS = 8
# Сколько подтем назвать в «что сделать»: больше трёх задач за раз не берут
TOP_ARTICLES = 3
KEYWORDS = 5
EXAMPLE_CHARS = 200
# pick_question сравнивает обращения группы между собой: выборки хватает
EXAMPLE_SAMPLE = 400
_PERCENT = 100

_SENTENCES = re.compile(r"(?<=[.!?])\s+")
# Переадресация: прямой совет обратиться в другое место. Одно упоминание ЕАИСТ
# или ЕИС — ещё не переадресация: «цены поступают из ЕАИСТ» объясняет причину.
_REDIRECT = re.compile(
    r"обрат(?:итесь|иться|ятся)\b(?:\s+[\w«»\"-]+){0,5}?\s+(?:в|во|к|на)\s"
    r"|адрес(?:уйте|овать|уется)\s+(?:[\w«»\"-]+\s+){0,3}?(?:в|к)\s"
    r"|(?:не|вне)\s+(?:относится\s+к\s+)?компетенци"
    r"|не\s+(?:является|входит)\s+(?:зоной|в\s+зону)\s+ответственности",
    re.IGNORECASE,
)
# Встречная просьба вместо ответа; просьба «на случай» ответом её не делает
_ASK = re.compile(
    r"(?:просьба|просим)\s+(?:предоставить|прислать|направить|уточнить|сообщить|указать|приложить)"
    r"|предоставьте|пришлите|приложите|уточните,?\s+пожалуйста|направьте\s+(?:нам\s+)?(?:скриншот|ссылку|номер|файл)",
    re.IGNORECASE,
)
_CONDITIONAL = re.compile(r"^(?:в случае|если|при (?:повтор|возобнов|сохранен|возникновен))", re.IGNORECASE)
_MASS = re.compile(
    r"массов\w*\s+(?:задач|проблем|инцидент|сбо|ошибк)|ведутся\s+(?:технические\s+)?работы|известн\w+\s+проблем",
    re.IGNORECASE,
)
_MASS_NUMBER = re.compile(r"массов\w*\s+задач\w*\s*(?:с\s+номером\s*)?(?:№\s*)?(\d{6,})", re.IGNORECASE)
_MASS_NUMBER_AFTER = re.compile(r"задач\w*\s*(?:№\s*)?(\d{6,})[^.]{0,60}?массов", re.IGNORECASE)


def _asks_back(sentences: Iterable[str]) -> bool:
    return any(_ASK.search(s) and not _CONDITIONAL.match(s.strip()) for s in sentences)


def answer_kind(answer: str) -> str:
    """Вид ответа оператора: инструкция, отписка какого-то рода или массовый сбой."""
    sentences = [s for s in _SENTENCES.split(answer.strip()) if s]
    if len(answer) < SHORT_ANSWER_CHARS:
        return ASK if _asks_back(sentences) else SHORT
    if _MASS.search(answer):
        return MASS
    redirects = [s for s in sentences if _REDIRECT.search(s)]
    if redirects:
        # «Для снятия КС… выполните шаги 1–3. По вопросам статуса обратитесь к
        # заказчику» — это инструкция с адресом, а не переадресация
        rest = " ".join(s for s in sentences if s not in redirects)
        if len(rest) >= MIN_INSTRUCTION_CHARS and classify_answer(rest)[0] == kbkind.ANSWER:
            return INSTRUCTION
        return REDIRECT
    if classify_answer(answer)[0] == kbkind.ANSWER:
        return INSTRUCTION
    return ASK if _asks_back(sentences) else STATUS


def mass_number(answer: str) -> str | None:
    """Номер массовой задачи из ответа: «заведена массовая задача 33550005»."""
    found = _MASS_NUMBER.search(answer) or _MASS_NUMBER_AFTER.search(answer)
    return found.group(1) if found else None


def is_incident(impact: str) -> bool:
    """«Услуга недоступна» — инцидент; RFI и RFC — запросы (как при импорте выгрузки)."""
    head = impact.strip().casefold()
    return bool(head) and not head.startswith(("rfi", "rfc"))


@dataclass(frozen=True, slots=True)
class Row:
    """Одна строка выгрузки, уже разобранная."""

    topic: str
    subtopic: str
    text: str
    answer: str
    kind: str
    incident: bool
    mass: str | None


def parse_rows(
    records: Iterable[dict[str, str]],
    min_topic_rows: int = MIN_TOPIC_ROWS,
    taxonomy: Iterable[str] = (),
) -> list[Row]:
    """Строки выгрузки → разобранные обращения.

    Тема — из справочника тем, если он дан: в выгрузке рядом с десятью темами
    живут «ДОП», «Контракты (44-ФЗ)» и номера задач, и порогом по числу строк их
    не отсечь — «ДОП» встречается чаще, чем законная «Блокировка личного
    кабинета». Без справочника редкие темы сливаются в «Прочее» по порогу.
    """
    known = {clean_topic(t).casefold(): clean_topic(t) for t in taxonomy if clean_topic(t)}
    raw = []
    for record in records:
        answer = clean_answer(str(record.get("Решение") or ""))
        subtopic, text = split_description(str(record.get("Описание") or ""))
        raw.append(
            (
                clean_topic(str(record.get("Тема") or "")),
                subtopic,
                text,
                answer,
                str(record.get("Влияние") or ""),
            )
        )
    sizes = Counter(topic for topic, *_ in raw)

    def canonical(topic: str) -> str:
        if known:
            return known.get(topic.casefold(), OTHER_TOPIC)
        return topic if topic and sizes[topic] >= min_topic_rows else OTHER_TOPIC

    return [
        Row(
            topic=canonical(topic),
            subtopic=subtopic or NO_SUBTOPIC,
            text=text,
            answer=answer,
            kind=answer_kind(answer),
            incident=is_incident(impact),
            mass=mass_number(answer),
        )
        for topic, subtopic, text, answer, impact in raw
    ]


def _share(hits: int, total: int) -> float:
    return round(hits / total, 3) if total else 0.0


def _example(texts: Sequence[str], answer_hint: str = "") -> str:
    """Типичное обращение группы — без номеров заявок и персональных данных."""
    question = pick_question(list(texts[:EXAMPLE_SAMPLE]), answer_hint)
    return mask_pii(question)[:EXAMPLE_CHARS]


def _problem(topic: str, subtopic: str, rows: Sequence[Row]) -> dict[str, Any]:
    kinds = Counter(r.kind for r in rows)
    texts = [r.text for r in rows]
    return {
        "topic": topic,
        "subtopic": subtopic,
        "count": len(rows),
        "brush_off_share": _share(sum(kinds[k] for k in BRUSH_OFF), len(rows)),
        "mass_share": _share(kinds[MASS], len(rows)),
        "keywords": top_words(texts[: EXAMPLE_SAMPLE * 5], KEYWORDS),
        "example": _example(texts),
    }


def _topic(topic: str, rows: Sequence[Row], total: int) -> dict[str, Any]:
    kinds = Counter(r.kind for r in rows)
    by_subtopic: dict[str, list[Row]] = defaultdict(list)
    for r in rows:
        by_subtopic[r.subtopic].append(r)
    biggest = sorted(by_subtopic.items(), key=lambda item: -len(item[1]))[:TOP_SUBTOPICS]
    return {
        "topic": topic,
        "count": len(rows),
        "share": _share(len(rows), total),
        "explained_share": _share(kinds[INSTRUCTION], len(rows)),
        "brush_off_share": _share(sum(kinds[k] for k in BRUSH_OFF), len(rows)),
        "redirect_share": _share(kinds[REDIRECT], len(rows)),
        "ask_share": _share(kinds[ASK], len(rows)),
        "status_share": _share(kinds[STATUS], len(rows)),
        "short_share": _share(kinds[SHORT], len(rows)),
        "mass_share": _share(kinds[MASS], len(rows)),
        "incident_share": _share(sum(1 for r in rows if r.incident), len(rows)),
        "median_answer_chars": round(statistics.median(len(r.answer) for r in rows)) if rows else 0,
        "top_subtopics": [_problem(topic, sub, items) for sub, items in biggest],
    }


def _mass_incidents(rows: Sequence[Row]) -> list[dict[str, Any]]:
    by_number: dict[str, list[Row]] = defaultdict(list)
    for r in rows:
        if r.mass:
            by_number[r.mass].append(r)
    incidents = []
    for number, items in sorted(by_number.items(), key=lambda item: -len(item[1]))[:TOP_MASS]:
        answer = Counter(r.answer for r in items).most_common(1)[0][0]
        incidents.append(
            {
                "number": number,
                "count": len(items),
                "topics": [t for t, _ in Counter(r.topic for r in items).most_common(3)],
                "example": mask_pii(answer)[:EXAMPLE_CHARS],
            }
        )
    return incidents


def build_report(rows: Sequence[Row], source: str = "") -> dict[str, Any]:
    """Отчёт по выгрузке: виды ответов в целом и по темам, частые проблемы, массовые сбои."""
    total = len(rows)
    kinds = Counter(r.kind for r in rows)
    by_topic: dict[str, list[Row]] = defaultdict(list)
    by_subtopic: dict[tuple[str, str], list[Row]] = defaultdict(list)
    for r in rows:
        by_topic[r.topic].append(r)
        if r.subtopic != NO_SUBTOPIC:
            by_subtopic[(r.topic, r.subtopic)].append(r)
    topics = [
        _topic(topic, items, total) for topic, items in sorted(by_topic.items(), key=lambda i: -len(i[1]))
    ]
    # «Прочее» — сборная солянка: в таблице тем её место последнее
    topics.sort(key=lambda t: (t["topic"] == OTHER_TOPIC, -t["count"]))
    common = sorted(by_subtopic.items(), key=lambda item: -len(item[1]))[:TOP_PROBLEMS]
    report: dict[str, Any] = {
        "generated_at": datetime.now(UTC).isoformat(),
        "source": source,
        "total": total,
        "answer_kinds": {kind: kinds[kind] for kind in KINDS},
        "explained_share": _share(kinds[INSTRUCTION], total),
        "brush_off_share": _share(sum(kinds[k] for k in BRUSH_OFF), total),
        "mass_share": _share(kinds[MASS], total),
        "incident_share": _share(sum(1 for r in rows if r.incident), total),
        "median_answer_chars": round(statistics.median(len(r.answer) for r in rows)) if rows else 0,
        "topics": topics,
        "problems": [_problem(topic, sub, items) for (topic, sub), items in common],
        "mass_incidents": _mass_incidents(rows),
        "mass_task_count": len({r.mass for r in rows if r.mass}),
        "notes": [
            "В выгрузке нет оценок, времени ответа и имён операторов, а заявители обезличены. "
            "Поэтому качество меряем по тексту ответа: объяснил ли оператор, что делать.",
            "Повторные обращения одного заявителя по выгрузке не посчитать — заявитель скрыт.",
        ],
    }
    report["conclusion"] = compose_conclusion(report)
    return report


def _pct(value: float) -> str:
    return f"{round(value * _PERCENT)}%"


def compose_conclusion(report: dict[str, Any]) -> str:
    """Выводы по выгрузке простыми словами: доли, худшие темы, частые проблемы, сбои."""
    total = report["total"]
    if not total:
        return "Выгрузка пустая — выводов нет."
    kinds = report["answer_kinds"]
    parts = [
        f"Разобрано {total} {plural(total, ('обращение', 'обращения', 'обращений'))} из выгрузки СТП. "
        f"Понятную инструкцию получили {_pct(report['explained_share'])}, "
        f"отписку — {_pct(report['brush_off_share'])}: "
        + ", ".join(
            f"{LABELS[k]} — {_pct(kinds[k] / total)}" for k in (SHORT, STATUS, REDIRECT, ASK) if kinds.get(k)
        )
        + ".",
    ]
    ranked = [t for t in report["topics"] if t["topic"] != OTHER_TOPIC]
    worst = max(ranked, key=lambda t: t["brush_off_share"], default=None)
    best = max(ranked, key=lambda t: t["explained_share"], default=None)
    if worst and best and worst is not best:
        parts.append(
            f"Хуже всего объясняют в теме «{worst['topic']}»: отписка в "
            f"{_pct(worst['brush_off_share'])} ответов. "
            f"Лучше всего — «{best['topic']}»: инструкция в {_pct(best['explained_share'])}."
        )
    if report["problems"]:
        top = ", ".join(f"«{p['subtopic']}» ({p['count']})" for p in report["problems"][:3])
        parts.append(f"Чаще всего пишут про: {top}.")
    if report["mass_incidents"]:
        m = report["mass_incidents"][0]
        tasks = report.get("mass_task_count") or len(report["mass_incidents"])
        failures = plural(tasks, ("массовый сбой", "массовых сбоя", "массовых сбоев"))
        parts.append(
            f"Операторы сослались на {tasks} {failures}; "
            f"крупнейший — задача {m['number']}: {m['count']} "
            f"{plural(m['count'], ('обращение', 'обращения', 'обращений'))} с одной причиной."
        )
    # Статьи — в первую очередь по подтемам с наибольшим числом отписок в штуках:
    # там одна статья избавит от объяснений больше всего людей
    ranked = sorted(report["problems"], key=lambda p: -p["count"] * p["brush_off_share"])
    targets = [p for p in ranked if _brush_offs(p)][:TOP_ARTICLES]
    where = (
        "по подтемам "
        + ", ".join(f"«{p['subtopic']}» (отписка в {_brush_offs(p)} из {p['count']})" for p in targets)
        if targets
        else "по частым вопросам"
    )
    parts.append(
        f"Что сделать: написать статьи базы знаний с пошаговым ответом {where} — тогда человек "
        "получит объяснение сразу, а не отписку; о массовых сбоях сообщать баннером на портале, "
        "а не в каждом ответе."
    )
    return "\n".join(parts)


def _brush_offs(problem: dict[str, Any]) -> int:
    return round(float(problem["count"]) * float(problem["brush_off_share"]))


def read_export(path: Path) -> list[dict[str, str]]:
    """Строки первого листа выгрузки как словари «колонка → текст»."""
    import pandas as pd

    frame = pd.read_excel(path, dtype=str).fillna("")
    return [{str(k): str(v) for k, v in row.items()} for row in frame.to_dict(orient="records")]


def read_taxonomy(path: Path) -> list[str]:
    """Темы из справочника «Темы_подтемы_обращений.xlsx».

    Тема стоит в строке своей первой подтемы, ниже — пусто; колонку ищем по
    заголовку «Тема обращений», а не по номеру: справочник правят руками.
    """
    import pandas as pd

    frame = pd.read_excel(path, header=None, dtype=str).fillna("")
    for row_index, row in frame.iterrows():
        for column, cell in row.items():
            if str(cell).strip().casefold().startswith("тема обращ"):
                below = frame.loc[frame.index > row_index, column]
                return [t for t in dict.fromkeys(str(v).strip() for v in below) if t]
    return []


async def save_report(report: dict[str, Any], database_url: str) -> None:
    """Сохранить отчёт в AnalyticsReport — админка покажет его на странице отзывов."""
    from app.db import Database
    from app.models import AnalyticsReport

    db = Database(database_url)
    try:
        await db.create_all()
        async with db.session_factory() as session:
            session.add(AnalyticsReport(kind=KIND, payload=report))
            await session.commit()
    finally:
        await db.dispose()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Качество ответов по выгрузке СТП")
    parser.add_argument("export", type=Path, help="выгрузка СТП (.xlsx)")
    parser.add_argument("--out", type=Path, help="куда записать JSON-отчёт")
    parser.add_argument(
        "--taxonomy", type=Path, help="справочник тем (.xlsx): остальные темы уйдут в «Прочее»"
    )
    parser.add_argument(
        "--min-topic", type=int, default=MIN_TOPIC_ROWS, help="без справочника: реже — в «Прочее»"
    )
    parser.add_argument("--save", action="store_true", help="сохранить отчёт в базу приложения")
    parser.add_argument("--database-url", default="", help="база для --save; пусто — из настроек приложения")
    args = parser.parse_args(argv)

    taxonomy = read_taxonomy(args.taxonomy) if args.taxonomy else []
    rows = parse_rows(read_export(args.export), args.min_topic, taxonomy)
    report = build_report(rows, source=args.export.name)
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.out:
        args.out.write_text(text + "\n", encoding="utf-8")
    print(report["conclusion"])
    if args.save:
        from app.config import get_settings

        asyncio.run(save_report(report, args.database_url or get_settings().effective_database_url))
        print("Отчёт сохранён в базу", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
