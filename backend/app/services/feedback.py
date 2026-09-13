"""Аналитика отзывов: сентимент, кластеризация негатива, оценка специалистов,
содержательное заключение о системных проблемах.

Методика (что и как считается по специалистам) — в specialist_metrics.py, там
чистые функции. Здесь — сбор выборок из базы и формулировка выводов простыми
словами: заключение читает руководитель службы поддержки, а не аналитик.

Сентимент — лексиконный (локально, мгновенно). Кластеризация — жадная по косинусной
близости эмбеддингов (без sklearn). Заключение строится шаблоном; если настроен LLM —
дополнительно формулируется генеративно.
"""

import json
import logging
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import numpy as np
from sqlalchemy import select

from app.models import AnalyticsReport, AuditLog, ChatSession, Feedback, Message, User
from app.services import specialist_metrics as sm
from app.services.retrieval import tokenize
from app.services.wordforms import plural, top_words

if TYPE_CHECKING:
    from app.runtime import AppRuntime

log = logging.getLogger(__name__)

_POSITIVE = {
    "спасибо",
    "отлично",
    "быстро",
    "помог",
    "помогл",
    "понятно",
    "супер",
    "хорошо",
    "оперативно",
    "вежлив",
    "решил",
    "решен",
    "четко",
    "грамотно",
    "доволен",
    "довольн",
    "прекрасно",
    "молодц",
    "разобрал",
    "работает",
}
_NEGATIVE = {
    "плохо",
    "ужасно",
    "долго",
    "не помог",
    "непонятно",
    "бесполезн",
    "хамств",
    "груб",
    "не решил",
    "не работает",
    "ошибк",
    "жду",
    "ожидан",
    "медленно",
    "отвратительно",
    "некомпетент",
    "путан",
    "сложно",
    "неудобно",
    "зря",
    "робот",
    "шаблон",
}


def sentiment_score(text: str) -> float:
    """-1..1 по лексикону (стемминг префиксом — как в поиске)."""
    lower = text.lower().replace("ё", "е")
    tokens = tokenize(text)
    pos = sum(1 for t in tokens for w in _POSITIVE if t.startswith(w[:6]))
    neg = sum(1 for t in tokens for w in _NEGATIVE if t.startswith(w[:6]))
    neg += sum(1 for phrase in ("не помог", "не решил", "не работает") if phrase in lower)
    if pos == neg == 0:
        return 0.0
    return round((pos - neg) / (pos + neg), 3)


# Столько слов помещается в строку карточки кластера и читается с одного взгляда
CLUSTER_KEYWORDS = 5


def _greedy_clusters(vectors: np.ndarray, texts: list[str], threshold: float = 0.62) -> list[dict[str, Any]]:
    """Жадная кластеризация: каждый текст липнет к ближайшему центроиду либо создаёт новый."""
    centroids: list[np.ndarray] = []
    members: list[list[int]] = []
    norms = np.linalg.norm(vectors, axis=1)
    norms[norms == 0] = 1.0
    unit = vectors / norms[:, None]
    for i, v in enumerate(unit):
        best, best_sim = -1, threshold
        for c_idx, c in enumerate(centroids):
            sim = float(v @ c / (np.linalg.norm(c) or 1.0))
            if sim > best_sim:
                best, best_sim = c_idx, sim
        if best == -1:
            centroids.append(v.copy())
            members.append([i])
        else:
            members[best].append(i)
            centroids[best] = unit[members[best]].mean(axis=0)

    clusters = []
    for idxs in sorted(members, key=len, reverse=True):
        cluster_texts = [texts[i] for i in idxs]
        clusters.append(
            {
                "size": len(idxs),
                "representative": max(cluster_texts, key=len),
                # Ключевые слова читает администратор, а не поисковый индекс: целые
                # слова без служебных, а не шестибуквенные токены поиска
                "keywords": top_words(cluster_texts, CLUSTER_KEYWORDS),
                "examples": cluster_texts[:3],
                # Номера текстов кластера: по ним отчёт находит, чья это жалоба
                "members": idxs,
            }
        )
    return clusters


