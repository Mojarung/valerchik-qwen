"""Идемпотентный сидер: наполняет пустую БД демо-данными, индексирует БЗ,
обучает классификатор линий. Детерминирован (seed=42)."""

import logging
import random
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.chat.knowledge import TOP_QUERIES_KEY
from app.config import DEMO_ADMIN_PASSWORD, DEMO_OPERATOR_PASSWORD, Settings, get_settings
from app.models import (
    AuditLog,
    ChatSession,
    Feedback,
    KBArticle,
    KBGap,
    McpServer,
    Message,
    QuickReply,
    User,
    new_id,
)
from app.security import hash_password
from app.seed import data
from app.services.feedback import sentiment_score
from app.services.retrieval import tokenize

if TYPE_CHECKING:
    from app.runtime import AppRuntime

log = logging.getLogger(__name__)

ZAKUPKI_MCP_NAME = "Каталог товаров портала"


async def seed_if_empty(rt: AppRuntime) -> None:
    async with rt.db.session_factory() as db:
        users_count = (await db.execute(select(func.count(User.id)))).scalar() or 0
        kb_count = (await db.execute(select(func.count(KBArticle.id)))).scalar() or 0
        feedback_count = (await db.execute(select(func.count(Feedback.id)))).scalar() or 0
    if users_count == 0:
        await _seed_users(rt)
    if kb_count == 0:
        await _seed_dialogs(rt, await _seed_kb(rt))
    elif feedback_count == 0:
        # Стенд «дрейфанул»: статьи налили импортом, поэтому условие «база пустая»
        # больше не выполняется — и демо-данных не появилось никогда.
        #
        # Считаем именно отзывы, а не диалоги: диалоги на стенде набегают от
        # ручных проверок, а оценок при этом нет ни одной — и раздел оценки
        # специалистов пуст целиком, вместе с отчётом и средней оценкой на
        # дашборде. Снаружи это выглядит не как «данных пока мало», а как
        # неработающий раздел, который закрывает целый пункт задания.
        #
        # Досеваем поверх того, что уже есть, ничего не удаляя. На системе с
        # живыми пользователями ветка не нужна и не должна срабатывать —
        # там сидер выключается целиком через SEED_ON_STARTUP=false.
        articles = await _active_articles(rt)
        if articles:
            log.info("В базе %d статей и ни одного отзыва — досеваю демо-диалоги", len(articles))
            await _seed_dialogs(rt, articles)
    await _register_zakupki_mcp(rt)


async def _register_zakupki_mcp(rt: AppRuntime) -> None:
    """Заводит в реестре MCP сервер каталога товаров портала.

    Проверка идёт по имени, а не по пустоте таблицы: на уже развёрнутой базе запись
    тоже должна появиться, иначе после обновления интеграция молча не включится.
    Существующую запись не трогаем — администратор мог её выключить или подменить
    адрес, и переписывать его решение при каждом рестарте нельзя.
    """
    url = get_settings().zakupki_mcp_url
    if not url:
        return
    async with rt.db.session_factory() as db:
        exists = (
            await db.execute(select(McpServer).where(McpServer.name == ZAKUPKI_MCP_NAME))
        ).scalar_one_or_none()
        if exists is not None:
            return
        db.add(
            McpServer(
                name=ZAKUPKI_MCP_NAME,
                url=url,
                enabled=True,
                description="Каталог товаров Портала поставщиков: поиск СТЕ, цены, фотографии",
            )
        )
        await db.commit()
    log.info("MCP «%s» зарегистрирован: %s", ZAKUPKI_MCP_NAME, url)


def staff_passwords(cfg: Settings) -> tuple[str, str]:
    """Пароли администратора и операторов при первом сидировании.

    Демо-пароли остаются только в lite: это режим показа и тестов, где сервис
    обязан подниматься одной командой. В full пустой пароль — не повод молча
    завести общеизвестную учётку, а причина остановить старт.
    """
    admin, operator = cfg.admin_password.strip(), cfg.operator_password.strip()
    if cfg.is_lite:
        return admin or DEMO_ADMIN_PASSWORD, operator or DEMO_OPERATOR_PASSWORD
    if not admin:
        raise RuntimeError("ADMIN_PASSWORD не задан: в режиме full пароль администратора обязателен")
    if not operator:
        raise RuntimeError("OPERATOR_PASSWORD не задан: в режиме full пароль оператора обязателен")
    return admin, operator


