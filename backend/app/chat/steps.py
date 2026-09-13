"""Шаги обработки сообщения: каждый решает свою развилку и уходит.

Шаг отвечает на один вопрос — «это моя ситуация?» — и либо возвращает готовый
ответ пользователю, либо None, и тогда очередь идёт дальше. Новая логика в
диалоге становится новым шагом, а не ещё одной веткой внутри общего метода.
"""

from __future__ import annotations

import asyncio
import difflib
import logging
import re
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import select

from app.chat import source_conflicts
from app.chat.turn import Turn
from app.models import Message
from app.services import answer_guard, guardrails, kbkind, querytext, verbatim
from app.services import audience as audience_of
from app.services.agent import wants_operator as _wants_operator
from app.services.layout import detect_wrong_layout
from app.services.mcp import looks_like_product_request, wants_external
from app.services.retrieval import RetrievedDoc, tokenize

if TYPE_CHECKING:
    from app.services.chat import ChatService
    from app.services.guardrails import ProfanityVerdict, ToxicityVerdict

log = logging.getLogger(__name__)


async def _maybe_fix_layout(svc: ChatService, session_id: str, msg: Message, text: str) -> str:
    """«Rfr pfhtubcnhbhjdfnmcz» → «Как зарегистрироваться», «регестрация» → «регистрация».

    Сообщение уже показано пользователю как есть — правим его на месте и сообщаем об
    этом отдельным событием, чтобы интерфейс успел проиграть починку. Оригинал остаётся
    в meta: человек должен видеть, что именно система изменила в его словах.

    Раскладка и опечатки чинятся одним проходом: одна запись в базу и одно событие,
    иначе анимация проигралась бы на одном пузыре дважды подряд.
    """
    dyn = svc.rt.dyn
    fixed, kinds = text, []
    if dyn.get("guardrails.layout_fix_enabled"):
        converted = detect_wrong_layout(text, float(dyn.get("guardrails.layout_fix_threshold")))
        if converted and converted != text:
            fixed, kinds = converted, ["layout"]
    if dyn.get("guardrails.typo_fix_enabled"):
        # Словарь — слова самой базы знаний, та же правка, которой поиск и так чинит
        # запрос: теперь человек видит ровно то, по чему бот искал ответ
        spelled = svc.rt.retriever.correct_display(fixed)
        if spelled != fixed:
            fixed = spelled
            kinds.append("typo")
    if not kinds:
        return text

    async with svc.rt.db.session_factory() as db:
        row = await db.get(Message, msg.id)
        if row is None:  # реплику успели удалить вместе с диалогом — править нечего
            return text
        row.content = fixed
        row.meta = {**(row.meta or {}), **{f"{kind}_fixed": True for kind in kinds}, "original": text}
        await db.commit()

    await svc.rt.ws.publish(
        f"session:{session_id}",
        {
            "type": "layout_fixed",
            "message_id": msg.id,
            "original": text,
            "text": fixed,
            "kinds": kinds,
        },
    )
    delay_ms = int(dyn.get("chat.layout_fix_delay_ms") or 0)
    if delay_ms > 0:
        await asyncio.sleep(delay_ms / 1000)
    log.info("Исправлено (%s) в сессии %s: %r -> %r", "+".join(kinds), session_id, text[:60], fixed[:60])
    return fixed


async def _maybe_verbatim(svc: ChatService, turn: Turn) -> dict[str, Any] | None:
    """Готовый авторский ответ отдаётся как есть, без обращения к модели."""
    session_id, text, docs, started = turn.session_id, turn.text, turn.probe, turn.started
    if not docs:
        return None
    # Провенанс берём на всю выдачу, а не на первую статью: дословно совпавшая
    # пара может лежать второй, и без её происхождения выбирать было бы не из чего.
    # Запрос всё равно один — _provenance_of принимает список.
    origins, visuals = await svc._provenance_of([doc.article_id for doc in docs])
    top = verbatim.pick_verbatim(svc.rt.dyn, text, docs, origins, visuals)
    if top is None:
        return None

    # Ответ-картинка: текста в статье нет, и он собирается из подписи. Совсем
    # пустой пузырь в ленте неотличим от сбоя. Дословный ответ идёт мимо модели,
    # а значит, и мимо её страховки: разметку тикет-системы из статей, загруженных
    # до чистки импорта («<m #ffff00>»), снимаем здесь.
    images = visuals.get(top.article_id) or []
    answer = answer_guard.sanitize(top.answer or "") or verbatim.visual_answer_text(svc.rt.dyn, images)

    citations = [{"article_id": top.article_id, "question": top.question, "score": round(top.score, 3)}]
    conflict = await source_conflicts.maybe_conflict_reply(svc, turn, answer, citations)
    if conflict is not None:
        return conflict
    await svc._attach_citation_images(citations)
    latency_ms = int((time.perf_counter() - started) * 1000)
    await svc._stream_answer(session_id, answer)
    meta = {
        **turn.flags,
        "question": text,
        "reply_to": turn.message_id,
        "audience": turn.role,
        "page_context": turn.page_text,
        "confidence": top.score,
        "answered": True,
        "escalated": False,
        # Видно и в интерфейсе, и в выгрузке: этот ответ не сочинён моделью,
        # а взят из источника слово в слово.
        "tool_trace": ["verbatim"],
        "verbatim": True,
        "latency_ms": latency_ms,
    }
    msg = await svc._save_message(session_id, "assistant", answer, citations=citations, meta=meta)
    await svc.rt.ws.publish(f"session:{session_id}", svc._msg_event(msg))
    await svc._track_query(text)
    log.info("Дословный ответ в сессии %s по статье %s (score=%.3f)", session_id, top.article_id, top.score)
    return {
        "status": "answered",
        "answer": answer,
        "citations": citations,
        "confidence": top.score,
        "escalated": False,
        "latency_ms": latency_ms,
    }


async def _last_assistant_meta(svc: ChatService, session_id: str) -> dict[str, Any]:
    """meta последней реплики ассистента; пустой словарь, если он ещё молчал.

    Уточнение, вопрос о роли и предложение оператора — вопросы бота к человеку,
    и следующая реплика человека читается как ответ на них. Чей это был вопрос,
    видно только по последней реплике ассистента.
    """
    async with svc.rt.db.session_factory() as db:
        last = (
            (
                await db.execute(
                    select(Message)
                    .where(Message.session_id == session_id, Message.role == "assistant")
                    .order_by(Message.created_at.desc())
                    .limit(1)
                )
            )
            .scalars()
            .first()
        )
    return dict(last.meta or {}) if last else {}


async def _pending_audience_question(svc: ChatService, session_id: str) -> str | None:
    """Вопрос, на который бот ответил «вы поставщик или заказчик?», если это
    было последней репликой ассистента."""
    meta = await _last_assistant_meta(svc, session_id)
    if meta.get("kind") != "clarify_audience":
        return None
    question = str(meta.get("question") or "").strip()
    return question or None