# На пятибалльной шкале 1–3 — недовольство, 4–5 — довольство
NEGATIVE_MAX_RATING = sm.NEGATIVE_MAX_RATING
POSITIVE_MIN_RATING = sm.POSITIVE_MIN_RATING
# Образцом называем специалиста с индексом от 85 и не меньше чем пятью отзывами
STRONG_INDEX = 85
# В заключении — не больше трёх имён и трёх тем: дальше текст перестают читать
MAX_NAMED = 3
# Частая жалоба — от двух похожих отзывов: один отзыв — частный случай
COMPLAINT_MIN_SIZE = 2
MAX_RECOMMENDATIONS = 5
MAX_CLUSTERS = 6
# Заголовок статьи — вопрос целиком; в тексте заключения хватает начала
ARTICLE_TITLE_CHARS = 80
NO_TOPIC = "Без темы"
_PERCENT = 100

EMPTY_CONCLUSION = "Отзывов пока нет — заключение будет сформировано после накопления обратной связи."


@dataclass
class _Cited:
    """Статья, по которой отвечал ИИ, и пальцы под этими ответами."""

    topic: str = ""
    up: int = 0
    down: int = 0
    unclear: int = 0


@dataclass
class _Trail:
    """Что известно о диалоге из его сообщений."""

    question: str = ""
    topic: str = ""
    first_operator_at: datetime | None = None
    replies: list[str] = field(default_factory=list)
    latencies: list[int] = field(default_factory=list)
    up: int = 0
    down: int = 0
    unclear: int = 0
    # Статьи ответов ИИ в порядке появления; отзыв о диалоге относим к первой
    cited: dict[str, _Cited] = field(default_factory=dict)


def _trails(rows: list[Any]) -> dict[str, _Trail]:
    """Сообщения по диалогам: первый вопрос, тема по первой цитате, ответы ИИ, пальцы."""
    trails: dict[str, _Trail] = {}
    for session_id, role, content, meta, citations, created_at in rows:
        trail = trails.setdefault(session_id, _Trail())
        meta = meta or {}
        if role == "user" and not trail.question:
            trail.question = content or ""
        elif role == "operator" and trail.first_operator_at is None:
            trail.first_operator_at = created_at
        elif role == "assistant":
            kind = sm.reply_kind(meta)
            if kind is None:
                continue
            trail.replies.append(kind)
            if isinstance(meta.get("latency_ms"), int | float):
                trail.latencies.append(int(meta["latency_ms"]))
            rating = meta.get("rating")
            unclear = rating == -1 and meta.get("rating_reason") == sm.UNCLEAR_REASON
            trail.up += rating == 1
            trail.down += rating == -1
            trail.unclear += unclear
            if not trail.topic:
                trail.topic = next(
                    (str(c.get("category") or "") for c in citations or [] if c.get("category")), ""
                )
            source = next((c for c in citations or [] if c.get("question")), None)
            if source:
                cited = trail.cited.setdefault(
                    str(source["question"]), _Cited(str(source.get("category") or ""))
                )
                cited.up += rating == 1
                cited.down += rating == -1
                cited.unclear += unclear
    return trails


def _article_cases(
    sessions: Sequence[Any], trails: Mapping[str, _Trail], feedback: Sequence[Any]
) -> list[sm.ArticleCase]:
    """Диалоги глазами статей: по какой статье ответил ИИ и что человек сказал об ответе."""
    reviewed = {f.session_id: f for f in feedback}
    cases = []
    for s in sessions:
        trail = trails.get(s.id)
        if trail is None:
            continue
        # После перевода на оператора звёзды — оператору, статье остаются пальцы
        review = None if s.operator_id else reviewed.get(s.id)
        for position, (title, cited) in enumerate(trail.cited.items()):
            about = review if position == 0 else None
            cases.append(
                sm.ArticleCase(
                    article=title,
                    topic=cited.topic,
                    rating=about.rating if about else None,
                    solved=about.solved if about else None,
                    clear=about.clear if about else None,
                    up=cited.up,
                    down=cited.down,
                    down_unclear=cited.unclear,
                )
            )
    return cases


def _line_label(lines: Any, line: int | None) -> str:
    if not line:
        return ""
    label = (lines or {}).get(str(line), {}).get("label") if isinstance(lines, dict) else None
    return f"Линия {line}: {label}" if label else f"Линия {line}"


@dataclass
class _Sample:
    reviews: list[sm.Review]
    cases: list[sm.Case]
    names: dict[str, str]
    abandoned: dict[str, int]
    articles: list[sm.ArticleCase] = field(default_factory=list)