async def _seed_users(rt: AppRuntime) -> None:
    cfg = get_settings()
    admin_password, operator_password = staff_passwords(cfg)
    async with rt.db.session_factory() as db:
        db.add(
            User(
                id="admin".ljust(32, "0"),
                username="admin",
                password_hash=hash_password(admin_password),
                role="admin",
                display_name="Администратор",
            )
        )
        for uid, name, line in data.OPERATORS:
            db.add(
                User(
                    id=uid.ljust(32, "0"),
                    username=uid,
                    password_hash=hash_password(operator_password),
                    role="operator",
                    display_name=name,
                    line=line,
                )
            )
        db.add(
            User(
                id="demouser".ljust(32, "0"),
                username="demo",
                password_hash=hash_password("demo12345"),
                role="user",
                display_name="ООО «Ромашка»",
            )
        )
        await db.commit()
    log.info("Пользователи созданы: admin + %d операторов + demo", len(data.OPERATORS))


def _flat_articles() -> list[dict[str, Any]]:
    """Статьи сида плоским списком. Роль статьи перекрывает роль категории."""
    out = []
    for category, line, category_audience, items in data.KB:
        for item in items:
            question, answer, keywords, *rest = item
            audience = rest[0] if rest else category_audience
            out.append(
                {
                    "question": question,
                    "answer": answer,
                    "keywords": keywords,
                    "category": category,
                    "line": line,
                    "audience": audience,
                }
            )
    return out


async def _seed_kb(rt: AppRuntime) -> list[KBArticle]:
    rng = random.Random(42)
    articles_data = _flat_articles()

    async with rt.db.session_factory() as db:
        articles: list[KBArticle] = []
        for a in articles_data:
            row = KBArticle(
                **a,
                status="active",
                source="seed",
                views=rng.randint(15, 400),
                helpful=rng.randint(3, 60),
                not_helpful=rng.randint(0, 8),
            )
            db.add(row)
            articles.append(row)
        for title, text, audience in data.QUICK_REPLIES:
            db.add(QuickReply(title=title, text=text, audience=audience, usage_count=rng.randint(0, 40)))
        await db.commit()
        for article in articles:
            await db.refresh(article)

    await rt.kb.index_all()

    samples = [(f"{a['question']} {' '.join(a['keywords'])}", a["line"]) for a in articles_data]
    await rt.classifier.train(samples)
    return articles


async def _active_articles(rt: AppRuntime) -> list[KBArticle]:
    async with rt.db.session_factory() as db:
        rows = (
            (await db.execute(select(KBArticle).where(KBArticle.status == "active").order_by(KBArticle.id)))
            .scalars()
            .all()
        )
    return list(rows)


