"""Авто-регресс качества ответов: золотой набор вопросов и метрики по нему.

Без такой проверки любая правка промпта, порогов или базы знаний — гадание:
стало лучше или мы только что уронили половину ответов, видно не будет, пока не
пожалуются люди.

Что меряем и почему именно это. Совпадение по id статьи не годится: при повторном
разборе методички статьи пересоздаются, id и формулировки едут, и «эталон» протух
бы после первого же импорта. Поэтому эталон — устойчивые фрагменты, которые обязаны
оказаться В ОТВЕТЕ. Это заодно и честнее: человеку важно, что ему сказали, а не
какая статья нашлась.

Отдельно считаем долю уверенных ошибок. Она опаснее доли ненайденных: «не знаю»
человек перепроверит, а уверенный неправильный ответ примет на веру.
"""

import asyncio
import json
import logging
import statistics
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from sqlalchemy import select

from app.models import AnalyticsReport
from app.rag.agent import run_support_agent
from app.services.agent import AgentRequest

if TYPE_CHECKING:
    from app.dynamic_settings import DynamicSettings
    from app.runtime import AppRuntime

log = logging.getLogger(__name__)

REPORT_KIND = "eval"
GOLDEN_PATH = Path(__file__).resolve().parent.parent / "seed" / "golden.json"
# Набор по выгрузке СТП: реальные обращения с эталонами из ответов операторов.
# Меряет базу, собранную из выданных данных, поэтому на демо-базе не гоняется —
# какой набор прогонять, решает настройка eval.question_set.
STP_PATH = GOLDEN_PATH.with_name("golden_stp.json")
# Версия 2 — те же темы, что у base, но эталоны сверены со статьями боевой базы
# (инструкции и выгрузка СТП), плюс набор СТП. Base остаётся для демо-базы и тестов.
V2_PATH = GOLDEN_PATH.with_name("golden_v2.json")
QUESTION_SETS = {
    "base": (GOLDEN_PATH,),
    "stp": (STP_PATH,),
    "all": (GOLDEN_PATH, STP_PATH),
    "v2": (V2_PATH, STP_PATH),
}
PROGRESS_KEY = "eval:progress"
PROGRESS_TTL = 3600
# Набор по выгрузке СТП — тысячи вопросов, и разбирать нужно каждый промах, а не
# первые сорок: остальные раньше пропадали из отчёта молча.
MISSES_LIMIT = 5000


def _norm(text: str) -> str:
    """ё и Ё приравниваем к е: в базе знаний «трёх», в вопросе «трех», и без этого
    эталон срабатывал бы через раз в зависимости от того, кто печатал."""
    return text.lower().replace("ё", "е")


def load_questions(dyn: DynamicSettings) -> list[dict[str, Any]]:
    """Набор из репозитория плюс дописанное админом.

    Файл в git — источник истины: он переживает пересоздание томов и виден в
    истории изменений вместе с кодом, который меряет. Дополнения живут в
    настройках: заводить ради них CRUD-раздел было бы дороже пользы, а
    редактор JSON в админке уже есть.
    """
    questions: list[dict[str, Any]] = []
    for path in QUESTION_SETS.get(str(dyn.get("eval.question_set")), QUESTION_SETS["base"]):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            questions.extend(payload.get("questions") or [])
        except OSError, ValueError:
            log.exception("Золотой набор не прочитан: %s", path)

    for extra in dyn.get("eval.extra_questions") or []:
        if isinstance(extra, dict) and extra.get("q"):
            questions.append(extra)
    questions = [q for q in questions if isinstance(q.get("q"), str) and q["q"].strip()]
    # Каждый N-й вопрос: полный набор СТП на медленной модели идёт часами, а срез
    # через шаг сохраняет пропорции тем — вопросы в файле идут подряд по темам
    step = max(1, int(dyn.get("eval.sample_every") or 1))
    return questions[::step]


def score_answer(answer: str, expect: list[str]) -> bool:
    """Попадание — когда в ответе есть все ожидаемые фрагменты.

    Все, а не любой: фрагменты подобраны как признаки одного правильного ответа,
    и засчитывать частичное совпадение значило бы завышать метрику там, где бот
    угадал тему, но не сказал главного.
    """
    if not expect:
        return bool(answer.strip())
    haystack = _norm(answer)
    return all(_norm(str(fragment)) in haystack for fragment in expect)


async def _ask(rt: AppRuntime, question: dict[str, Any], threshold: float) -> dict[str, Any]:
    """Один вопрос мимо диалогов.

    Прогон принципиально не идёт через handle_user_message: тот пишет сессию и
    сообщения в базу, и полсотни служебных вопросов после каждого прогона
    оседали бы в статистике дашборда как реальные обращения людей.
    """
    started = time.perf_counter()
    outcome = await run_support_agent(
        rt.dyn,
        rt.retriever,
        rt.classifier,
        AgentRequest(question["q"], timeout_s=float(rt.dyn.get("agent.timeout_s"))),
    )
    hit = outcome.answered and score_answer(outcome.answer, question.get("expect") or [])
    return {
        "question": question["q"],
        "topic": question.get("topic", ""),
        "answered": outcome.answered,
        "hit": hit,
        "escalated": outcome.escalated,
        "confidence": outcome.confidence,
        # Уверенно ответил не туда: ответ дан, уверенность выше порога, а по сути мимо
        "confident_wrong": bool(outcome.answered and outcome.confidence >= threshold and not hit),
        "latency_ms": int((time.perf_counter() - started) * 1000),
        "answer": outcome.answer[:400],
    }