def _same_question(first: str, second: str) -> float:
    """Насколько два вопроса — один и тот же, только для разных ролей: 0..1.

    Без `svc` — как и `_clarify_text` рядом: сравнение двух строк ничего не
    спрашивает у диалога. На классе метод остаётся статическим, каким был до
    расшивки: его звали и от класса, `ChatService._same_question(a, b)`.

    Меньшая из двух долей общих слов: «Как зарегистрироваться на портале?» и
    «Как зарегистрироваться на портале в качестве заказчика?» совпадают почти
    целиком, а «Что такое котировочная сессия?» и «Как создать котировочную
    сессию?» — лишь наполовину, и роль тут не решает, чем они различаются.
    """
    a, b = set(tokenize(first)), set(tokenize(second))
    if not a or not b:
        return 0.0
    common = len(a & b)
    return min(common / len(a), common / len(b))


# Переспрашивать есть смысл, только когда выбирать есть из чего — хотя бы из двух
_MIN_CHOICES = 2
_EXACT_COMMON_OVERLAP = 0.95
_FUZZY_WORD_OVERLAP = 0.65


def _retrieval_rank(doc: RetrievedDoc) -> float:
    """Шкала, по которой фактически упорядочил кандидатов текущий поиск."""
    if doc.rerank_score is not None:
        return doc.rerank_score
    return doc.kw_score or doc.score


def _fuzzy_question_overlap(first: str, second: str) -> float:
    """Совпадение с учётом однокоренных форм вроде «зарегистрироваться/регистрация»."""
    left, right = set(tokenize(first)), set(tokenize(second))
    if not left or not right:
        return 0.0
    remaining = set(right)
    matched = 0
    for word in sorted(left):
        candidate = max(
            remaining,
            key=lambda other: difflib.SequenceMatcher(None, word, other).ratio(),
            default=None,
        )
        if candidate is None:
            continue
        similarity = difflib.SequenceMatcher(None, word, candidate).ratio()
        if similarity >= _FUZZY_WORD_OVERLAP:
            matched += 1
            remaining.remove(candidate)
    return min(matched / len(left), matched / len(right))


async def _maybe_clarify_audience(svc: ChatService, turn: Turn) -> dict[str, Any] | None:
    """«Вы поставщик или заказчик?» — когда роль не выбрана, а спорят две статьи
    для разных сторон сделки.

    Условия намеренно узкие: обе статьи адресные (не «для всех»), их вопросы
    почти совпадают, оценки близкие. Иначе это два разных вопроса, и роль в
    них ничего не решает — переспрашивать было бы допросом.
    """
    session_id, text, docs, started = turn.session_id, turn.text, turn.probe, turn.started
    audience = turn.role
    if audience_of.is_known(audience) or not docs or len(docs) < _MIN_CHOICES:
        return None
    if (
        not svc.rt.dyn.get("clarify.audience_enabled")
        or _wants_operator(text)
        or wants_external(text, svc.rt.dyn.get("mcp.explicit_keywords"))
        or looks_like_product_request(text, str(svc.rt.dyn.get("mcp.product_intent") or ""))
    ):
        return None
    overlap = float(svc.rt.dyn.get("clarify.audience_min_overlap"))
    suppliers = [doc for doc in docs if doc.audience == audience_of.SUPPLIER]
    customers = [doc for doc in docs if doc.audience == audience_of.CUSTOMER]
    pairs = [
        (supplier, customer)
        for supplier in suppliers
        for customer in customers
        if svc._same_question(supplier.question, customer.question) >= overlap
        and max(
            _fuzzy_question_overlap(text, supplier.question),
            _fuzzy_question_overlap(text, customer.question),
        )
        >= overlap
    ]
    if not pairs:
        return None
    # Точное общее руководство уже даёт однозначный ответ и не зависит от роли.
    # Ролевые статьи ниже в выдаче в таком случае не должны превращать ответ в допрос.
    if any(
        doc.audience == audience_of.ANY and svc._same_question(text, doc.question) >= _EXACT_COMMON_OVERLAP
        for doc in docs
    ):
        return None
    pair = max(
        pairs,
        key=lambda item: (
            min(_retrieval_rank(doc) for doc in item),
            max(_retrieval_rank(doc) for doc in item),
            tuple(doc.article_id for doc in item),
        ),
    )
    top, second = sorted(pair, key=lambda doc: (_retrieval_rank(doc), doc.article_id), reverse=True)
    if top.score <= 0 or top.score < float(svc.rt.dyn.get("agent.confidence_threshold")):
        return None
    # Близость меряем по совпадению со статьёй целиком, а не по показываемой
    # уверенности: та опирается на заголовок, а заголовки адресных статей
    # различаются ровно словами роли («… в качестве заказчика»). По уверенности
    # вопрос одной стороны всегда обгонял бы вопрос другой, и роль не
    # спрашивалась бы никогда. Совпадение статей между собой проверяется ниже.
    lead, runner = _retrieval_rank(top), _retrieval_rank(second)
    gap = (lead - runner) / lead if lead > 0 else 1.0
    if gap > float(svc.rt.dyn.get("clarify.max_relative_gap")):
        return None
    if await svc._already_clarified(session_id):
        return None

    answer = str(svc.rt.dyn.get("clarify.audience_message"))
    options = audience_of.clarify_options()
    latency_ms = int((time.perf_counter() - started) * 1000)
    await svc._stream_answer(session_id, answer)
    msg = await svc._save_message(
        session_id,
        "assistant",
        answer,
        meta={
            "kind": "clarify_audience",
            "options": options,
            "question": text,
            "confidence": top.score,
            "answered": False,
            "escalated": False,
            "latency_ms": latency_ms,
        },
    )
    await svc.rt.ws.publish(f"session:{session_id}", svc._msg_event(msg))
    await svc._track_query(text)
    log.info("Уточнение роли в сессии %s: %r против %r", session_id, top.question[:50], second.question[:50])
    return {
        "status": "clarify",
        "answer": answer,
        "options": options,
        "citations": [],
        "confidence": top.score,
        "escalated": False,
        "latency_ms": latency_ms,
    }


def _word_overlap(first: str, second: str) -> float:
    """Доля общих слов двух текстов (Jaccard по основам слов): 0..1.

    Симметричная, в отличие от `_same_question`: здесь спрашиваем не «входит ли
    один вопрос в другой», а «одно ли это и то же». Пустой текст ни на что не
    похож — ответ-картинка без текста отличается от любого текстового.
    """
    a, b = set(tokenize(first)), set(tokenize(second))
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _distinct_subjects(first: RetrievedDoc, second: RetrievedDoc) -> bool:
    """Две статьи одного шаблона, но про разное: «заблокировать» и «разблокировать».

    Инструкции портала пишутся по образцу, и доля общих слов у таких ответов
    под 0.9, хотя действия противоположные. Различаются они ровно тем словом,
    ради которого человек спрашивал. Поэтому смотрим не на долю, а на
    различающую часть: у каждого ответа есть слова, которых нет в другом, и
    хотя бы у одной статьи эти слова стоят и в её вопросе. Тогда разница в
    ответах и есть разница в предмете. Пересказ одного ответа другими словами
    так не выглядит: либо один ответ целиком входит в другой, либо лишние
    слова вопросу чужие. На portal-kb правило развело 37 из 56 пар с
    совпадением ответов от 0.6: блокировку и разблокировку, физлицо и ИП,
    РДИК_0212 и РДИК_0213.
    """
    q_first, q_second = set(tokenize(first.question)), set(tokenize(second.question))
    a_first, a_second = set(tokenize(first.answer)), set(tokenize(second.answer))
    only_first, only_second = a_first - a_second, a_second - a_first
    if not (only_first and only_second):
        return False
    return bool((q_first - q_second) & only_first or (q_second - q_first) & only_second)