# Демо-диалоги: сколько каких и за какой срок. Объём подобран так, чтобы
# дашборду и оценке специалистов было что показать, а стенд не распухал.
DEMO_DAYS = 13
WORKDAY_MINUTES = 600
RESOLVED_DIALOGS = 78
ESCALATED_DIALOGS = 20
VIOLATION_REPEATS = 2
OFF_KB_REPEATS = 3
# Отзыв оставляют не все — чуть больше половины, и из них большинство довольны
FEEDBACK_SHARE = 0.55
PRAISE_SHARE = 0.85
# Каждая третья эскалация достаётся «слабому» специалисту: на этом контрасте
# держится раздел оценки специалистов, иначе в нём нечего разбирать
WEAK_OPERATOR = "op_sergey".ljust(32, "0")
WEAK_OPERATOR_EVERY = 3
# Слабое место у него конкретное: объясняет непонятно и часто не решает вопрос.
# Доли подобраны так, чтобы отчёт назвал именно эти измерения, а не всё сразу.
WEAK_SOLVED_SHARE = 0.3
WEAK_CLEAR_SHARE = 0.15
# Он вышел на смену неделю назад: его отзывы — в последних днях, и отчёт видит
# рост негатива к прошлой неделе вместе с его причиной
WEAK_OPERATOR_DAYS = 6
# Первый ответ оператора, сек: у слабого — вдесятеро дольше нормы в две минуты
REPLY_SECONDS = (70, 200)
WEAK_REPLY_SECONDS = (600, 1500)
CLOSE_AFTER_SECONDS = (600, 1500)
# Два обращения он взял и бросил: диспетчер вернул их в очередь, закрыли другие
RECLAIMED_FROM_WEAK = (1, 4)
RECLAIM_SILENCE_SECONDS = 300
# Сложная тема: по ней бот чаще не решает вопрос — на этом держится разрез
# «тема × нерешённые». На импортированной базе такой темы может не быть — тогда
# разреза не видно, и это честно.
HARD_TOPIC = "Электронная подпись"
HARD_TOPIC_PRAISE_SHARE = 0.35
# Не каждый отвечает на два вопроса после звёзд — как и в жизни
SKIP_ANSWERS_SHARE = 0.15
# Довольный ответом иногда всё же пишет «непонятно» — без этого доля «понятно» была бы 100%
CONTENT_BUT_UNCLEAR_SHARE = 0.1
# Палец вверх или вниз под ответом бота ставит примерно каждый второй из оценивших
THUMB_SHARE = 0.5
TOPIC_CHARS = 128


class _DialogWriter:
    """Пишет демо-диалоги в одну сессию БД. Случайность — из одного генератора
    с фиксированным зерном, поэтому набор повторяется от запуска к запуску."""

    def __init__(self, db: AsyncSession, rng: random.Random, now: datetime) -> None:
        self.db = db
        self.rng = rng
        self.now = now

    def moment(self, spread_minutes: float = 0, days_back: float = DEMO_DAYS) -> datetime:
        """Момент за последние days_back дней; spread_minutes — разброс внутри дня."""
        days = self.rng.uniform(0, days_back)
        minutes = self.rng.uniform(0, spread_minutes) if spread_minutes else 0
        return self.now - timedelta(days=days, minutes=minutes)

    def paraphrase(self, question: str) -> str:
        """Вопрос статьи так, как его написал бы человек."""
        template = self.rng.choice(data.PARAPHRASES)
        return template.format(q=question, ql=question[0].lower() + question[1:])

    def session(self, created: datetime, status: str, **columns: Any) -> ChatSession:
        session = ChatSession(id=new_id(), created_at=created, status=status, channel="web", **columns)
        self.db.add(session)
        return session

    def message(self, session: ChatSession, offset_s: int, role: str, content: str, **columns: Any) -> None:
        """Реплика через offset_s секунд от начала диалога; в columns — цитаты и meta."""
        self.db.add(
            Message(
                session_id=session.id,
                role=role,
                content=content,
                created_at=session.created_at + timedelta(seconds=offset_s),
                **columns,
            )
        )

    def answers(self, solved: bool, clear: bool) -> tuple[bool | None, bool | None]:
        """Ответы на «Проблема решена?» и «Понятно объяснили?»; часть людей их пропускает."""
        if self.rng.random() < SKIP_ANSWERS_SHARE:
            return None, None
        return solved, clear

    def feedback(
        self,
        session: ChatSession,
        rating: int,
        comment: str,
        operator_id: str | None = None,
        answers: tuple[bool | None, bool | None] = (None, None),
    ) -> None:
        """Отзыв об обращении; без оператора оценивают ИИ-ассистента."""
        # Отзыв оставляют после завершения обращения, но не позже «сейчас»
        after = (session.closed_at or session.created_at + timedelta(minutes=28)) + timedelta(minutes=2)
        self.db.add(
            Feedback(
                session_id=session.id,
                rating=rating,
                comment=comment,
                specialist_kind="operator" if operator_id else "ai",
                operator_id=operator_id,
                sentiment=sentiment_score(comment) if comment else None,
                solved=answers[0],
                clear=answers[1],
                created_at=min(after, self.now),
            )
        )


