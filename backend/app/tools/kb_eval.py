"""Замер поиска на реальных обращениях службы поддержки.

    uv run python -m app.tools.kb_eval split <выгрузка.xlsx> <каталог> [--cases 400] [--seed 42]
    uv run python -m app.tools.kb_eval run <cases.json> [--e2e] [--out отчёт.json]

Золотой набор (seed/golden.json) проверяет перефразы к синтетическим статьям, то
есть меряет, насколько поиск узнаёт то, что мы сами же написали. Выгрузка СТП —
двадцать пять тысяч обращений живых людей и ответы операторов на них: опечатки,
приветствия, номера заявок вместо слов. Меряться надо на ней.

Разбиение честное. Обращение из теста не попадает в базу знаний ни вопросом, ни
ключевой формулировкой: split выкладывает train.xlsx без тестовых строк, базу
собирают из него, а спрашивают тестовыми. Иначе поиск «узнавал» бы собственный
вопрос статьи, и метрика мерила бы память, а не понимание.

Эталон — текст ответа оператора, а не id статьи: id меняются при каждом
переимпорте, а ответ, данный заявителю, — нет. Совпадение считается по словам
ответа с допуском: один и тот же ответ оператор копирует с разной пунктуацией.

Судья здесь свой и намеренно не делит код с импортом. Правило «этот ответ —
отписка про конкретную заявку, а не инструкция» живёт и в разборе выгрузки, но
мерить разбор его же правилом значило бы ставить себе оценки самому.
"""

from __future__ import annotations

import argparse
import asyncio
import io
import json
import random
import re
import statistics
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from app.ingest.sources.support_export import _Appeal, _Layout, answer_key

if TYPE_CHECKING:
    from app.runtime import AppRuntime

# Обращение короче — «ошибка», «см. скрин»: по нему не спросит и человек в чате.
# Длиннее — рассказ на абзац, который в чат-бот так не пишут.
CASE_MIN_CHARS = 15
CASE_MAX_CHARS = 300
# Группа ответа должна пережить изъятие тестовой строки: статья из выгрузки
# появляется от двух употреблений ответа, значит в группе нужно хотя бы три.
MIN_GROUP_FOR_TEST = 3
DEFAULT_CASES = 400
TOP_K = 5
# Слова ответа совпадают на столько — это тот же ответ с другой пунктуацией или
# опечаткой оператора, а не другой ответ.
SAME_ANSWER_JACCARD = 0.85

# Вопросы не про портал: на них правильный ответ — «не знаю» и оператор. Смесь
# бытовых, соседних госуслуг и провокаций: соседние темы опаснее бытовых, у них
# общие слова с базой («налоговый», «договор», «ИНН»).
OUT_OF_DOMAIN = (
    "какая погода завтра в Нижнем Новгороде",
    "как получить налоговый вычет за лечение",
    "как записаться к врачу через госуслуги",
    "сколько стоит доставка пиццы",
    "как оформить загранпаспорт",
    "как испечь блины на молоке",
    "кто выиграл чемпионат мира по футболу",
    "как заменить масло в двигателе",
    "посоветуй фильм на вечер",
    "как оформить пособие по уходу за ребенком",
    "как получить права после лишения",
    "где купить билеты на поезд до Казани",
    "как написать резюме программисту",
    "какой курс доллара сегодня",
    "как подать заявление в загс онлайн",
    "как вылечить простуду за один день",
    "как оплатить штраф гибдд",
    "сколько дней отпуска положено по трудовому кодексу",
    "как зарегистрировать автомобиль в гибдд",
    "как получить полис омс",
    "как поменять тариф мобильной связи",
    "где посмотреть кадастровую стоимость квартиры",
    "как взять ипотеку с господдержкой",
    "расскажи анекдот",
    "как стать блогером",
    "какие документы нужны для открытия ИП в налоговой",
    "как закрыть ИП через госуслуги",
    "как узнать задолженность по налогам по ИНН",
    "как заключить договор аренды квартиры",
    "как вернуть товар в магазин без чека",
    "почему не работает wifi дома",
    "как восстановить пароль от почты mail.ru",
    "как установить windows 11",
    "где найти вакансии бухгалтера в Москве",
    "как оформить субсидию на оплату жку",
    "что такое биткоин",
    "как настроить принтер hp",
    "сколько калорий в банане",
    "как получить выписку из егрн",
    "напиши стихотворение про осень",
)