def _same_answer(first: RetrievedDoc, second: RetrievedDoc, overlap: float) -> bool:
    """Одна ли это инструкция: общих слов не меньше порога и предмет не различается."""
    return _word_overlap(first.answer, second.answer) >= overlap and not _distinct_subjects(first, second)


def _interchangeable(first: RetrievedDoc, second: RetrievedDoc, overlap: float) -> bool:
    """Два варианта уточнения, из которых человеку нечего выбирать.

    Совпадают ответы или сами формулировки вопросов, и шаблон не прячет за
    этим разные действия. Две кнопки «Как заблокировать…» и «Как
    разблокировать…» — настоящий выбор, склеивать их нельзя.
    """
    alike = (
        _word_overlap(first.answer, second.answer) >= overlap
        or _word_overlap(first.question, second.question) >= overlap
    )
    return alike and not _distinct_subjects(first, second)


# Приветствие в начале «вопроса» — верный признак сырого обращения, а не
# формулировки статьи: «Добрый вечер. Опять повторяется одна и та же проблема…»
_GREETING = re.compile(
    r"^\W*(?:добр\w*\s+(?:день|утр\w*|вечер\w*|времен\w*|ноч\w*)|здравству\w*|привет\w*|уважаем\w*)",
    re.IGNORECASE,
)
# Номер заявки, ИНН или телефон: пять цифр подряд или «№ 12». В формулировках
# статей их не бывает, а «44-ФЗ» и год под правило не попадают.
_TICKET_NUMBER = re.compile(r"\d{5,}|№\s*\d")
# Цитата — текст ошибки с экрана («…Пожалуйста, выберите другой»), а не голос
# спрашивающего. Проверки ниже смотрят на вопрос без цитат.
_QUOTED = re.compile(r"«[^»]*»|\"[^\"]*\"|“[^”]*”|&quot;.*?&quot;")
# Просьба к поддержке и спешка: в формулировке статьи никто никого не просит
_APPEAL = re.compile(r"\b(?:помогите|подскажите|прошу|просим|просьба|пожалуйста|срочно)\b", re.IGNORECASE)
# Первое лицо: «у нашей компании», «мне нужно», «мною выиграна». Кнопку
# выбирают как свой вопрос, а подробности чужой организации в нём чужие.
_FIRST_PERSON = re.compile(
    r"\b(?:я|мы|мне|меня|мной|мною|нам|нас|нами"
    r"|мой|моя|моё|мое|мои|моего|моей|моему|моим|моих|мою|моём|моем"
    r"|наш|наша|наше|наши|нашего|нашей|нашему|нашим|наших|нашу|нашем)\b",
    re.IGNORECASE,
)
# Рассказ о своём случае: «вчера», «опять», «скрин прилагаю», «данная ошибка».
# «Данным/данных» сюда не входят: это ещё и «данные».
_NARRATIVE = re.compile(
    r"\b(?:вчера|сегодня|опять|снова|вроде|скрин\w*|данн(?:ый|ая|ое|ого|ой|ую|ом))\b", re.IGNORECASE
)
# Дата «22.05» — подробность одной заявки
_DATE = re.compile(r"\b\d{1,2}\.\d{1,2}(?:\.\d{2,4})?\b")
# Крик: «НА КАКОМ ОСНОВАНИИ». Одно слово капсом — ещё сокращение или название
# («МЧД», «КРИПТОПРО»), три подряд — уже интонация.
_SHOUT = re.compile(r"\b[А-ЯЁ]{2,}\s+[А-ЯЁ]{2,}\s+[А-ЯЁ]{2,}\b")
# Служебный идентификатор: латиница вперемешку с цифрами («E_7UH8CgvH»)
_SERVICE_ID = re.compile(r"\b(?=[A-Za-z0-9_]*[A-Za-z])(?=[A-Za-z0-9_]*\d)[A-Za-z0-9_]{6,}\b")
# Второе предложение или восклицание: «Не можем подписать оферту. Помогите.»
# После точки — буква, а не цифра: «ст. 93» остаётся одним предложением.
_SENTENCE_BREAK = re.compile(r"[.!?…]\s+[^\W\d_]|[.!?…][А-ЯЁA-Z]|!")
_CYRILLIC_WORD = re.compile(r"[а-яё]+")
# Вопрос из одного слова — рубрика выгрузки («Консультация», «Полномочия»)
_MIN_OPTION_WORDS = 2


def _narrates(text: str) -> bool:
    """Рассказ о своём случае вместо вопроса: первое лицо или прошлое в утверждении.

    Глагол в первом лице («можем», «нажимаю») выдаёт рассказ всегда. Прошедшее
    время — только в законченном утверждении с точкой: «Направили приглашение
    в ЭДО.» и «Котировочная сессия состоялась.» — куски чужой истории, а
    «Почему появился другой победитель?» и заголовок «СТЕ подтверждена, а номер
    не появился» — нормальные формулировки.
    """
    if _FIRST_PERSON.search(text) or _NARRATIVE.search(text):
        return True
    morph = querytext._morph()
    if morph is None:  # без словаря судим только по словам выше
        return False
    statement = text.rstrip().endswith(".")
    for word in _CYRILLIC_WORD.findall(text.lower()):
        tag = morph.parse(word)[0].tag
        if "VERB" in tag and ("1per" in tag or (statement and "past" in tag)):
            return True
    return False


def _presentable_question(question: str, max_chars: int) -> bool:
    """Годится ли вопрос статьи в кнопку уточнения.

    Вариант — это чужая формулировка, которую человеку предлагают выбрать как
    свою. Сырое обращение из выгрузки поддержки («Добрый вечер… скрин
    прилагаю…», «Не можем подписать оферту. Помогите.») под этим не подпишется
    никто, а номер чужой заявки в кнопке — ещё и утечка. Многоточие в конце
    выдаёт текст, обрезанный на полуслове.

    Правила подобраны на вопросах записей после парсера выгрузки: из 447
    ответов проходят около половины — формулировки вида «Как…?», «Ошибка
    РДИК_0161: …», «Не приходят уведомления на почту». Из 326 авторских
    вопросов portal-kb отсеиваются единицы, и те написаны от первого лица.
    """
    text = question.strip()
    if not text or len(text) > max_chars or "\n" in text or text.endswith(("…", "...")):
        return False
    if len(text.split()) < _MIN_OPTION_WORDS or _GREETING.search(text) or _TICKET_NUMBER.search(text):
        return False
    plain = _QUOTED.sub("«»", text)
    if any(pattern.search(plain) for pattern in (_APPEAL, _DATE, _SHOUT, _SERVICE_ID)):
        return False
    if _SENTENCE_BREAK.search(plain.rstrip(".!?… ")):
        return False
    return not _narrates(plain)