def _resolved_by_bot(writer: _DialogWriter, articles: list[KBArticle]) -> None:
    """Вопросы, на которые бот ответил сам; часть собеседников оставила отзыв."""
    rng = writer.rng
    for _ in range(RESOLVED_DIALOGS):
        art = rng.choice(articles)
        created = writer.moment(WORKDAY_MINUTES)
        s = writer.session(
            created, "resolved", closed_at=created + timedelta(minutes=10), topic=art.category[:TOPIC_CHARS]
        )
        confidence = round(rng.uniform(0.45, 0.95), 3)
        reviewed = rng.random() < FEEDBACK_SHARE
        praised = rng.random() < (HARD_TOPIC_PRAISE_SHARE if art.category == HARD_TOPIC else PRAISE_SHARE)
        meta: dict[str, Any] = {
            "answered": True,
            "confidence": confidence,
            "escalated": False,
            "latency_ms": rng.randint(350, 2200),
        }
        if reviewed and rng.random() < THUMB_SHARE:
            meta["rating"] = 1 if praised else -1
        question = writer.paraphrase(art.question)
        # Отзыв считается раньше, чем пишутся реплики: причина 👎 под ответом
        # берётся из того же «понял ли». Порядок обращений к генератору прежний —
        # демо-набор от этого не меняется
        if reviewed and praised:
            clear = rng.random() >= CONTENT_BUT_UNCLEAR_SHARE
            comment = rng.choice(data.POSITIVE_COMMENTS + [""])
            writer.feedback(s, rng.choice([4, 5, 5]), comment, answers=writer.answers(True, clear))
        elif reviewed:
            clear = rng.random() < 0.5  # noqa: PLR2004 — «непонятно» у половины недовольных
            comment = rng.choice(data.NEGATIVE_COMMENTS)
            writer.feedback(s, rng.choice([2, 3]), comment, answers=writer.answers(False, clear))
            if meta.get("rating") == -1:
                meta["rating_reason"] = "not_working" if clear else "unclear"
        writer.message(s, 0, "user", question)
        writer.message(
            s,
            4,
            "assistant",
            art.answer,
            citations=[
                {
                    "article_id": art.id,
                    "question": art.question,
                    "category": art.category,
                    "score": confidence,
                }
            ],
            meta=meta,
        )


def _escalated_to_operators(writer: _DialogWriter, articles: list[KBArticle]) -> None:
    """Эскалации на операторов линий: вопрос, перевод, подключение оператора, его ответ и отзыв."""
    # Слабого исключаем из штатного расписания линий: он на той же второй линии,
    # что и Дмитрий, и раньше словарь «линия → оператор» отдавал ему все
    # обращения линии, а Дмитрию — ни одного
    operators = {
        line: uid.ljust(32, "0") for uid, _, line in data.OPERATORS if uid.ljust(32, "0") != WEAK_OPERATOR
    }
    names = {uid.ljust(32, "0"): name for uid, name, _ in data.OPERATORS}
    # На импортированной базе линия у статей могла проставиться иначе, чем у
    # сидовой: если второй и третьей линии там нет вовсе, берём что есть —
    # без эскалаций рейтинг специалистов не наберётся, а он и есть весь смысл.
    escalatable = [a for a in articles if a.line in (2, 3)] or articles
    rng = writer.rng
    for i in range(ESCALATED_DIALOGS):
        art = rng.choice(escalatable)
        weak = i % WEAK_OPERATOR_EVERY == 0
        op_id = WEAK_OPERATOR if weak else operators.get(art.line, operators[max(operators)])
        created = writer.moment(WORKDAY_MINUTES, WEAK_OPERATOR_DAYS if weak else DEMO_DAYS)
        reply_s = rng.randint(*(WEAK_REPLY_SECONDS if weak else REPLY_SECONDS))
        reclaimed = i in RECLAIMED_FROM_WEAK
        s = writer.session(
            created,
            "resolved",
            line=art.line,
            operator_id=op_id,
            escalation_reason=art.question,
            escalated_at=created + timedelta(seconds=30),
            closed_at=created + timedelta(seconds=reply_s + rng.randint(*CLOSE_AFTER_SECONDS)),
            topic=art.category[:TOPIC_CHARS],
            reclaimed_count=1 if reclaimed else 0,
            last_operator_id=WEAK_OPERATOR if reclaimed else None,
        )
        if reclaimed:
            # Та же запись, что оставляет диспетчер: по ней отчёт считает брошенные
            writer.db.add(
                AuditLog(
                    actor="dispatcher",
                    action="session.reclaimed",
                    entity=s.id,
                    payload={
                        "operator_id": WEAK_OPERATOR,
                        "silence_seconds": RECLAIM_SILENCE_SECONDS,
                        "reclaimed_count": 1,
                    },
                    created_at=created + timedelta(seconds=RECLAIM_SILENCE_SECONDS),
                )
            )
        writer.message(s, 0, "user", writer.paraphrase(art.question))
        writer.message(
            s,
            4,
            "assistant",
            f"Перевожу ваше обращение на оператора линии {art.line}.",
            meta={
                "answered": False,
                "escalated": True,
                "confidence": 0.2,
                "latency_ms": rng.randint(400, 1500),
            },
        )
        writer.message(
            s,
            reply_s - 30,
            "system",
            f"Оператор {names[op_id]} подключился к диалогу.",
            meta={"kind": "operator_joined"},
        )
        writer.message(s, reply_s, "operator", art.answer, meta={"operator_id": op_id})
        if weak:
            answers = writer.answers(rng.random() < WEAK_SOLVED_SHARE, rng.random() < WEAK_CLEAR_SHARE)
            comment = rng.choice(data.WEAK_OPERATOR_COMMENTS)
            writer.feedback(s, rng.choice([1, 2, 2, 3]), comment, op_id, answers)
        else:
            answers = writer.answers(True, rng.random() >= CONTENT_BUT_UNCLEAR_SHARE)
            comment = rng.choice(data.POSITIVE_COMMENTS + ["", ""])
            writer.feedback(s, rng.choice([4, 5, 5]), comment, op_id, answers)