async def run(rt: AppRuntime) -> dict[str, Any]:
    """Полный прогон золотого набора. Возвращает отчёт и сохраняет его."""
    questions = load_questions(rt.dyn)
    threshold = float(rt.dyn.get("agent.confidence_threshold"))
    total = len(questions)
    await _set_progress(rt, 0, total)
    if not total:
        return await _store(rt, _empty_report())

    limit = max(1, int(rt.dyn.get("eval.concurrency")))
    gate = asyncio.Semaphore(limit)
    done = 0
    lock = asyncio.Lock()

    async def one(question: dict[str, Any]) -> dict[str, Any]:
        nonlocal done
        async with gate:
            try:
                result = await _ask(rt, question, threshold)
            except Exception:
                log.exception("Вопрос золотого набора не отработал: %s", question["q"][:80])
                result = {
                    "question": question["q"],
                    "topic": question.get("topic", ""),
                    "answered": False,
                    "hit": False,
                    "escalated": False,
                    "confidence": 0.0,
                    "confident_wrong": False,
                    "latency_ms": 0,
                    "answer": "",
                    "failed": True,
                }
        async with lock:
            done += 1
            await _set_progress(rt, done, total)
        return result

    results = await asyncio.gather(*(one(q) for q in questions))
    return await _store(rt, _summarize(results))


def _empty_report() -> dict[str, Any]:
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "total": 0,
        "metrics": {},
        "by_topic": [],
        "misses": [],
    }


def _summarize(results: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(results)
    answered = sum(1 for r in results if r["answered"])
    hits = sum(1 for r in results if r["hit"])
    confident_wrong = sum(1 for r in results if r["confident_wrong"])
    latencies = [r["latency_ms"] for r in results if r["latency_ms"]]
    confidences = [r["confidence"] for r in results if r["answered"]]

    topics: dict[str, dict[str, Any]] = {}
    for r in results:
        row = topics.setdefault(
            r["topic"] or "Без темы", {"topic": r["topic"] or "Без темы", "total": 0, "hits": 0}
        )
        row["total"] += 1
        row["hits"] += int(r["hit"])

    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "total": total,
        "metrics": {
            "accuracy": round(hits / total, 3),
            "answer_rate": round(answered / total, 3),
            # Главная метрика риска: ответил уверенно и мимо
            "confident_wrong_rate": round(confident_wrong / total, 3),
            "avg_confidence": round(statistics.fmean(confidences), 3) if confidences else None,
            "median_latency_ms": int(statistics.median(latencies)) if latencies else None,
        },
        "by_topic": sorted(topics.values(), key=lambda t: t["hits"] / t["total"]),
        # Промахи — то, ради чего отчёт открывают: с ними идут править базу знаний
        "misses": [
            {
                "question": r["question"],
                "topic": r["topic"],
                "confidence": r["confidence"],
                "answer": r["answer"],
                "confident_wrong": r["confident_wrong"],
            }
            for r in results
            if not r["hit"]
        ][:MISSES_LIMIT],
    }


async def _store(rt: AppRuntime, report: dict[str, Any]) -> dict[str, Any]:
    async with rt.db.session_factory() as db:
        db.add(AnalyticsReport(kind=REPORT_KIND, payload=report))
        await db.commit()
    metrics = report.get("metrics") or {}
    log.info(
        "Прогон золотого набора: %d вопросов, точность %s, уверенных ошибок %s",
        report.get("total"),
        metrics.get("accuracy"),
        metrics.get("confident_wrong_rate"),
    )
    return report


async def _set_progress(rt: AppRuntime, done: int, total: int) -> None:
    """Прогресс в Redis, а не в памяти процесса: инстансов api может быть несколько,
    и статус, видимый только одному из них, врал бы через раз."""
    try:
        await rt.cache.set(PROGRESS_KEY, json.dumps({"done": done, "total": total}), ex=PROGRESS_TTL)
    except Exception:  # noqa: BLE001 — прогресс необязателен, прогон важнее
        log.debug("Не удалось записать прогресс прогона")


async def progress(rt: AppRuntime) -> dict[str, Any] | None:
    try:
        raw = await rt.cache.get(PROGRESS_KEY)
    except Exception:  # noqa: BLE001
        return None
    if not raw:
        return None
    try:
        progress: dict[str, Any] = json.loads(raw if isinstance(raw, str) else raw.decode())
    except ValueError, AttributeError:
        return None
    return progress


async def last_reports(rt: AppRuntime, limit: int = 2) -> list[dict[str, Any]]:
    """Последние прогоны, свежий первым: отчёт читают не в одиночку, а в сравнении
    с предыдущим — «стало хуже» важнее абсолютных чисел."""
    async with rt.db.session_factory() as db:
        rows = (
            (
                await db.execute(
                    select(AnalyticsReport)
                    .where(AnalyticsReport.kind == REPORT_KIND)
                    .order_by(AnalyticsReport.created_at.desc())
                    .limit(limit)
                )
            )
            .scalars()
            .all()
        )
    return [row.payload for row in rows if row.payload]