def _clarify_options(svc: ChatService, docs: list[RetrievedDoc], limit: int) -> list[dict[str, Any]]:
    """Варианты выбора — разные по смыслу ответы с человеческими вопросами.

    Раньше варианты разводились по категориям, но категории выгрузки поддержки
    шумные: одна проблема лежит в трёх рубриках, и человек получал на выбор три
    формулировки одного и того же. Теперь вариант отбрасывается, если его ответ
    или вопрос почти совпадает с уже выбранным, — выбор между одинаковыми
    ответами не уточнение, а лишний шаг. Шаблонные инструкции про разные
    действия при этом остаются обе (_interchangeable). Первым берётся самый
    уверенный.

    Только ответы: случай для оператора и справочник кнопкой быть не могут —
    выбравший их человек получил бы чужую отписку или рубрику без инструкции.
    """
    dyn = svc.rt.dyn
    max_chars = int(dyn.get("clarify.option_max_chars"))
    overlap = float(dyn.get("clarify.same_answer_overlap"))
    chosen: list[RetrievedDoc] = []
    for doc in docs:
        if doc.kind != kbkind.ANSWER or not _presentable_question(doc.question, max_chars):
            continue
        if any(_interchangeable(doc, taken, overlap) for taken in chosen):
            continue
        chosen.append(doc)
        if len(chosen) >= limit:
            break
    return [{"text": doc.question.strip(), "article_id": doc.article_id} for doc in chosen]


async def _already_clarified(svc: ChatService, session_id: str) -> bool:
    """Уточнять дважды подряд нельзя — это уже не помощь, а допрос.

    Считается любое уточнение: и по теме, и по роли. Человек, только что
    ответивший «я заказчик», не должен тут же получить «а оферту или сессию?».
    """
    meta = await _last_assistant_meta(svc, session_id)
    return meta.get("kind") in ("clarify", "clarify_audience")


def _relative_gap(top: RetrievedDoc, other: RetrievedDoc) -> float:
    """Насколько статья отстаёт от первой — доля от оценки первой.

    По той же шкале, что задала порядок выдачи (rank_score): после реранкера
    это его вероятности, без него — уверенность поиска.
    """
    return (top.rank_score - other.rank_score) / top.rank_score if top.rank_score > 0 else 1.0


async def _maybe_clarify(svc: ChatService, turn: Turn) -> dict[str, Any] | None:
    """Уточняющий вопрос вместо ответа наугад.

    Срабатывает в узкой полосе: совпадение не уверенное, но и не пустое, а
    первые две статьи стоят вплотную и дают РАЗНЫЕ ответы. Именно там бот
    отвечает про оферту человеку, который спрашивал про сессию. Разные именно
    ответы, а не категории: рубрики выгрузки поддержки шумные, и две статьи с
    одним ответом в разных рубриках раньше превращались в «выберите из двух
    одинаковых». Когда ответы совпадают, выбирать не из чего — отвечаем лучшей.

    Полоса узкая намеренно. Главный риск фичи — переспрашивать там, где всё
    понятно: такой бот раздражает сильнее, чем изредка ошибающийся, и
    доверие теряется быстрее. Поэтому все три границы вынесены в настройки и
    подбираются на реальных диалогах, а не на глаз.

    Просьбу позвать человека не трогаем ни при каких оценках: переспрашивать
    в ответ на «позовите оператора» — издевательство.
    """
    session_id, text, docs, started = turn.session_id, turn.text, turn.probe, turn.started
    if not svc.rt.dyn.get("clarify.enabled") or not docs or len(docs) < _MIN_CHOICES:
        return None
    if _wants_operator(text):
        return None

    top, second = docs[0], docs[1]
    # Нижняя граница — порог ответа, а не своя настройка: ниже него база
    # знаний отказывает, выдача там шум, и близкие оценки говорят лишь о том,
    # что ответа нет. Уточнять в этой зоне значит подсовывать человеку выбор
    # из двух случайных статей вместо честного «не знаю».
    lower = float(svc.rt.dyn.get("agent.confidence_threshold"))
    upper = float(svc.rt.dyn.get("clarify.upper_score"))
    if top.score <= 0 or not lower <= top.score < upper:
        return None
    # Одинаковые ответы — выбирать не из чего. Но не шаблонные инструкции про
    # разные действия: там общих слов под 0.9, а выбор настоящий.
    if _same_answer(top, second, float(svc.rt.dyn.get("clarify.same_answer_overlap"))):
        return None
    # Разрыв меряем по той же шкале, что задала порядок. После реранкера это его
    # вероятности: по гибридной уверенности вторая статья может стоять «выше»
    # первой, разрыв выходил отрицательным и проверку близости проходил всегда.
    max_gap = float(svc.rt.dyn.get("clarify.max_relative_gap"))
    if _relative_gap(top, second) > max_gap:
        return None

    # Варианты — только из тех, кто стоит вплотную к первой: статья с третьего
    # места, далеко отставшая, не соперник, а шум, и предлагать её наравне с
    # первой значит сбивать человека.
    close = [doc for doc in docs if _relative_gap(top, doc) <= max_gap]
    options = svc._clarify_options(close, int(svc.rt.dyn.get("clarify.max_options")))
    # Лучшая статья обязана быть среди вариантов. Если её вопрос не годится в
    # кнопку (сырое обращение, номер заявки), выбор без неё прячет самый
    # вероятный ответ — лучше ответить ею, чем переспрашивать мимо.
    if len(options) < _MIN_CHOICES or options[0]["article_id"] != top.article_id:
        return None
    if await svc._already_clarified(session_id):
        return None

    answer = _clarify_text(str(svc.rt.dyn.get("clarify.message")), [o["text"] for o in options])
    latency_ms = int((time.perf_counter() - started) * 1000)
    await svc._stream_answer(session_id, answer)
    msg = await svc._save_message(
        session_id,
        "assistant",
        answer,
        meta={
            "kind": "clarify",
            "options": options,
            "confidence": top.score,
            # Уточнение — не автоответ: засчитывать его в долю решённых без
            # оператора значило бы отчитываться за работу, которая ещё не
            # сделана. И не пробел базы знаний: ответ есть, неясен вопрос.
            "answered": False,
            "escalated": False,
            "latency_ms": latency_ms,
        },
    )
    await svc.rt.ws.publish(f"session:{session_id}", svc._msg_event(msg))
    await svc._track_query(text)
    log.info(
        "Уточнение в сессии %s: top=%.3f second=%.3f (%s / %s)",
        session_id,
        top.score,
        second.score,
        top.category,
        second.category,
    )
    return {
        "status": "clarify",
        "answer": answer,
        "options": options,
        "citations": [],
        "confidence": top.score,
        "escalated": False,
        "latency_ms": latency_ms,
    }


# --- предложение оператора ---