_TAG = re.compile(r"</?[a-z]+(?:\s[^>]*)?>|<[cm]\s#[0-9a-f]+>", re.IGNORECASE)
_ESCAPE = re.compile(r"\\([\\`*_{}\[\]()#+\-.!|<>])")
_WORD = re.compile(r"[а-яёa-z0-9]{3,}")
# Отписка про конкретную заявку: оператор сообщил состояние или сделал действие
# за заявителя. Такой текст правильный для одного человека и вредный для всех
# остальных — на такие обращения правильно отвечает оператор, а не статья.
_SITUATIONAL = re.compile(
    r"на момент проверки|в настоящ(?:ий|ее) (?:момент|время)|^(?:добр\w+ \w+[.!,]?\s*)?"
    r"(?:исправлен|ошибка исправлен|проблема устранен|решено|выполнено|контракт заключ|"
    r"направлен|отправлен|внесен|внесён|заведен|актуализирован|удален|изменен|подписан)"
    r"|просьба (?:предоставить|прислать|направить)|предоставьте|пришлите|\b\d{7,}\b",
    re.IGNORECASE,
)


_GREETING = re.compile(r"^\s*(?:добр\w*\s+(?:день|утро|вечер)|здравствуйте)[\s!.,]*", re.IGNORECASE)
# Статья короче — слишком мало слов, чтобы «содержится в эталоне» что-то значило
CONTAINED_MIN_WORDS = 8
CONTAINED_SHARE = 0.9


def normalize_answer(text: str) -> str:
    """Ответ для сравнения: без разметки тикет-системы, экранирования, приветствия и регистра."""
    text = _ESCAPE.sub(r"\1", _TAG.sub(" ", text or ""))
    text = text.replace("**", " ").replace("ё", "е").replace("Ё", "Е")
    text = _GREETING.sub("", " ".join(text.split()), count=1)
    return text.strip(" .").casefold()


def same_answer(first: str, second: str) -> bool:
    """Один и тот же ответ оператора, пусть и с другой пунктуацией.

    Засчитывается и вложение: статья может быть частью эталона, из которой при
    импорте убрали отчёт про одну заявку («На момент проверки… Необходимо…» →
    «Необходимо…»). Человеку отдали ту самую инструкцию, что и оператор.
    """
    a, b = normalize_answer(first), normalize_answer(second)
    if not a or not b:
        return False
    if a == b:
        return True
    left, right = set(_WORD.findall(a)), set(_WORD.findall(b))
    if not left or not right:
        return False
    if len(left & right) / len(left | right) >= SAME_ANSWER_JACCARD:
        return True
    shorter = min(left, right, key=len)
    return len(shorter) >= CONTAINED_MIN_WORDS and len(left & right) / len(shorter) >= CONTAINED_SHARE


def is_situational(answer: str) -> bool:
    """Отписка про конкретную заявку, а не инструкция на всех (правило судьи)."""
    return bool(_SITUATIONAL.search(normalize_answer(answer)))


# --- split ---


def _read_export(path: Path) -> Any:
    import pandas as pd

    book = pd.read_excel(io.BytesIO(path.read_bytes()), sheet_name=None, dtype=str)
    for name, frame in book.items():
        frame = frame.fillna("")
        if _Layout.of([str(column) for column in frame.columns]) is not None:
            return str(name), frame
    raise SystemExit(f"{path.name}: не похоже на выгрузку поддержки (нет колонок Тема/Описание/Решение)")