async def _load(rt: AppRuntime) -> _Sample:
    """Выборки для методики из базы: отзывы, обращения, брошенные обращения."""
    async with rt.db.session_factory() as db:
        feedback = (await db.execute(select(Feedback))).scalars().all()
        names = {
            u.id: u.display_name or u.username
            for u in (await db.execute(select(User).where(User.role == "operator"))).scalars()
        }
        sessions = (await db.execute(select(ChatSession))).scalars().all()
        rows = (
            await db.execute(
                select(
                    Message.session_id,
                    Message.role,
                    Message.content,
                    Message.meta,
                    Message.citations,
                    Message.created_at,
                )
                .where(Message.role.in_(("user", "assistant", "operator")))
                .order_by(Message.created_at)
            )
        ).all()
        # Кого диспетчер лишил обращения за молчание: журнал хранит каждый такой
        # случай с оператором, а у диалога остаётся лишь последний отпустивший
        reclaims = (
            (await db.execute(select(AuditLog.payload).where(AuditLog.action == "session.reclaimed")))
            .scalars()
            .all()
        )
    trails = _trails(list(rows))
    lines = rt.dyn.get("routing.lines")
    topics: dict[str, str] = {}
    cases = []
    for s in sessions:
        trail = trails.get(s.id, _Trail())
        topics[s.id] = s.topic or trail.topic or _line_label(lines, s.line) or NO_TOPIC
        cases.append(
            sm.Case(
                session_id=s.id,
                specialist=s.operator_id or sm.AI_ID,
                created_at=s.created_at,
                escalated_at=s.escalated_at,
                closed_at=s.closed_at,
                first_reply_at=trail.first_operator_at,
                visitor=s.user_id,
                question=trail.question,
                line=s.line,
                ai_replies=tuple(trail.replies),
                ai_latencies_ms=tuple(trail.latencies),
                thumbs_up=trail.up,
                thumbs_down=trail.down,
                thumbs_unclear=trail.unclear,
            )
        )
    reviews = [
        sm.Review(
            specialist=f.operator_id or sm.AI_ID,
            rating=f.rating,
            solved=f.solved,
            clear=f.clear,
            comment=f.comment,
            sentiment=f.sentiment,
            topic=topics.get(f.session_id, NO_TOPIC),
            created_at=f.created_at,
        )
        for f in feedback
    ]
    abandoned: dict[str, int] = {}
    for payload in reclaims:
        operator = (payload or {}).get("operator_id")
        if operator:
            abandoned[operator] = abandoned.get(operator, 0) + 1
    return _Sample(reviews, cases, names, abandoned, _article_cases(sessions, trails, feedback))


def _summary(reviews: list[sm.Review]) -> dict[str, Any]:
    ratings = [r.rating for r in reviews]
    solved = sm.tally(r.solved for r in reviews)
    clear = sm.tally(r.clear for r in reviews)
    return {
        "count": len(ratings),
        "avg_rating": round(sum(ratings) / len(ratings), 2),
        "positive_share": sm.share(sum(1 for r in ratings if r >= POSITIVE_MIN_RATING), len(ratings)),
        "negative_count": sum(1 for r in ratings if r <= NEGATIVE_MAX_RATING),
        "ai_count": sum(1 for r in reviews if r.specialist == sm.AI_ID),
        "operator_count": sum(1 for r in reviews if r.specialist != sm.AI_ID),
        "solved_rate": sm.share(solved.yes, solved.total),
        "solved_total": solved.total,
        "clear_rate": sm.share(clear.yes, clear.total),
        "clear_total": clear.total,
    }


def _settings(rt: AppRuntime) -> sm.Settings:
    target = rt.dyn.get("feedback.reply_target_seconds")
    valid = isinstance(target, int | float) and not isinstance(target, bool) and target > 0
    return sm.Settings(
        weights=sm.normalize_weights(rt.dyn.get("feedback.index_weights")),
        reply_target_s=float(target) if valid else sm.DEFAULT_REPLY_TARGET_S,
    )