# Ответ на «передать обращение оператору?» — короткая реплика целиком из слов
# ниже. «Да, а как подать заявку?» ответом не будет: в ней есть слова мимо
# словаря, и она идёт обычным путём как новый вопрос. Буква «ё» здесь уже «е».
#: Просьба перевести — согласие на любое предложение. Только повелительные формы:
#: «передать» и «переведите» бывают и в вопросах («как передать УПД»,
#: «переведите страницу на английский»), поэтому глагол решает лишь в короткой реплике.
_REQUEST_VERBS = frozenset(
    {"передайте", "передай", "переведите", "переводите", "соедините", "подключите", "позовите", "позови"}
    | {"зовите"}
)
#: Прямое «да» на вопрос бота
_YES = frozenset({"да", "давайте", "давай", "конечно", "согласен", "согласна"})
#: Согласие, которое легко спутать с вежливостью: «хорошо» — это и «передайте», и «понял»
_SOFT_YES = frozenset({"хорошо", "ладно", "ок", "окей", "ok", "okay", "ага", "угу", "можно"})
_GRATITUDE = frozenset({"спасибо", "благодарю", "благодарен", "благодарна", "спс", "большое"})
_NEGATIONS = frozenset({"нет", "не", "неа", "отмена"})
_OFFER_FILLER = frozenset(
    {"пожалуйста", "оператор", "оператору", "оператора", "оператором", "специалист", "специалиста"}
    | {"специалисту", "человек", "человека", "человеку", "живой", "живого", "живому", "обращение"}
    | {"его", "меня", "мне", "нужно", "надо", "нужен", "нужна", "хочу", "пусть", "тогда", "буду"}
    | {"стоит", "сам", "сама", "уже", "пока", "звать", "передавать", "передавайте", "срочно", "лучше"}
    | {"без"}
)
_OFFER_WORDS = _REQUEST_VERBS | _YES | _SOFT_YES | _GRATITUDE | _NEGATIONS | _OFFER_FILLER
# Отрицание при самой просьбе: «не надо», «оператор не нужен», «не зовите»,
# «без оператора». Одно «не» в реплике ещё ничего не значит: в «позовите
# оператора, у меня не работает ЭЦП» оно про подпись, а не про оператора.
_NEGATED_REQUEST = re.compile(
    r"\bне\s+(?:[а-я]+\s+)?(?:нуж\w*|надо|зов\w*|звать|переда\w*|перевод\w*|перевест\w*"
    r"|соедин\w*|подключ\w*|стоит|хочу|беспоко\w*|оператор\w*)|\bбез\s+оператор\w*"
)
_REPLY_WORD = re.compile(r"[а-яa-z]+")
# Длиннее — уже не ответ на вопрос бота, а новый вопрос
_SHORT_REPLY_WORDS = 6

# Как понята реплика на предложение оператора
ACCEPT, DECLINE, THANKS = "accept", "decline", "thanks"


@dataclass(frozen=True)
class OfferReply:
    verdict: str  # ACCEPT | DECLINE | THANKS
    # В согласии есть подробности сверх него: «позовите оператора, не работает ЭЦП»
    details: bool = False


def _plain_reply(text: str) -> str:
    return text.lower().replace("ё", "е")


def _is_offer_reply(words: list[str]) -> bool:
    """Короткая реплика целиком из словаря ответов на предложение."""
    vocab = set(words)
    return 0 < len(vocab) <= _SHORT_REPLY_WORDS and vocab <= _OFFER_WORDS


def _explicit_request(words: list[str]) -> bool:
    """«Передайте», «позовите» — и не после «не»: «не переводите» — это отказ."""
    return any(word in _REQUEST_VERBS and words[i - 1 : i] != ["не"] for i, word in enumerate(words))


def _read_offer_reply(text: str, offer: dict[str, Any]) -> OfferReply | None:
    """Реплика на предложение оператора: согласие, отказ, благодарность или новый вопрос (None).

    Порядок проверок и есть смысл функции:
    1. Короткая явная просьба решает всё: «нет, позовите оператора» — согласие.
    2. Отрицание при просьбе — отказ, даже со словом «оператор»: «оператор не
       нужен», «не надо оператора». Раньше слово «оператор» перевешивало, и
       человека, прямо отказавшегося, ставили в очередь.
    3. Просьба о человеке словами (wants_operator) — согласие; если в ней есть
       что-то сверх согласия, это подробности, и оператор должен их увидеть.
    4. Остальные короткие «да», «хорошо», «спасибо» читаются с оглядкой на то,
       о чём спрашивали (_read_short_reply).
    """
    words = _REPLY_WORD.findall(_plain_reply(text))
    short = _is_offer_reply(words)
    if short and _explicit_request(words):
        return OfferReply(ACCEPT)
    negated = bool(_NEGATED_REQUEST.search(_plain_reply(text)))
    if negated or (short and set(words) & _NEGATIONS and not _wants_operator(text)):
        # Длинная реплика с отказом («оператор не нужен, как подать заявку?») —
        # новый вопрос, и отвечать на него надо по существу
        return OfferReply(DECLINE) if short else None
    if _wants_operator(text):
        return OfferReply(ACCEPT, details=not short)
    return _read_short_reply(set(words), offer) if short else None


def direct_operator_request(text: str) -> bool:
    """Явная просьба о человеке вне ответа на предложение бота.

    Одного короткого «да» здесь недостаточно, а отрицание («оператор не нужен»,
    «не переводите») должно победить само слово «оператор». Используем тот же
    разбор, что и для ответа на предложение, чтобы два входа не расходились.
    """
    reply = _read_offer_reply(text, {})
    return _wants_operator(text) and reply is not None and reply.verdict == ACCEPT


def _read_short_reply(vocab: set[str], offer: dict[str, Any]) -> OfferReply | None:
    """«Да», «хорошо», «спасибо» — в зависимости от того, о чём спрашивал бот.

    На прямой вопрос «передать обращение?» и на отказ базы знаний «хорошо» и
    «ок» — согласие, как и раньше в агенте (asked_for_operator). Но с
    благодарностью те же слова значат «понял, спасибо»: «хорошо, спасибо» после
    отказа — это прощание, и за вежливость в очередь не ставят. Такому
    человеку коротко подтверждаем, что никуда не передаём.

    Предложение при ответе по существу — запасной выход, а не вопрос: там
    переводит только «да» без благодарности, а «да, спасибо» — это «помогло».
    """
    thanks = bool(vocab & _GRATITUDE)
    if offer.get("answered"):
        return OfferReply(ACCEPT) if vocab & _YES and not thanks else None
    if vocab & _YES or (vocab & _SOFT_YES and not thanks):
        return OfferReply(ACCEPT)
    return OfferReply(THANKS) if thanks else None


# --- благодарность ---