def _closed_for_violations(writer: _DialogWriter) -> None:
    """Обращения, закрытые за нецензурную лексику."""
    for text in data.VIOLATION_MESSAGES * VIOLATION_REPEATS:
        created = writer.moment()
        s = writer.session(created, "closed_violation", closed_at=created + timedelta(minutes=1))
        writer.message(s, 0, "user", text)
        writer.message(
            s,
            2,
            "system",
            "Обращение завершено: в сообщении обнаружена нецензурная лексика.",
            meta={"kind": "violation"},
        )


def _outside_kb(writer: _DialogWriter, no_answer: str) -> None:
    """Вопросы вне базы знаний: бот честно отказал, а сами вопросы легли в пробелы БЗ."""
    rng = writer.rng
    for question in data.OFF_KB_QUESTIONS * OFF_KB_REPEATS:
        created = writer.moment()
        s = writer.session(created, "resolved", closed_at=created + timedelta(minutes=5))
        writer.message(s, 0, "user", question)
        writer.message(
            s,
            4,
            "assistant",
            no_answer,
            meta={
                "answered": False,
                "escalated": False,
                "confidence": round(rng.uniform(0.05, 0.3), 3),
                "latency_ms": rng.randint(300, 1200),
            },
        )
    for question in data.OFF_KB_QUESTIONS:
        writer.db.add(
            KBGap(
                query_norm=" ".join(tokenize(question))[:500],
                query_example=question,
                hits=3,
                best_score=round(rng.uniform(0.05, 0.3), 3),
            )
        )


async def _seed_dialogs(rt: AppRuntime, articles: list[KBArticle]) -> None:
    async with rt.db.session_factory() as db:
        writer = _DialogWriter(db, random.Random(42), datetime.now(UTC))
        _resolved_by_bot(writer, articles)
        _escalated_to_operators(writer, articles)
        _closed_for_violations(writer)
        _outside_kb(writer, str(rt.dyn.get("agent.no_answer_message")))
        db.add(
            AuditLog(
                actor="system",
                action="seed",
                entity="database",
                payload={"articles": len(articles), "note": "синтетические демо-данные"},
            )
        )
        await db.commit()

    for a in articles[:20]:
        await rt.cache.zincrby(TOP_QUERIES_KEY, random.Random(a.id).randint(1, 25), a.question)

    log.info("Демо-данные готовы: %d статей, диалоги, отзывы, пробелы БЗ", len(articles))