async def build_feedback_report(rt: AppRuntime) -> dict[str, Any]:
    """Полный отчёт: сводка, специалисты, темы, динамика, кластеры, проблемы, заключение."""
    sample = await _load(rt)
    if not sample.reviews:
        return {
            "summary": {"count": 0},
            "specialists": [],
            "clusters": [],
            "topics": [],
            "problems": [],
            "recommendations": [],
            "conclusion": EMPTY_CONCLUSION,
        }

    settings = _settings(rt)
    base = sm.baseline(sample.reviews)
    summary = _summary(sample.reviews)
    specialists = sm.specialist_scores(
        sample.reviews, sample.cases, names=sample.names, abandoned=sample.abandoned, settings=settings
    )
    topics = sm.topic_breakdown(sample.reviews, base)
    articles = sm.article_breakdown(sample.articles)
    trend = sm.negative_trend(sample.reviews, datetime.now(UTC))

    # кластеры негативных комментариев -> системные проблемы
    negative = [r for r in sample.reviews if r.rating <= NEGATIVE_MAX_RATING and r.comment.strip()]
    neg_texts = [r.comment for r in negative]
    clusters: list[dict[str, Any]] = []
    if neg_texts:
        vectors = np.array(await rt.embedder.embed(neg_texts), dtype=np.float32)
        clusters = _greedy_clusters(vectors, neg_texts)[:MAX_CLUSTERS]
        for cluster in clusters:
            members = [negative[i] for i in cluster.pop("members")]
            focus = _complaint_focus(members, sample.names)
            if focus:
                cluster["focus"], cluster["focus_count"] = focus

    problems = system_problems(specialists, topics, trend, clusters, articles=articles)
    recommendations = _recommendations(problems)
    conclusion = _compose_conclusion(summary, specialists, clusters, Findings(topics, articles, trend))
    if recommendations:
        conclusion += "\nЧто сделать:\n" + "\n".join(
            f"— {line[:1].upper()}{line[1:]}." for line in recommendations
        )
    if rt.dyn.get("llm.base_url") and rt.dyn.get("llm.model"):
        generated = await _llm_conclusion(rt, summary, specialists, problems, clusters)
        if generated:
            conclusion = generated

    report = {
        "summary": summary,
        "specialists": specialists,
        "clusters": clusters,
        "topics": topics,
        "trend": trend,
        "problems": problems,
        "recommendations": recommendations,
        "methodology": {
            "weights": dict(settings.weights),
            "prior_weight": sm.PRIOR_WEIGHT,
            # Средние по службе — к ним тянется оценка того, у кого мало отзывов;
            # страница показывает на них пример поправки
            "baseline": {
                "solved": round(base.solved, 3),
                "clear": round(base.clear, 3),
                "csat": round(base.csat, 3),
            },
            "min_reviews": sm.MIN_REVIEWS,
            "reply_target_seconds": settings.reply_target_s,
            "repeat_window_hours": int(sm.REPEAT_WINDOW.total_seconds() // 3600),
        },
        "conclusion": conclusion,
        "generated_at": datetime.now(UTC).isoformat(),
    }
    async with rt.db.session_factory() as db:
        db.add(AnalyticsReport(kind="feedback_summary", payload=report))
        await db.commit()
    return report


def _pct(value: float | None) -> str:
    return f"{round((value or 0) * _PERCENT)}%"


def _topic_detail(topic: dict[str, Any]) -> str:
    parts = []
    if topic["unsolved"] and topic["solved_total"]:
        parts.append(
            f"не решено в {topic['unsolved']} из {topic['solved_total']} {_out_of(topic['solved_total'])}"
        )
    if topic["unclear"] and topic["clear_total"]:
        parts.append(
            f"непонятно в {topic['unclear']} из {topic['clear_total']} {_out_of(topic['clear_total'])}"
        )
    return ", ".join(parts)


def _out_of(total: int) -> str:
    return plural(total, ("отзыва", "отзывов", "отзывов"))


def _trend_detail(trend: dict[str, Any]) -> str:
    current = _pct(trend["current"]["negative_share"])
    previous = _pct(trend["previous"]["negative_share"])
    return (
        f"оценки 1–3 у {current} отзывов за последние {trend['days']} дней против {previous} неделей раньше"
    )


def _short(title: str) -> str:
    """Начало заголовка статьи: заголовок — вопрос целиком и бывает в два предложения."""
    if len(title) <= ARTICLE_TITLE_CHARS:
        return title
    return title[: ARTICLE_TITLE_CHARS - 1].rstrip() + "…"


def _article_detail(article: dict[str, Any]) -> str:
    dialogs = plural(article["cases"], ("диалога", "диалогов", "диалогов"))
    reasons = [
        f"{label} — {article[key]}"
        for key, label in (("unsolved", "не помогло"), ("unclear", "непонятно"))
        if article[key]
    ]
    text = f"после ответа по статье недовольны в {article['bad']} из {article['cases']} {dialogs}"
    return f"{text}: {', '.join(reasons)}" if reasons else text


def _complaint_focus(members: Sequence[sm.Review], names: Mapping[str, str]) -> tuple[str, int] | None:
    """Чья жалоба: оператор или тема, на которых сошлось больше половины отзывов кластера.

    Совет «разобрать эти обращения» без адресата никуда не ведёт. ИИ-ассистента
    адресатом не называем: он отвечает по статьям, и чинить надо тему.
    """
    operators = Counter(r.specialist for r in members if r.specialist != sm.AI_ID).most_common(1)
    themes = Counter(r.topic for r in members if r.topic and r.topic != NO_TOPIC).most_common(1)
    candidates = [(names.get(sid, sid), n) for sid, n in operators] + [(f"тема «{t}»", n) for t, n in themes]
    for label, count in candidates:
        if count >= COMPLAINT_MIN_SIZE and count * 2 > len(members):
            return label, count
    return None


def _specialist_problems(specialists: list[dict[str, Any]]) -> list[dict[str, str]]:
    return [
        {
            "kind": "specialist",
            "title": s["name"],
            "detail": "; ".join(spot["text"] for spot in s["weak_spots"]),
            # Два первых совета: решение и понятность стоят в слабых местах первыми,
            # и у одного человека обычно хромает и то и другое
            "advice": "; ".join(dict.fromkeys(spot["advice"] for spot in s["weak_spots"][:2])),
            "target": s["name"],
        }
        for s in specialists
        if s.get("weak_spots")
    ]


def _topic_problems(topics: list[dict[str, Any]], articles: list[dict[str, Any]]) -> list[dict[str, str]]:
    # Правку темы начинают с её худшей статьи — иначе «проверить статьи темы» тонет в объёме
    worst_article: dict[str, str] = {}
    for a in articles:
        if a["bad"] and a["topic"]:
            worst_article.setdefault(a["topic"], a["article"])
    problems = []
    for t in topics:
        if not t.get("flagged"):
            continue
        advice = "проверить статьи этой темы и дописать недостающие шаги"
        if t["topic"] in worst_article:
            advice += f", начать со статьи «{_short(worst_article[t['topic']])}»"
        problems.append(
            {
                "kind": "topic",
                "title": f"Тема «{t['topic']}»",
                "detail": _topic_detail(t),
                "advice": advice,
                "target": f"тема «{t['topic']}»",
            }
        )
    return problems


def _article_problems(articles: list[dict[str, Any]]) -> list[dict[str, str]]:
    return [
        {
            "kind": "article",
            "title": f"Статья «{_short(a['article'])}»",
            "detail": _article_detail(a),
            # Непонятую статью переписывают, а непомогшую проверяют по существу
            "advice": "переписать проще: по шагам, без служебных слов"
            if a["unclear"] >= a["unsolved"]
            else "проверить, верны ли шаги и не устарела ли статья",
            "target": f"статья «{_short(a['article'])}»",
        }
        for a in articles
        if a.get("flagged")
    ]


def _complaint_problems(clusters: list[dict[str, Any]]) -> list[dict[str, str]]:
    problems = []
    for c in clusters:
        if c["size"] < COMPLAINT_MIN_SIZE:
            continue
        appeals = plural(c["size"], ("отзыв", "отзыва", "отзывов"))
        detail = f"«{c['representative'][:160]}»"
        if c.get("focus"):
            detail += f"; {c['focus_count']} из {c['size']} — {c['focus']}"
        problems.append(
            {
                "kind": "complaint",
                "title": f"Частая жалоба ({c['size']} {appeals})",
                "detail": detail,
                "advice": "разобрать эти обращения и устранить общую причину",
                # Без общего адресата советом правит сама жалоба: иначе в списке дел
                # стояло бы безымянное «разобрать эти обращения»
                "target": c.get("focus") or f"жалоба «{_short(c['representative'])}»",
            }
        )
    return problems


def system_problems(
    specialists: list[dict[str, Any]],
    topics: list[dict[str, Any]],
    trend: dict[str, Any] | None,
    clusters: list[dict[str, Any]],
    *,
    articles: list[dict[str, Any]] | None = None,
) -> list[dict[str, str]]:
    """Системные проблемы простыми словами: что, в чём это видно, что делать и кому."""
    problems = (
        _specialist_problems(specialists)
        + _topic_problems(topics, articles or [])
        + _article_problems(articles or [])
    )
    if trend and trend.get("growing"):
        problems.append(
            {
                "kind": "trend",
                "title": "Негатива стало больше",
                "detail": _trend_detail(trend),
                "advice": "выяснить, что изменилось за неделю: новые сотрудники, сбои портала, новые правила",
                "target": "",
            }
        )
    return problems + _complaint_problems(clusters)


def _recommendations(problems: list[dict[str, str]]) -> list[str]:
    """Советы «кому — что сделать», в порядке важности проблем.

    Одному адресату — одна строка: жалоба на того же оператора или тему, что
    уже в списке, повторила бы его, а не добавила дело.
    """
    lines: list[str] = []
    targets: set[str] = set()
    for problem in problems:
        advice, target = problem.get("advice", ""), problem.get("target", "")
        if not advice or (target and target in targets):
            continue
        line = f"{target}: {advice}" if target else advice
        if line not in lines:
            lines.append(line)
            targets.add(target)
    return lines[:MAX_RECOMMENDATIONS]


def _headline(summary: dict[str, Any], specialists: Sequence[dict[str, Any]] = ()) -> list[str]:
    parts = [
        f"Проанализировано отзывов: {summary['count']}. Средняя оценка {summary['avg_rating']}/5, "
        f"доля положительных {(summary.get('positive_share') or 0) * _PERCENT:.0f}%.",
    ]
    # Две части методики — двумя фразами: руководителю важно, что хромает — знания
    # (не решили) или объяснение (не поняли), чинят их по-разному
    if summary.get("solved_rate") is not None:
        parts.append(f"Результат работы: проблему решили в {_pct(summary['solved_rate'])} случаев.")
    clarity = []
    if summary.get("clear_rate") is not None:
        clarity.append(f"понятно объяснили в {_pct(summary['clear_rate'])} случаев")
    ai = next((s for s in specialists if s.get("kind") == "ai" and s.get("thumbs_unclear")), None)
    if ai:
        marked, votes = ai["thumbs_unclear"], ai["thumbs_up"] + ai["thumbs_down"]
        times = plural(marked, ("раз", "раза", "раз"))
        clarity.append(
            f"под ответами ИИ-ассистента «Непонятно» отметили {marked} {times} "
            f"из {votes} {plural(votes, ('оценки', 'оценок', 'оценок'))}"
        )
    if clarity:
        parts.append("Качество донесения: " + "; ".join(clarity) + ".")
    return parts


def _specialists_part(specialists: list[dict[str, Any]]) -> list[str]:
    parts = []
    weak = [s for s in specialists if s.get("weak_spots")]
    if weak:
        # В тексте — три главных слабых места: решение, понятность и оценки идут
        # первыми; полный список виден в таблице специалистов
        named = "; ".join(
            f"{s['name']} — {', '.join(spot['text'] for spot in s['weak_spots'][:MAX_NAMED])}"
            for s in weak[:MAX_NAMED]
        )
        parts.append(f"Требуют внимания специалисты: {named}.")
    strong = max(
        (s for s in specialists if s.get("index") is not None and not s.get("low_data")),
        key=lambda s: s["index"],
        default=None,
    )
    if strong and strong["index"] >= STRONG_INDEX and not strong.get("weak_spots"):
        parts.append(
            f"Лучший результат — {strong['name']} (индекс качества {strong['index']} из 100): "
            "эти ответы стоит взять за образец."
        )
    return parts


@dataclass(frozen=True, slots=True)
class Findings:
    """Разрезы отчёта, из которых заключение берёт проблемы: темы, статьи, динамику."""

    topics: list[dict[str, Any]] = field(default_factory=list)
    articles: list[dict[str, Any]] = field(default_factory=list)
    trend: dict[str, Any] | None = None


def _compose_conclusion(
    summary: dict[str, Any],
    specialists: list[dict[str, Any]],
    clusters: list[dict[str, Any]],
    findings: Findings | None = None,
) -> str:
    """Шаблонное заключение: каждое предложение — факт с числом и тем, к кому он относится."""
    found_in = findings or Findings()
    parts = _headline(summary, specialists) + _specialists_part(specialists)
    flagged = [t for t in found_in.topics if t.get("flagged")]
    if flagged:
        listed = ", ".join(f"«{t['topic']}» ({_topic_detail(t)})" for t in flagged[:MAX_NAMED])
        parts.append(f"Проблемные темы: {listed}.")
    weak_articles = [a for a in found_in.articles if a.get("flagged")]
    if weak_articles:
        listed = ", ".join(
            f"«{_short(a['article'])}» (недовольны в {a['bad']} из {a['cases']})"
            for a in weak_articles[:MAX_NAMED]
        )
        parts.append(f"Статьи, после которых чаще недовольны: {listed}.")
    trend = found_in.trend
    if trend and trend.get("growing"):
        parts.append(f"Негатива стало больше: {_trend_detail(trend)}.")
    if clusters:
        top = clusters[0]
        # В отзыве из одних служебных слов («так себе») значимых слов нет — тогда
        # без фразы о словах, иначе выходило «характеризуется словами: .»
        words = f" характеризуется словами: {', '.join(top['keywords'])}" if top["keywords"] else ""
        # Число согласуем со словом: было «1 системных кластеров» и «3 обращений»
        count = len(clusters)
        found = plural(count, ("Выявлен", "Выявлено", "Выявлено"))
        kinds = plural(count, ("системный кластер", "системных кластера", "системных кластеров"))
        appeals = plural(top["size"], ("обращение", "обращения", "обращений"))
        parts.append(
            f"{found} {count} {kinds} негатива; крупнейший ({top['size']} {appeals})"
            f"{words}. Типичный отзыв: «{top['representative'][:160]}»."
        )
        if not specialists and not flagged:
            parts.append(
                "Рекомендации: пополнить базу знаний по проблемным темам, обновить регламенты "
                "ответов и проверить SLA по линиям, где сконцентрирован негатив."
            )
    return "\n".join(parts)


def _facts(
    summary: dict[str, Any],
    specialists: list[dict[str, Any]],
    problems: list[dict[str, str]],
    clusters: list[dict[str, Any]],
) -> str:
    """Данные для LLM — только то, что нужно для выводов, без служебных полей."""
    compact = [
        {
            "имя": s["name"],
            "индекс": s.get("index"),
            "мало данных": s.get("low_data"),
            "отзывов": s["count"],
            "довольны": s.get("csat"),
            "решено": s.get("solved_rate"),
            "понятно": s.get("clear_rate"),
            "слабые места": [spot["text"] for spot in s.get("weak_spots") or []],
        }
        for s in specialists
    ]
    return json.dumps(
        {
            "сводка": summary,
            "специалисты": compact,
            "проблемы": problems,
            "жалобы": [{"размер": c["size"], "пример": c["representative"]} for c in clusters],
        },
        ensure_ascii=False,
    )


async def _llm_conclusion(
    rt: AppRuntime,
    summary: dict[str, Any],
    specialists: list[dict[str, Any]],
    problems: list[dict[str, str]],
    clusters: list[dict[str, Any]],
) -> str | None:
    """Генеративное заключение через настроенный LLM (тот же OpenAI-совместимый API)."""
    try:
        import httpx

        prompt = (
            "Ты — руководитель службы поддержки. По данным ниже напиши краткое содержательное "
            "заключение (5-7 предложений) о качестве работы специалистов, о том, насколько понятно "
            "они объясняют, и о системных проблемах, с конкретными рекомендациями. Пиши простыми "
            "словами для человека не из IT, называй числа и имена.\n\n"
            + _facts(summary, specialists, problems, clusters)
        )
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                f"{str(rt.dyn.get('llm.base_url')).rstrip('/')}/chat/completions",
                headers={"Authorization": f"Bearer {rt.dyn.get('llm.api_key')}"},
                json={
                    "model": rt.dyn.get("llm.model"),
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.3,
                },
            )
            resp.raise_for_status()
            content: str | None = resp.json()["choices"][0]["message"]["content"]
            return content
    except Exception:
        log.exception("LLM-заключение не получилось, используем шаблонное")
        return None