# «Спасибо», «спасибо огромное», «благодарю, всё понятно» — прощание, а не вопрос.
# Раньше такая реплика шла в поиск, ничего не находила и получала отказ базы
# знаний с предложением оператора. Реплика должна целиком состоять из слов ниже
# и содержать саму благодарность: «спасибо, а как подать заявку?» — новый вопрос.
_THANKS_ROOT = re.compile(r"^(?:спасиб\w*|спс|благодар\w*|мерси|thanks?|thx)$")
_THANKS_FILLER = frozenset(
    {"большое", "огромное", "огромнейшее", "преогромное", "огромен", "очень", "всем"}
    | {"вам", "вас", "тебе", "тебя"}
    | {"за", "помощь", "ответ", "ответы", "информацию", "консультацию", "подсказку", "разъяснение"}
    | {"все", "понятно", "ясно", "хорошо", "ок", "окей", "ok", "ладно", "отлично", "супер"}
    | {"понял", "поняла", "поняли", "разобрался", "разобралась", "помогли", "помог", "помогло"}
    | {"и", "вы", "ты", "так", "much", "you", "thank", "a", "lot", "до", "свидания", "всего"}
    | {"доброго", "хорошего", "дня", "вечера", "пока"}
)
# Обращение к боту в любом падеже: «спасибо, Валерчик», «благодарю вас, помощник»
_THANKS_ADDRESS = re.compile(r"^(?:валер\w*|бот\w*|помощник\w*|ассистент\w*|друг|дружище|бро)$")
# Длиннее — уже рассказ, а не «спасибо»; такое пусть читает агент
_THANKS_MAX_WORDS = 10


def is_gratitude(text: str) -> bool:
    """Реплика — только благодарность и вежливые слова вокруг неё."""
    words = _REPLY_WORD.findall(_plain_reply(text))
    if not 0 < len(words) <= _THANKS_MAX_WORDS:
        return False
    thanks = [word for word in words if _THANKS_ROOT.match(word)]
    return bool(thanks) and all(
        word in _THANKS_FILLER or _THANKS_ROOT.match(word) or _THANKS_ADDRESS.match(word) for word in words
    )


async def _close_on_thanks(svc: ChatService, turn: Turn) -> dict[str, Any]:
    """Благодарность завершает диалог: прощание, просьба оценить и закрытие.

    Завершение то же, что у оператора и у паузы без реплик: для клиента это
    обычное успешное окончание с карточкой оценки. Прощание — без «answered»
    в meta: это служебная реплика, а не ответ по базе знаний, и в долю
    решённых ИИ она не идёт.
    """
    answer = str(svc.rt.dyn.get("chat.thanks_message"))
    latency_ms = int((time.perf_counter() - turn.started) * 1000)
    await svc._stream_answer(turn.session_id, answer)
    msg = await svc._save_message(
        turn.session_id, "assistant", answer, meta={"kind": "farewell", "latency_ms": latency_ms}
    )
    await svc.rt.ws.publish(f"session:{turn.session_id}", svc._msg_event(msg))

    await svc._update_session(turn.session_id, status="resolved", closed_at=datetime.now(UTC))
    ask = str(svc.rt.dyn.get("feedback.request_message"))
    request = await svc._save_message(
        turn.session_id, "system", ask, meta={"kind": "feedback_request", "reason": "thanks"}
    )
    await svc.rt.ws.publish(f"session:{turn.session_id}", svc._msg_event(request))
    await svc.rt.ws.publish(f"session:{turn.session_id}", {"type": "session_closed", "reason": "resolved"})
    log.info("Диалог %s завершён благодарностью", turn.session_id)
    return {
        "status": "resolved",
        "answer": answer,
        "citations": [],
        "escalated": False,
        "latency_ms": latency_ms,
    }


class _Blanks(dict[str, Any]):
    """Неизвестный заполнитель в тексте из админки остаётся как есть, а не роняет ответ."""

    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


def _fill(template: str, **values: Any) -> str:
    try:
        return template.format_map(_Blanks(values))
    except ValueError, IndexError:  # непарная скобка в тексте из админки
        return template


def _line_label(svc: ChatService, line: int) -> str:
    lines: dict[str, Any] = svc.rt.dyn.get("routing.lines") or {}
    return str((lines.get(str(line)) or {}).get("label") or "поддержка")


async def _maybe_offer_operator(svc: ChatService, turn: Turn) -> dict[str, Any] | None:
    """«Такие обращения решает специалист — передать оператору?»

    Выгрузка поддержки полна обращений, где ответом было действие специалиста:
    «на момент проверки УПД в статусе…», «заказчику направлено приглашение».
    Отдать такой текст новому человеку — соврать ему, а молча отказать — бросить
    с вопросом, который решается одним звонком. Поэтому, когда вопрос заметно
    ближе к таким случаям, чем к любой инструкции, бот честно говорит, что нужен
    человек, и предлагает его — на ту линию, где похожее решали раньше.

    Только предлагает: эскалирует следующая реплика, если человек согласится.
    Перевести без спроса значило бы поставить в очередь того, кто, может быть,
    спрашивал о другом. Пороги консервативные: ошибиться здесь — не ответить
    там, где инструкция была.
    """
    dyn = svc.rt.dyn
    text, docs = turn.text, turn.probe
    # Поиск сломался (None) — сравнивать не с чем; просьбу позвать человека
    # решает агент, как и раньше.
    if not dyn.get("routing.case_offer_enabled") or docs is None or _wants_operator(text):
        return None
    # Товар и явная просьба поискать снаружи — работа каталога, а не специалиста
    if wants_external(text, dyn.get("mcp.explicit_keywords")) or looks_like_product_request(
        text, str(dyn.get("mcp.product_intent") or "")
    ):
        return None
    # Уверенность, а не порядок реранкера: пороги стоят на её шкале, и с ней же
    # сравнивается порог ответа. Лучший ответ — по максимуму, в пользу инструкции.
    answer_score = max((doc.score for doc in docs), default=0.0)
    min_score = float(dyn.get("routing.case_min_score"))
    margin = float(dyn.get("routing.case_margin"))
    if max(min_score, answer_score + margin) > 1.0:
        return None  # инструкцию не обогнать: оценка выше единицы не бывает, второй поиск не нужен
    try:
        cases = await svc.rt.retriever.search_cases(text, audience=turn.role)
    except Exception:
        log.exception("Поиск похожих обращений не удался")
        return None
    if not cases:
        return None
    case = cases[0]
    if case.score < min_score or case.score - answer_score < margin:
        return None

    line = max(1, int(case.line or 1))
    topic = case.category.strip()
    answer = _fill(
        str(dyn.get("routing.case_offer_message")),
        topic=topic,
        line=line,
        line_label=_line_label(svc, line),
    )
    if not topic:  # тема не известна — пустые скобки в тексте хуже, чем без них
        answer = re.sub(r"\s*\(\s*\)", "", answer)
    latency_ms = int((time.perf_counter() - turn.started) * 1000)
    await svc._stream_answer(turn.session_id, answer)
    msg = await svc._save_message(
        turn.session_id,
        "assistant",
        answer,
        meta={
            "kind": "operator_offer",
            "offer_operator": True,
            "line": line,
            "case_article_id": case.article_id,
            "case_score": case.score,
            # Причина будущей эскалации: оператор должен увидеть вопрос, а не «да»
            "question": text,
            "confidence": answer_score,
            # Не автоответ и не пробел базы знаний: инструкции тут и не должно
            # быть, вопрос решает человек проверкой данных.
            "answered": False,
            "escalated": False,
            "latency_ms": latency_ms,
        },
    )
    await svc.rt.ws.publish(f"session:{turn.session_id}", svc._msg_event(msg))
    await svc._track_query(text)
    log.info(
        "Предложен оператор линии %s в сессии %s: обращение %s (%.3f) против ответа %.3f",
        line,
        turn.session_id,
        case.article_id,
        case.score,
        answer_score,
    )
    return {
        "status": "no_answer",
        "answer": answer,
        "citations": [],
        "confidence": answer_score,
        "escalated": False,
        "latency_ms": latency_ms,
    }