def split(export: Path, out_dir: Path, cases: int, seed: int) -> dict[str, Any]:
    """Выгрузка → train.xlsx (для базы) и cases.json (для вопросов)."""
    sheet, frame = _read_export(export)
    layout = _Layout.of([str(column) for column in frame.columns])
    assert layout is not None  # проверено в _read_export
    rows = [[str(cell) for cell in row] for row in frame.values.tolist()]

    groups: dict[str, list[tuple[int, _Appeal]]] = {}
    for index, cells in enumerate(rows):
        appeal = _Appeal.read(index + 1, cells, layout)
        if appeal is not None:
            groups.setdefault(answer_key(appeal.answer), []).append((index, appeal))

    rng = random.Random(seed)
    eligible = [group for group in groups.values() if len(group) >= MIN_GROUP_FOR_TEST]
    rng.shuffle(eligible)
    picked: list[dict[str, Any]] = []
    held_out: set[int] = set()
    for group in eligible:
        if len(picked) >= cases:
            break
        texts = [appeal.text for _, appeal in group]
        candidates = [
            (index, appeal)
            for index, appeal in group
            if CASE_MIN_CHARS <= len(appeal.text) <= CASE_MAX_CHARS and texts.count(appeal.text) == 1
        ]
        if not candidates:
            continue
        index, appeal = rng.choice(candidates)
        held_out.add(index)
        picked.append(
            {
                "q": appeal.text,
                "topic": appeal.topic,
                "subtopic": appeal.subtopic,
                "expect": appeal.answer,
                "group_size": len(group),
            }
        )

    out_dir.mkdir(parents=True, exist_ok=True)
    train = frame.drop(frame.index[sorted(held_out)])
    train_path = out_dir / "train.xlsx"
    train.to_excel(train_path, sheet_name=sheet, index=False)
    meta = {
        "export": export.name,
        "seed": seed,
        "rows": len(rows),
        "train_rows": len(train),
        "cases": len(picked),
        "groups_total": len(groups),
        "groups_eligible": len(eligible),
    }
    (out_dir / "cases.json").write_text(
        json.dumps({"meta": meta, "cases": picked}, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    return meta


# --- run ---


@dataclass
class CaseResult:
    kind: str  # kb | operator | ood
    q: str
    rank: int | None  # место правильного ответа в выдаче, 1-based
    top_score: float
    answered: bool
    top_situational: bool
    latency_ms: float
    top_question: str = ""
    e2e: dict[str, Any] = field(default_factory=dict)


def _load_cases(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return list(payload.get("cases") or [])


def _has_fragments(text: str, fragments: list[str]) -> bool:
    """Все эталонные фрагменты есть в тексте — с точностью до регистра, ё и пробелов."""
    haystack = normalize_answer(text)
    return all(normalize_answer(fragment) in haystack for fragment in fragments)


def _matches(doc: Any, case: dict[str, Any]) -> bool:
    """Эталон экспертного вопроса — фрагменты, эталон обращения — ответ оператора."""
    fragments = case.get("fragments") or []
    if fragments:
        return _has_fragments(f"{doc.question} {doc.answer}", fragments)
    expect = case.get("expect") or ""
    return bool(expect) and same_answer(doc.answer, expect)


async def _judge(rt: AppRuntime, case: dict[str, Any], kind: str, threshold: float) -> CaseResult:
    started = time.perf_counter()
    docs = await rt.retriever.search(case["q"])
    latency = (time.perf_counter() - started) * 1000
    rank = next((i for i, doc in enumerate(docs[:TOP_K], start=1) if _matches(doc, case)), None)
    top_score = max((doc.score for doc in docs), default=0.0)
    return CaseResult(
        kind=kind,
        q=case["q"],
        rank=rank,
        top_score=round(top_score, 4),
        answered=top_score >= threshold,
        top_situational=bool(docs) and is_situational(docs[0].answer),
        latency_ms=round(latency, 1),
        top_question=docs[0].question[:120] if docs else "",
    )


async def _e2e(rt: AppRuntime, text: str) -> dict[str, Any]:
    """Весь конвейер диалога: уточнение, дословный ответ, агент, отказ."""
    session = await rt.chat.create_session(channel="api")
    reply = await rt.chat.handle_user_message(session["session_id"], text)
    return {
        "status": reply.get("status"),
        "answer": (reply.get("answer") or "")[:600],
        "escalated": bool(reply.get("escalated")),
        "options": len(reply.get("options") or []),
    }


def _share(part: int, whole: int) -> float | None:
    return round(part / whole, 3) if whole else None


def summarize(results: list[CaseResult]) -> dict[str, Any]:
    kb = [r for r in results if r.kind == "kb"]
    operator = [r for r in results if r.kind == "operator"]
    ood = [r for r in results if r.kind == "ood"]
    latencies = sorted(r.latency_ms for r in results)

    def hits(items: list[CaseResult], k: int) -> float | None:
        return _share(sum(1 for r in items if r.rank is not None and r.rank <= k), len(items))

    expert = [r for r in results if r.kind == "expert"]
    metrics: dict[str, Any] = {
        "expert_cases": len(expert),
        "expert_hit@1": hits(expert, 1),
        "expert_hit@3": hits(expert, 3),
        "expert_answer_rate": _share(sum(1 for r in expert if r.answered), len(expert)),
        "kb_cases": len(kb),
        "hit@1": hits(kb, 1),
        "hit@3": hits(kb, 3),
        "hit@5": hits(kb, 5),
        "mrr": round(statistics.fmean(1 / r.rank if r.rank else 0.0 for r in kb), 3) if kb else None,
        "answer_rate": _share(sum(1 for r in kb if r.answered), len(kb)),
        # Уверенно и мимо: порог пройден, а первым стоит чужой ответ
        "confident_wrong": _share(sum(1 for r in kb if r.answered and r.rank != 1), len(kb)),
        "operator_cases": len(operator),
        # На обращение, которое решает оператор, бот уверенно выдал чужую отписку
        "operator_answered_with_status": _share(
            sum(1 for r in operator if r.answered and r.top_situational), len(operator)
        ),
        "ood_cases": len(ood),
        "ood_false_answer": _share(sum(1 for r in ood if r.answered), len(ood)),
        "latency_p50_ms": latencies[len(latencies) // 2] if latencies else None,
        "latency_p95_ms": latencies[int(len(latencies) * 0.95)] if latencies else None,
    }
    e2e = [r for r in results if r.e2e]
    if e2e:
        statuses: dict[str, int] = {}
        for r in e2e:
            status = str(r.e2e.get("status"))
            statuses[f"{r.kind}:{status}"] = statuses.get(f"{r.kind}:{status}", 0) + 1
        metrics["e2e_statuses"] = dict(sorted(statuses.items()))
        metrics["e2e_kb_exact"] = _share(
            sum(1 for r in e2e if r.kind == "kb" and r.e2e.get("status") == "answered" and r.e2e.get("hit")),
            sum(1 for r in e2e if r.kind == "kb"),
        )
    return metrics


async def run(
    cases_path: Path, *, e2e: bool, limit: int | None, expert_path: Path | None = None
) -> dict[str, Any]:
    from app.config import get_settings
    from app.runtime import AppRuntime

    rt = AppRuntime(get_settings())
    await rt.start()
    try:
        threshold = float(rt.dyn.get("agent.confidence_threshold"))
        cases = _load_cases(cases_path)[:limit] if limit else _load_cases(cases_path)
        jobs: list[tuple[str, dict[str, Any]]] = [
            ("operator" if is_situational(case["expect"]) else "kb", case) for case in cases
        ]
        if expert_path:
            jobs += [("expert", case) for case in _load_cases(expert_path)]
        jobs += [("ood", {"q": text}) for text in OUT_OF_DOMAIN]
        results: list[CaseResult] = []
        for kind, case in jobs:
            result = await _judge(rt, case, kind, threshold)
            if e2e:
                result.e2e = await _e2e(rt, case["q"])
                fragments = case.get("fragments") or []
                result.e2e["hit"] = (
                    _has_fragments(result.e2e["answer"], fragments)
                    if fragments
                    else bool(case.get("expect"))
                    and same_answer(result.e2e["answer"], case.get("expect", ""))
                )
            results.append(result)
        report = {
            "threshold": threshold,
            "reranker": bool(rt.dyn.get("retrieval.use_reranker")),
            "metrics": summarize(results),
            "misses": [
                {"q": r.q, "kind": r.kind, "score": r.top_score, "top": r.top_question, "e2e": r.e2e}
                for r in results
                if (r.kind == "kb" and r.rank != 1) or (r.kind == "ood" and r.answered)
            ][:120],
        }
    finally:
        await rt.stop()
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="command", required=True)
    split_cmd = sub.add_parser("split", help="выгрузка → train.xlsx + cases.json")
    split_cmd.add_argument("export", type=Path)
    split_cmd.add_argument("out_dir", type=Path)
    split_cmd.add_argument("--cases", type=int, default=DEFAULT_CASES)
    split_cmd.add_argument("--seed", type=int, default=42)
    run_cmd = sub.add_parser("run", help="прогон вопросов по поднятой базе знаний")
    run_cmd.add_argument("cases", type=Path)
    run_cmd.add_argument("--e2e", action="store_true", help="ещё и через весь конвейер диалога")
    run_cmd.add_argument("--limit", type=int)
    run_cmd.add_argument("--expert", type=Path, help="экспертные вопросы с обязательными фрагментами ответа")
    run_cmd.add_argument("--out", type=Path)
    args = parser.parse_args(argv)

    if args.command == "split":
        meta = split(args.export, args.out_dir, args.cases, args.seed)
        print(json.dumps(meta, ensure_ascii=False, indent=1))
        return 0

    report = asyncio.run(run(args.cases, e2e=args.e2e, limit=args.limit, expert_path=args.expert))
    text = json.dumps(report, ensure_ascii=False, indent=1)
    if args.out:
        args.out.write_text(text, encoding="utf-8")
    print(json.dumps(report["metrics"], ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