async def _maybe_answer_offer(svc: ChatService, turn: Turn) -> dict[str, Any] | None:
    """Реплика в ответ на «передать обращение оператору?»: согласие, отказ или новый вопрос.

    Предлагают оператора и похожее обращение (_maybe_offer_operator), и отказ
    базы знаний — у обоих в meta стоит offer_operator. «Да», «давайте»,
    «передайте» и просьба позвать человека переводят обращение; «нет» и
    «оператор не нужен» получают короткий ответ, а не поиск по базе знаний по
    слову «нет»; «хорошо, спасибо» после отказа — тоже. Всё остальное — новый
    вопрос, и он идёт обычным путём.

    Сначала смотрим на саму реплику и только потом в историю: обычный вопрос
    не должен платить за лишний запрос к базе.
    """
    text = turn.text
    if not (_is_offer_reply(_REPLY_WORD.findall(_plain_reply(text))) or _wants_operator(text)):
        return None
    offer = await _last_assistant_meta(svc, turn.session_id)
    if not offer.get("offer_operator"):
        return None
    reply = _read_offer_reply(text, offer)
    if reply is None:
        return None
    if reply.verdict == ACCEPT:
        return await _accept_offer(svc, turn, offer, details=reply.details)
    return await _decline_offer(svc, turn)


async def _accept_offer(
    svc: ChatService,
    turn: Turn,
    offer: dict[str, Any],
    *,
    details: bool = False,
    accepted_offer: bool = True,
) -> dict[str, Any]:
    """Согласие: обращение уходит на предложенную линию с исходным вопросом в причине.

    Линия — та, что названа в предложении. Спрашивать классификатор заново по
    слову «да» бессмысленно, а по исходному вопросу — значит рисковать тем, что
    человеку пообещали одну линию, а поставили в очередь другой. Классификатор
    нужен, только если линию не называли: так предлагает оператора отказ базы
    знаний.

    Другое дело — согласие с подробностями: «Позовите оператора, у меня не
    работает ЭЦП». Тут человек описал проблему заново, и она может быть совсем
    не той, о которой спрашивал прошлый вопрос. Причина — прошлый вопрос и
    новая реплика вместе (оператор видит обе), линию решает классификатор по
    ним же. Та же пара потом уходит в дообучение классификатора (tasks/jobs.py),
    поэтому текст причины и текст, по которому выбрана линия, совпадают.
    """
    question = str(offer.get("question") or "").strip()
    line = offer.get("line")
    reason = question or turn.text
    if details:
        reason = f"{question}\n{turn.text}" if question and question != turn.text else turn.text
        line = None
    await svc._escalate(turn.session_id, line if isinstance(line, int) else None, reason)
    session = await svc._get_session(turn.session_id)
    line = session.line or 1
    router = (session.routing or {}).get("source", "default")
    external = (session.routing or {}).get("addressee", "line") != "line"
    answer = _fill(
        str(svc.rt.dyn.get("agent.escalation_message")), line=line, line_label=_line_label(svc, line)
    )
    if external:
        answer = (
            "Для этого вопроса нужен внешний адресат. Инструкция обращения указана в чате; "
            "автоматически запрос не отправлен."
        )
    latency_ms = int((time.perf_counter() - turn.started) * 1000)
    await svc._stream_answer(turn.session_id, answer)
    msg = await svc._save_message(
        turn.session_id,
        "assistant",
        answer,
        meta={
            "answered": False,
            "escalated": not external,
            "line": line,
            "accepted_offer": accepted_offer,
            "direct_operator_request": not accepted_offer,
            # Та же запись, что оставляет агент, когда переводит сам: по трассе
            # в выгрузке видно, кто и почему выбрал линию.
            "tool_trace": [
                {"tool": "escalate_to_operator", "reason": reason, "line": line, "router": router}
            ],
            "latency_ms": latency_ms,
        },
    )
    await svc.rt.ws.publish(f"session:{turn.session_id}", svc._msg_event(msg))
    log.info("Согласие на оператора в сессии %s: линия %s (%s)", turn.session_id, line, router)
    return {
        "status": "no_answer",
        "answer": answer,
        "citations": [],
        "escalated": not external,
        "latency_ms": latency_ms,
    }


async def _accept_direct_request(svc: ChatService, turn: Turn) -> dict[str, Any]:
    """Передаёт явную просьбу сразу, не заставляя LLM повторно угадывать намерение."""
    return await _accept_offer(svc, turn, {}, details=True, accepted_offer=False)


async def _decline_offer(svc: ChatService, turn: Turn) -> dict[str, Any]:
    """Отказ от оператора: коротко подтверждаем и ждём следующего вопроса."""
    answer = str(svc.rt.dyn.get("routing.case_decline_message"))
    latency_ms = int((time.perf_counter() - turn.started) * 1000)
    await svc._stream_answer(turn.session_id, answer)
    msg = await svc._save_message(
        turn.session_id,
        "assistant",
        answer,
        meta={"kind": "offer_declined", "answered": False, "escalated": False, "latency_ms": latency_ms},
    )
    await svc.rt.ws.publish(f"session:{turn.session_id}", svc._msg_event(msg))
    return {
        "status": "no_answer",
        "answer": answer,
        "citations": [],
        "escalated": False,
        "latency_ms": latency_ms,
    }


async def _need_external_tools(
    svc: ChatService,
    text: str,
    page_context: str = "",
    probe: list[RetrievedDoc] | None = None,
    audience: str = audience_of.ANY,
) -> bool:
    """Стоит ли поднимать MCP под этот вопрос.

    Подключение внешних серверов стоит секунды на каждое сообщение, а нужно
    оно на единицах вопросов. Поэтому сначала спрашиваем базу знаний обычным
    поиском — он и так занимает десятки миллисекунд: нашёлся ответ выше порога,
    значит внешние источники не нужны.

    Проверка приблизительная: агент сформулирует свой запрос к базе знаний
    сам, и он может отличаться от исходного текста. Но как решение «поднимать
    или нет» этого достаточно — цена ошибки в одну сторону лишние секунды,
    в другую — отказ там, где мог помочь внешний инструмент.

    Одного лишь «база знаний не отвечает» недостаточно. Это допущение верно на
    маленькой базе, а на импортированной поиск возвращает что-нибудь около 0.5
    на любой вопрос: порог не пробивается никогда, и каталог не подключается
    ни разу. Поэтому товарное намерение опознаётся ещё и по самому вопросу.
    """
    mode = str(svc.rt.dyn.get("mcp.mode") or "auto")
    if mode == "off":
        return False
    if mode == "always":
        return True
    if wants_external(text, svc.rt.dyn.get("mcp.explicit_keywords")):
        return True
    if looks_like_product_request(text, str(svc.rt.dyn.get("mcp.product_intent") or "")):
        return True
    if probe is None:
        probe = await svc._probe_kb(text, page_context, audience)
    if probe is None:
        # Поиск сломался: внешние инструменты остаются единственным шансом ответить
        return True
    top = max((d.score for d in probe), default=0.0)
    return top < float(svc.rt.dyn.get("agent.confidence_threshold"))


@dataclass(frozen=True)
class ViolationPolicy:
    """Как поступить с нарушением: за что оно, что делать и чем предупредить.

    action None — взять из настройки guardrails.profanity_action,
    warning_message None — из guardrails.warning_message.
    """

    reason: str = "profanity"
    action: str | None = None
    warning_message: str | None = None


async def _already_warned(svc: ChatService, session_id: str) -> bool:
    """Было ли в диалоге предупреждение за нарушение."""
    async with svc.rt.db.session_factory() as db:
        rows = (
            (
                await db.execute(
                    select(Message.meta).where(
                        Message.session_id == session_id,
                        Message.role == "system",
                    )
                )
            )
            .scalars()
            .all()
        )
    # Только предыдущие предупреждения, а не любое системное сообщение: иначе
    # «Оператор подключился» или запрос отзыва считались бы за первое нарушение
    # и следующее закрывало бы обращение без предупреждения.
    return any((meta or {}).get("kind") == "warning" for meta in rows)


async def _handle_violation(
    svc: ChatService,
    session_id: str,
    verdict: ProfanityVerdict | ToxicityVerdict,
    policy: ViolationPolicy | None = None,
) -> dict[str, Any]:
    policy = policy or ViolationPolicy()
    reason = policy.reason
    action = policy.action or svc.rt.dyn.get("guardrails.profanity_action")
    if action == "warn" and not await _already_warned(svc, session_id):
        content = policy.warning_message or str(svc.rt.dyn.get("guardrails.warning_message"))
        msg = await svc._save_message(
            session_id,
            "system",
            content,
            meta={"kind": "warning", "reason": reason, "matched": verdict.matched},
        )
        await svc.rt.ws.publish(f"session:{session_id}", svc._msg_event(msg))
        return {"status": "warned"}

    content = str(svc.rt.dyn.get("guardrails.violation_message"))
    msg = await svc._save_message(
        session_id,
        "system",
        content,
        meta={"kind": "violation", "reason": reason, "matched": verdict.matched},
    )
    await svc._update_session(session_id, status="closed_violation", closed_at=datetime.now(UTC))
    await svc.rt.ws.publish(f"session:{session_id}", svc._msg_event(msg))
    await svc.rt.ws.publish(f"session:{session_id}", {"type": "session_closed", "reason": "violation"})
    await svc.rt.ws.publish("operators", {"type": "session_violation", "session_id": session_id})
    return {"status": "closed_violation"}


def _clarify_text(prefix: str, options: list[str]) -> str:
    """«... — «А», «Б» или «В»?»

    Варианты попадают и в сам текст, не только в кнопки: в голосовом режиме
    нажимать нечего, и уточнение, состоящее из одного «выберите вариант», было
    бы там тупиком. Прочитанный вслух вопрос работает без интерфейса вовсе.
    """
    quoted = [f"«{option}»" for option in options]
    listed = quoted[0] if len(quoted) == 1 else f"{', '.join(quoted[:-1])} или {quoted[-1]}"
    return f"{prefix} {listed}?"


async def _toxicity_violation(svc: ChatService, session_id: str, tox: ToxicityVerdict) -> dict[str, Any]:
    """Грубость без мата: по умолчанию мягче — сначала предупреждение, обращение живёт."""
    dyn = svc.rt.dyn
    log.info("Токсичность в сессии %s: score=%.2f %s %s", session_id, tox.score, tox.categories, tox.matched)
    policy = ViolationPolicy(
        reason="toxicity",
        action=str(dyn.get("guardrails.toxicity_action")),
        warning_message=str(dyn.get("guardrails.toxicity_message")),
    )
    return await svc._handle_violation(session_id, tox, policy)


async def _ask_model(svc: ChatService, text: str) -> dict[str, float] | None:
    """Оценки нейросети — один запрос на реплику; None — выключена или недоступна."""
    model = svc.rt.toxicity
    if model is None or not svc.rt.dyn.get("guardrails.model_enabled"):
        return None
    return await model.scores(text)


async def check_guardrails(
    svc: ChatService, session_id: str, text: str
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    """Правила общения: мат, грубость, попытка подменить инструкции.

    Возвращает (готовый ответ или None, флаги для meta). Проверки идут от
    дешёвого к дорогому, первое сработавшее решает. Мат завершает обращение и
    сообщает пользователю о нарушении правил общения; грубость без мата — сначала
    предупреждение. Подозрение на инъекцию только помечает сообщение: это
    гипотеза, а не приговор, и обрывать из-за неё живого человека нельзя.
    """
    dyn = svc.rt.dyn
    profanity_on = bool(dyn.get("guardrails.profanity_enabled"))
    toxicity_on = bool(dyn.get("guardrails.toxicity_enabled"))

    # 1. Мат: корни с обходами обфускации и словарь производных («вымандохивали»)
    if profanity_on:
        verdict = guardrails.detect_profanity(
            text,
            dyn.get("guardrails.profanity_extra_roots"),
            dyn.get("guardrails.profanity_exceptions"),
            check_layout=bool(dyn.get("guardrails.layout_profanity_check")),
            lexicon=bool(dyn.get("guardrails.lexicon_enabled")),
        )
        if verdict.hit:
            return await svc._handle_violation(session_id, verdict), {}

    # 2. Оскорбления и грубость без матерных корней («побрей своё очко») — правила
    if toxicity_on:
        tox = guardrails.check_toxicity(
            text,
            threshold=float(dyn.get("guardrails.toxicity_threshold")),
            extra_terms=dyn.get("guardrails.toxicity_extra_terms"),
            extra_exceptions=dyn.get("guardrails.toxicity_exceptions"),
        )
        if tox.hit:
            return await _toxicity_violation(svc, session_id, tox), {}

    # 3. Нейросеть — то, что правила пропустили. Её «мат» — тот же мат, что по
    #    корням: обращение завершается. Оскорбление и угроза — грубость.
    if profanity_on or toxicity_on:
        scores = await _ask_model(svc, text)
        threshold = float(dyn.get("guardrails.model_threshold"))
        obscene = guardrails.model_verdict(scores, threshold, guardrails.MODEL_OBSCENITY)
        if profanity_on and obscene.hit:
            return await svc._handle_violation(session_id, obscene), {}
        abuse = guardrails.model_verdict(scores, threshold, guardrails.MODEL_ABUSE)
        if toxicity_on and abuse.hit:
            return await _toxicity_violation(svc, session_id, abuse), {}

    flags: dict[str, Any] = {}
    if dyn.get("guardrails.injection_enabled") and guardrails.detect_injection(text):
        flags["injection_suspect"] = True
        log.warning("Подозрение на prompt-инъекцию в сессии %s", session_id)
    return None, flags
