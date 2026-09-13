"""Оркестрация диалога: кто и в каком порядке отвечает пользователю.

Здесь остались жизненный цикл диалога и сам конвейер `handle_user_message`:
раскладка → правила общения → оператор → ответ на предложение оператора → роль
→ поиск → похожие обращения → инструменты → уточнение → дословный ответ → агент.
Каждый шаг решает одну развилку и либо возвращает готовый ответ, либо
пропускает ход дальше.

Зоны ответственности живут в `app/chat/`: шаги конвейера — `steps.py`, работа с
базой знаний — `knowledge.py`, подсказки оператору — `copilot_flow.py`, оценки и
отзывы — `feedback_flow.py`, всё про передачу диалога человеку —
`operator_flow.py`. Файл на тысячу строк означал, что вдвоём в него не влезть, а
новая логика диалога добавлялась ещё одной веткой посреди общего метода.

Класс сохраняет все прежние имена методов тонкими делегатами: их зовут роутеры,
диспетчер и два десятка тестов. Все стадии асинхронные; ответ агента стримится в
WebSocket кусками (эффект набора текста).
"""

import asyncio
import logging
import time
from typing import TYPE_CHECKING, Any

from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.chat import (
    answer_recovery,
    copilot_flow,
    document_flow,
    feedback_flow,
    followup,
    knowledge,
    operator_flow,
    source_conflicts,
    steps,
)
from app.chat.errors import ChatError

# Ключ и порог живут рядом с кодом, который их считает; здесь — реэкспорт: их
# импортируют сидер и подсказки, и переносить чужие импорты незачем.
from app.chat.knowledge import popular_queries
from app.chat.steps import ViolationPolicy
from app.chat.turn import Turn
from app.models import ChatSession, Message
from app.rag.agent import run_support_agent
from app.security import is_session_owner
from app.services import audience as audience_of
from app.services import copilot, pagectx
from app.services.agent import AgentOutcome, AgentRequest
from app.services.mcp import mcp_toolsets
from app.uploads import StoredFile

if TYPE_CHECKING:
    from app.runtime import AppRuntime
    from app.services.retrieval import RetrievedDoc

log = logging.getLogger(__name__)

# Пауза между кусочками ответа в анимации набора.
CHUNK_PAUSE_S = 0.04
# Слов в одном куске потокового ответа
STREAM_CHUNK_WORDS = 4
# Длиннее реплика в чат не принимается: это уже не вопрос, а документ, и для
# него есть загрузка в базу знаний.
MAX_MESSAGE_CHARS = 4000
# Превью последней реплики в списке «Мои обращения» — короче, чем в очереди
# оператора (там 140): здесь это подпись под карточкой, а не рабочий список.
HISTORY_PREVIEW_CHARS = 100
# Заголовок карточки — первый вопрос человека: по нему обращение и узнают.
HISTORY_TITLE_CHARS = 120


class ChatService:
    def __init__(self, rt: AppRuntime) -> None:
        self.rt = rt

    # --- вспомогательные ---

    async def _save_message(  # noqa: PLR0913
        self,
        session_id: str,
        role: str,
        content: str,
        citations: list[dict[str, Any]] | None = None,
        meta: dict[str, Any] | None = None,
        request_id: str | None = None,
    ) -> Message:
        async with self.rt.db.session_factory() as db:
            from app.services.support_flow import changed, emit, lock_session

            session = await lock_session(db, session_id)
            import hashlib
            import json

            from app.models import SupportCommand
            from app.services.support_flow import AlreadyAccepted, visitor_key

            key = visitor_key(session_id, request_id) if request_id else None
            fingerprint = hashlib.sha256(json.dumps([content, meta], sort_keys=True).encode()).hexdigest()
            if key:
                receipt = await db.get(SupportCommand, key)
                if receipt:
                    if receipt.fingerprint != fingerprint:
                        raise ChatError(409, "Идентификатор использован для другого сообщения")
                    raise AlreadyAccepted(receipt.result)
            if role == "user" and session.status == "waiting_user":
                session.status, session.waiting_reason = "operator_active", None
            msg = Message(
                session_id=session_id, role=role, content=content, citations=citations or [], meta=meta or {}
            )
            db.add(msg)
            await db.flush()
            if key:
                db.add(
                    SupportCommand(
                        id=key,
                        session_id=session_id,
                        fingerprint=fingerprint,
                        result={"status": "accepted", "message_id": msg.id},
                    )
                )
            changed(db, session, role, "message_saved")
            emit(db, session_id, f"session:{session_id}", self._msg_event(msg))
            await db.commit()
            await db.refresh(msg)
            return msg

    @staticmethod
    def _msg_event(msg: Message) -> dict[str, Any]:
        return {
            "type": "message",
            "message": {
                "id": msg.id,
                "role": msg.role,
                "content": msg.content,
                # Список и словарь всегда: у сообщения без цитат и меток в базе
                # NULL, а сокет и REST обязаны отдавать одну форму.
                "citations": msg.citations or [],
                "meta": msg.meta or {},
                "created_at": msg.created_at.isoformat(),
            },
        }

    async def _get_session(self, session_id: str) -> ChatSession:
        async with self.rt.db.session_factory() as db:
            session = await db.get(ChatSession, session_id)
        if session is None:
            raise ChatError(404, "Диалог не найден")
        return session

    async def _update_session(self, session_id: str, **values: Any) -> None:
        async with self.rt.db.session_factory() as db:
            session = await db.get(ChatSession, session_id)
            for k, v in values.items():
                setattr(session, k, v)
            await db.commit()

    # --- жизненный цикл диалога ---

    async def create_session(
        self, user_id: str | None = None, channel: str = "web", audience: str | None = None
    ) -> dict[str, Any]:
        role = audience_of.normalize(audience)
        async with self.rt.db.session_factory() as db:
            session = ChatSession(
                user_id=user_id, channel=channel, audience=role if audience_of.is_known(role) else None
            )
            db.add(session)
            await db.commit()
            await db.refresh(session)
        greeting = str(self.rt.dyn.get("chat.greeting"))
        await self._save_message(session.id, "assistant", greeting, meta={"kind": "greeting"})
        chips = await self._chips(role)
        return {"session_id": session.id, "greeting": greeting, "chips": chips, "audience": role}

    async def _chips(self, audience: str = audience_of.ANY) -> list[str]:
        """Подсказки приветственного экрана: заданные админом + реально частые.

        Роль меняет список: поставщику предлагают разместить оферту, заказчику —
        создать котировочную сессию. Пока роль не выбрана, идут общие вопросы.

        Порог по числу упоминаний обязателен: без него в чипсы попадала любая
        одиночная реплика, то есть чужое сообщение показывалось постороннему, а
        один настойчивый аноним мог навязать свой текст всем посетителям.
        """
        role = audience_of.normalize(audience)
        key = f"chat.chips_{role}" if audience_of.is_known(role) else "chat.chips"
        chips = list(self.rt.dyn.get(key) or self.rt.dyn.get("chat.chips") or [])
        for text in await popular_queries(self.rt.cache, 10):
            if text not in chips:
                chips.append(text)
        # Четыре карточки помещаются на экран целиком; частые вопросы
        # дополняют список, только если админ задал подсказок меньше.
        return chips[:4]

    async def set_audience(self, session_id: str, audience: str) -> dict[str, Any]:
        """Переключатель «поставщик / заказчик» посреди диалога.

        Роль запоминается у диалога и действует на все следующие сообщения;
        вместе с ней меняются и подсказки на экране.
        """
        session = await self._get_session(session_id)
        role = audience_of.normalize(audience)
        stored = role if audience_of.is_known(role) else None
        if session.audience != stored:
            await self._update_session(session_id, audience=stored)
        return {"audience": role, "chips": await self._chips(role)}

    async def _resolve_audience(self, session: ChatSession, text: str, requested: object) -> str:
        """Роль для этого сообщения: слова человека > переключатель > память диалога.

        Слова важнее переключателя: «я заказчик» написано только что, а
        переключатель мог остаться от прошлого раза. Смена роли доезжает до
        интерфейса отдельным событием, чтобы переключатель показывал то же, что
        знает сервер.
        """
        detected = audience_of.detect(text)
        chosen = detected or audience_of.normalize(requested)
        if not audience_of.is_known(chosen):
            chosen = audience_of.normalize(session.audience)
        if audience_of.is_known(chosen) and chosen != audience_of.normalize(session.audience):
            await self._update_session(session.id, audience=chosen)
            await self.rt.ws.publish(f"session:{session.id}", {"type": "audience", "audience": chosen})
        return chosen

    async def resume_session(self, session_id: str) -> dict[str, Any]:
        """Состояние существующего диалога — чтобы вернуть человека туда, где он был.

        Перезагрузка страницы или закрытая вкладка не должны терять разговор:
        особенно когда человек ждёт оператора, а его обращение уже стоит в
        очереди. Возвращаем ровно то, чем отличается восстановленный диалог от
        нового: статус, линию эскалации и подсказки для приветственного экрана.
        """
        session = await self._get_session(session_id)  # нет сессии -> ChatError 404
        role = audience_of.normalize(session.audience)
        labels = {"line_label": "", "addressee_label": ""}
        if session.line is not None and (session.escalated_at is not None or session.routing):
            # После перезагрузки шапка называет того же адресата, что и до неё,
            # а не голый номер линии
            labels = await operator_flow.route_labels(self, session.id, session.line)
        return {
            "session_id": session.id,
            "chips": await self._chips(role),
            "status": session.status,
            "line": session.line,
            "escalated": session.escalated_at is not None,
            "audience": role,
            "version": session.workflow_version or 0,
            "waiting_reason": session.waiting_reason,
            "operator_id": session.operator_id,
            **labels,
        }

    async def get_messages(self, session_id: str) -> list[dict[str, Any]]:
        async with self.rt.db.session_factory() as db:
            rows = (
                (
                    await db.execute(
                        select(Message).where(Message.session_id == session_id).order_by(Message.created_at)
                    )
                )
                .scalars()
                .all()
            )
        return [self._msg_event(m)["message"] for m in rows]

    async def staff_history(self, session_id: str) -> list[dict[str, Any]]:
        """История для сотрудника: без токена владельца, но только у существующего диалога."""
        await self._get_session(session_id)
        return await self.get_messages(session_id)

    @staticmethod
    async def _edge_messages(
        db: AsyncSession, session_ids: list[str], *, newest: bool, role: str | None = None
    ) -> dict[str, Message]:
        """Крайнее сообщение каждой сессии одним запросом: последнее или первое (с ролью).

        Тот же приём, что в очереди оператора (app/services/operator_desk.py):
        оконная функция вместо SELECT на каждую сессию — иначе список «Моих
        обращений» на десяток диалогов делал бы десяток round-trip'ов.
        """
        if not session_ids:
            return {}
        order = desc(Message.created_at) if newest else Message.created_at
        query = select(
            Message,
            func.row_number().over(partition_by=Message.session_id, order_by=order).label("rn"),
        ).where(Message.session_id.in_(session_ids))
        if role is not None:
            query = query.where(Message.role == role)
        ranked = query.subquery()
        edge = aliased(Message, ranked)
        rows = (await db.execute(select(edge).where(ranked.c.rn == 1))).scalars().all()
        return {m.session_id: m for m in rows}

    @staticmethod
    def _history_preview(message: Message | None) -> str:
        if message is None or not message.content.strip():
            return ""
        return message.content[:HISTORY_PREVIEW_CHARS]

    async def list_session_summaries(self, pairs: list[tuple[str, str]]) -> list[dict[str, Any]]:
        """Карточки для раздела «Мои обращения»: только валидные пары (id, owner).

        Учётных записей у чата нет — владение диалогом подтверждает подписанный
        токен рядом с id (см. security.session_owner_token), а не логин. Пара с
        неверным или чужим токеном просто выпадает из ответа: одна протухшая
        или подделанная запись в localStorage не должна валить весь список
        настоящих обращений посетителя.

        Обращение — диалог, где человек хоть что-то спросил: диалог с одним
        приветствием заводится при каждом заходе на страницу. Свежая переписка
        сверху; «Кому» — теми же словами, что в шапке восстановленного диалога.
        """
        valid_ids = [session_id for session_id, owner in pairs if is_session_owner(session_id, owner)]
        if not valid_ids:
            return []
        async with self.rt.db.session_factory() as db:
            first_questions = await self._edge_messages(db, valid_ids, newest=False, role="user")
            asked = list(first_questions)
            sessions = (
                (await db.execute(select(ChatSession).where(ChatSession.id.in_(asked)))).scalars().all()
                if asked
                else []
            )
            last_by_session = await self._edge_messages(db, asked, newest=True)
            unread_rows = (
                (
                    await db.execute(
                        select(Message.session_id, func.count(Message.id))
                        .join(ChatSession, Message.session_id == ChatSession.id)
                        .where(
                            Message.session_id.in_(asked),
                            Message.role.in_(("system", "operator")),
                            (ChatSession.read_at.is_(None)) | (Message.created_at > ChatSession.read_at),
                        )
                        .group_by(Message.session_id)
                    )
                ).all()
                if asked
                else []
            )
            unread_counts = dict(unread_rows)
        labels = await operator_flow.routes_labels(
            self, {s.id: s.line for s in sessions if s.line is not None and s.escalated_at is not None}
        )
        cards = []
        for s in sorted(sessions, key=lambda s: last_by_session[s.id].created_at, reverse=True):
            last = last_by_session[s.id]
            cards.append(
                {
                    "session_id": s.id,
                    "unread_count": unread_counts.get(s.id, 0),
                    "status": s.status,
                    "line": s.line,
                    "title": " ".join(first_questions[s.id].content.split())[:HISTORY_TITLE_CHARS],
                    "created_at": s.created_at.isoformat(),
                    "updated_at": last.created_at.isoformat(),
                    "closed_at": s.closed_at.isoformat() if s.closed_at else None,
                    "last_message_preview": self._history_preview(last),
                    "last_message_role": last.role,
                    **labels.get(s.id, {"line_label": "", "addressee_label": ""}),
                }
            )
        return cards

    # --- главный сценарий ---

    async def handle_user_message(
        self,
        session_id: str,
        text: str,
        page: object = None,
        audience: object = None,
        request_id: str | None = None,
    ) -> dict[str, Any]:
        """Реплика пользователя → ответ.

        Три фазы: принять и сохранить реплику, пройти ранние развилки (правила
        общения, оператор, уточнение, дословная цитата) и, если никто не ответил
        раньше, спросить агента. Всё, что известно о реплике, едет в Turn.
        """
        from app.models import SupportCommand
        from app.services.support_flow import AlreadyAccepted, visitor_key

        max_request_id = 128
        if request_id is not None and (
            not isinstance(request_id, str) or not 1 <= len(request_id) <= max_request_id
        ):
            raise ChatError(422, "Некорректный идентификатор сообщения")
        try:
            turn = await self._accept_turn(session_id, text, page, audience, request_id)
        except AlreadyAccepted as duplicate:
            return duplicate.result
        early = await self._screen_turn(turn)
        if early is None:
            early = await self._quick_answer(turn)
        result = early if early is not None else await self._agent_answer(turn)
        if request_id:
            async with self.rt.db.session_factory() as db:
                receipt = await db.get(SupportCommand, visitor_key(session_id, request_id))
                if receipt:
                    receipt.result = result
                    await db.commit()
        return result

    async def _accept_turn(
        self, session_id: str, text: str, page: object, audience: object, request_id: str | None = None
    ) -> Turn:
        """Проверить и сохранить реплику; вернуть её со всем, что известно о диалоге."""
        started = time.perf_counter()
        session = await self._get_session(session_id)
        if session.status in ("resolved", "closed_violation"):
            raise ChatError(409, "Диалог завершён — создайте новое обращение")
        text = text.strip()
        if not text:
            raise ChatError(422, "Пустое сообщение")
        if len(text) > MAX_MESSAGE_CHARS:
            raise ChatError(422, f"Сообщение слишком длинное (лимит {MAX_MESSAGE_CHARS} символов)")

        # Раздел портала едет с каждым сообщением, а не с диалогом: человек ходит
        # по сайту с открытым виджетом, и вопрос про оферту он задаёт со страницы
        # оферт, даже если разговор начал в личном кабинете.
        current_page = pagectx.clean(page)
        if current_page and not session.page_url:
            await self._update_session(session_id, page_url=current_page.get("path") or None)

        # Роль собеседника решается до сохранения: она уходит в meta сообщения, и
        # по ней потом видно, поставщики или заказчики задают этот вопрос чаще.
        role = await self._resolve_audience(session, text, audience)
        user_meta: dict[str, Any] = {}
        if current_page:
            user_meta["page"] = current_page
        if audience_of.is_known(role):
            user_meta["audience"] = role

        user_msg = await self._save_message(
            session_id, "user", text, meta=user_meta or None, request_id=request_id
        )
        await self.rt.ws.publish(f"session:{session_id}", self._msg_event(user_msg))

        # 0. Забытая раскладка — чиним до всего остального, дальше по диалогу идёт уже
        #    исправленный текст: и guardrails, и поиск по базе знаний работают с ним
        text = await self._maybe_fix_layout(session_id, user_msg, text)
        return Turn(
            session_id=session_id,
            text=text,
            started=started,
            status=(await self._get_session(session_id)).status,
            role=role,
            page_text=pagectx.as_text(current_page),
            message_id=user_msg.id,
            said=text,
        )

    async def _screen_turn(self, turn: Turn) -> dict[str, Any] | None:
        """Развилки до поиска: правила общения, диалог у оператора, ответ про роль."""
        # 1. Правила общения: мат и грубость завершают разговор, подозрение на
        #    подмену инструкций только помечает сообщение.
        violation, turn.flags = await steps.check_guardrails(self, turn.session_id, turn.text)
        if violation is not None:
            return violation

        # 2. Диалог уже у оператора — агент молчит, оператору летит копайлот
        if turn.status in ("waiting_operator", "operator_assigned", "operator_active", "waiting_user"):
            from app.services.background import spawn

            spawn(self._push_copilot(turn.session_id, turn.text, reply_to=turn.message_id))
            return {"status": "forwarded_to_operator"}

        # 2a. «Спасибо», «благодарю, всё понятно» — прощание: диалог завершается,
        #     а не уходит в поиск за отказом базы знаний. Раньше предложения
        #     оператора: «хорошо, спасибо» на него — тоже прощание, а не согласие.
        if steps.is_gratitude(turn.text):
            return await steps._close_on_thanks(self, turn)

        # 2b. Ответ на «передать обращение оператору?»: «да» — это согласие, а не
        #     вопрос к базе знаний. Линия — та, что была предложена, а не новая
        #     догадка классификатора по слову «да».
        offer_reply = await steps._maybe_answer_offer(self, turn)
        if offer_reply is not None:
            return offer_reply

        # Явная просьба о человеке — команда интерфейса, а не вопрос к RAG.
        # Раньше её всё равно отдавали модели: при пустом поиске та могла вернуть
        # ещё одно предложение «Позвать оператора», и человек нажимал дважды.
        # Само направление по-прежнему классифицируется по полному тексту просьбы.
        if steps.direct_operator_request(turn.text):
            return await steps._accept_direct_request(self, turn)

        # 2c. Ответ на «вы поставщик или заказчик?» — это не новый вопрос, а
        #     недостающая половина прежнего: искать надо его, а не реплику «я заказчик».
        pending = await self._pending_audience_question(turn.session_id)
        if pending and audience_of.is_choice(turn.text):
            log.info(
                "Роль уточнена в сессии %s: %s, возвращаемся к вопросу %r",
                turn.session_id,
                turn.role,
                pending[:60],
            )
            turn.text = pending
            turn.said = pending
        return None

    async def _quick_answer(self, turn: Turn) -> dict[str, Any] | None:
        """Stop a known confirmed contradiction before spending the one LLM call.

        Normal answers still go through the RAG agent. The preliminary retrieval
        only gives the conflict registry enough article IDs to recognize an exact
        disputed question; it never produces an answer by itself.
        """
        await self.rt.ws.publish(f"session:{turn.session_id}", {"type": "typing", "role": "assistant"})
        turn.probe = await self._probe_kb(turn.text, turn.page_text, turn.role)
        # Короткое продолжение прошлого вопроса («а где это сделать?»), которое само уверенного
        # ответа не находит, ищется вместе с ним: агент получает склейку в follow_up_of.
        await followup.maybe_continue(self, turn)
        audience_clarification = await steps._maybe_clarify_audience(self, turn)
        if audience_clarification is not None:
            return audience_clarification
        conflict = await source_conflicts.maybe_ambiguous_conflict(self, turn)
        if conflict is not None:
            return conflict
        turn.need_mcp = True
        return None

    async def _agent_answer(self, turn: Turn) -> dict[str, Any]:
        """4. Ответ ReAct-агента: база знаний, внешние инструменты, эскалация или отказ."""
        history = [
            (m["role"], m["content"])
            for m in await self.get_messages(turn.session_id)
            if m["role"] in ("user", "assistant")
        ][:-1]
        if turn.flags.get("recovery_of"):
            # Неудачные ответы нельзя передавать как авторитетную историю в system prompt.
            history = [(role, text) for role, text in history if role == "user"]
        # Продолжение едет агенту своими словами, а прошлый вопрос — отдельно: склейку
        # собирает сам агент, а просьбу об операторе он проверяет по словам человека.
        follow_up = turn.follow_up
        # Инструменты подключённых MCP-серверов живут ровно на время одного ответа:
        # реестр правится из админки на лету, кэшировать соединения между запросами —
        # значит отдавать пользователю то, что администратор уже отключил.
        async with mcp_toolsets(self.rt.db.session_factory, active=turn.need_mcp) as bundle:
            outcome = await run_support_agent(
                self.rt.dyn,
                self.rt.retriever,
                self.rt.classifier,
                AgentRequest(
                    user_text=answer_recovery.agent_text(turn.said if follow_up else turn.text, turn.flags),
                    follow_up_of=follow_up.question if follow_up else "",
                    response_style=str(turn.flags.get("response_style", "")),
                    history=history,
                    page_context=turn.page_text,
                    audience=turn.role,
                    timeout_s=float(self.rt.dyn.get("agent.timeout_s")),
                    mcp_toolsets=bundle.toolsets,
                ),
            )
        if bundle.failed:
            turn.flags["mcp_failed"] = [name for name, _ in bundle.failed]

        # Иллюстрации процитированных статей едут вместе с ответом, а не прячутся
        # в карточке источника: текст инструкций ссылается на них прямо («нажмите
        # (Рисунок 26 (2))»), и заставлять ради этого открывать статью — значит
        # обесценить всю затею.
        if not outcome.escalated:
            conflict = await source_conflicts.maybe_conflict_reply(
                self, turn, outcome.answer, outcome.citations
            )
            if conflict is not None:
                return conflict
        await self._attach_citation_images(outcome.citations, outcome.answer)

        # Время ответа — сколько думал сервис; анимация набора в него не входит.
        latency_ms = int((time.perf_counter() - turn.started) * 1000)
        await self._stream_answer(turn.session_id, outcome.answer)
        bot_msg = await self._save_message(
            turn.session_id,
            "assistant",
            outcome.answer,
            citations=outcome.citations,
            meta=self._answer_meta(turn, outcome, latency_ms),
        )
        await self.rt.ws.publish(f"session:{turn.session_id}", self._msg_event(bot_msg))

        await self._track_query(turn.text)

        if outcome.escalated:
            await self._escalate(turn.session_id, outcome.line or 1, turn.text)
        elif not outcome.answered:
            await self._record_gap(turn.text, outcome.confidence)

        return {
            "status": "answered" if outcome.answered else "no_answer",
            "answer": outcome.answer,
            "citations": outcome.citations,
            "confidence": outcome.confidence,
            "escalated": outcome.escalated,
            "latency_ms": latency_ms,
        }

    def _answer_meta(self, turn: Turn, outcome: AgentOutcome, latency_ms: int) -> dict[str, Any]:
        """meta ответа агента: уверенность, трасса, флаги, товары и сценарий по порталу."""
        meta = {
            "question": turn.text,
            "reply_to": turn.message_id,
            "audience": turn.role,
            "page_context": turn.page_text,
            "model": self.rt.dyn.get("llm.model"),
            "confidence": outcome.confidence,
            "answered": outcome.answered,
            "escalated": outcome.escalated,
            "tool_trace": outcome.tool_trace,
            "latency_ms": latency_ms,
            **turn.flags,
        }
        # «Нет ответа» везде одинаково: уведомить и ПРЕДЛОЖИТЬ оператора. Флаг
        # читается следующей репликой — «да» на него переводит обращение
        # человеку (steps._maybe_answer_offer). У отказа предложение есть всегда;
        # ответ по базе агент может снабдить им сам, когда опора неполная.
        # Через getattr: поля в AgentOutcome появляются в соседней ветке, и
        # оркестратор не должен ломаться ни до, ни после неё.
        declined = not outcome.answered and not outcome.escalated
        offer = declined or bool(getattr(outcome, "offer_operator", declined))
        meta["offer_operator"] = offer and not outcome.escalated
        if meta["offer_operator"]:
            # Причина будущей эскалации — сам вопрос, а не «да» в ответ на предложение
            meta["question"] = turn.text
        grounding = getattr(outcome, "grounding", None)
        if grounding is not None:
            meta["grounding"] = grounding
        # Карточки товаров из внешнего инструмента — в meta, а не в цитаты: цитата
        # ведёт в статью базы знаний, и подкладывать под неё товар портала значит
        # сломать переход к источнику. В meta они переживают перезагрузку страницы.
        if outcome.products:
            meta["products"] = outcome.products
        # Пошаговый сценарий по порталу — тоже в meta: расширение проведёт по
        # кнопкам, виджет покажет ссылку на раздел. Прикладывается и к отказу:
        # «как войти» база знаний может не покрывать, а дорогу показать можно.
        guide = self._guide_for(turn.text, turn.role)
        if guide:
            meta["guide"] = guide
        return meta

    async def _stream_answer(self, session_id: str, answer: str) -> None:
        """Псевдостриминг: кусочки по несколько слов для эффекта живого набора.

        Анимация — для того, кто смотрит диалог через сокет этого инстанса. Никто
        не смотрит (REST-клиент: расширение, MCP, curl) — кусочков нет: паузы
        между ними задерживали только HTTP-ответ. Готовый ответ всё равно уходит
        событием message.
        """
        room = f"session:{session_id}"
        if not self.rt.ws.has_room(room):
            return
        words = answer.split(" ")
        chunk: list[str] = []
        for i, w in enumerate(words):
            chunk.append(w)
            if len(chunk) >= STREAM_CHUNK_WORDS or i == len(words) - 1:
                await self.rt.ws.publish(
                    room,
                    {"type": "assistant_chunk", "text": " ".join(chunk) + " "},
                )
                chunk = []
                await asyncio.sleep(CHUNK_PAUSE_S)

    # --- делегаты ---
    # Тела методов живут в app/chat/: оркестрация здесь, зоны ответственности там.
    # Имена сохранены, потому что их зовут роутеры, диспетчер и два десятка тестов;
    # переписывать вызовы ради красоты значило бы менять чужой код без нужды.
    #
    # Сигнатуры повторяют оригиналы дословно, а не прячутся за `*args, **kwargs`:
    # с ними ни IDE, ни тайпчекер не видели ни одного параметра этих двадцати
    # методов, и опечатка в имени аргумента доживала до рантайма.

    # knowledge
    async def _attach_citation_images(self, citations: list[dict[str, Any]], answer: str = "") -> None:
        return await knowledge._attach_citation_images(self, citations, answer)

    async def _provenance_of(self, article_ids: list[str]) -> tuple[dict[str, Any], dict[str, Any]]:
        return await knowledge._provenance_of(self, article_ids)

    async def _probe_kb(
        self, text: str, page_context: str = "", audience: str = audience_of.ANY
    ) -> list[RetrievedDoc] | None:
        return await knowledge._probe_kb(self, text, page_context, audience)

    def _guide_for(self, text: str, audience: str) -> dict[str, Any] | None:
        return knowledge._guide_for(self, text, audience)

    async def _track_query(self, text: str) -> None:
        return await knowledge._track_query(self, text)

    async def _record_gap(self, query: str, best_score: float) -> None:
        return await knowledge._record_gap(self, query, best_score)

    # steps
    async def _maybe_fix_layout(self, session_id: str, msg: Message, text: str) -> str:
        return await steps._maybe_fix_layout(self, session_id, msg, text)

    async def _maybe_verbatim(
        self, session_id: str, text: str, docs: list[RetrievedDoc] | None, started: float
    ) -> dict[str, Any] | None:
        return await steps._maybe_verbatim(self, Turn(session_id, text, started, probe=docs))

    async def _pending_audience_question(self, session_id: str) -> str | None:
        return await steps._pending_audience_question(self, session_id)

    # Статический, как и до расшивки: сравнение двух вопросов ничего не знает о
    # диалоге, и вызов от класса — `ChatService._same_question(a, b)` — работал.
    # Обычный делегат с `self` молча превратил бы такой вызов в TypeError.
    @staticmethod
    def _same_question(first: str, second: str) -> float:
        return steps._same_question(first, second)

    async def _maybe_clarify_audience(
        self, session_id: str, text: str, docs: list[RetrievedDoc] | None, audience: str, started: float
    ) -> dict[str, Any] | None:
        return await steps._maybe_clarify_audience(
            self, Turn(session_id, text, started, role=audience, probe=docs)
        )

    def _clarify_options(self, docs: list[RetrievedDoc], limit: int) -> list[dict[str, Any]]:
        return steps._clarify_options(self, docs, limit)

    async def _already_clarified(self, session_id: str) -> bool:
        return await steps._already_clarified(self, session_id)

    async def _maybe_clarify(
        self, session_id: str, text: str, docs: list[RetrievedDoc] | None, started: float
    ) -> dict[str, Any] | None:
        return await steps._maybe_clarify(self, Turn(session_id, text, started, probe=docs))

    async def _need_external_tools(
        self,
        text: str,
        page_context: str = "",
        probe: list[RetrievedDoc] | None = None,
        audience: str = audience_of.ANY,
    ) -> bool:
        return await steps._need_external_tools(self, text, page_context, probe, audience)

    async def _handle_violation(
        self, session_id: str, verdict: Any, policy: ViolationPolicy | None = None
    ) -> dict[str, Any]:
        return await steps._handle_violation(self, session_id, verdict, policy)

    # copilot_flow
    async def _push_copilot(self, session_id: str, text: str, reply_to: str = "") -> None:
        return await copilot_flow._push_copilot(self, session_id, text, reply_to)

    async def _push_draft(self, session_id: str, question: str, reply_to: str) -> None:
        return await copilot_flow._push_draft(self, session_id, question, reply_to)

    async def _make_draft(
        self, session_id: str, question: str, *, reply_to: str = ""
    ) -> copilot.CopilotDraft:
        return await copilot_flow._make_draft(self, session_id, question, reply_to=reply_to)

    async def operator_draft(self, session_id: str) -> dict[str, Any]:
        return await copilot_flow.operator_draft(self, session_id)

    async def operator_suggestions(self, session_id: str) -> list[dict[str, Any]]:
        return await copilot_flow.operator_suggestions(self, session_id)

    async def operator_case_suggestions(self, session_id: str) -> list[dict[str, Any]]:
        return await copilot_flow.operator_case_suggestions(self, session_id)

    # feedback_flow
    async def rate_message(self, session_id: str, message_id: str, value: int) -> dict[str, Any]:
        return await feedback_flow.rate_message(self, session_id, message_id, value)

    async def _question_before(self, session_id: str, message_id: str) -> str | None:
        return await feedback_flow._question_before(self, session_id, message_id)

    async def add_feedback(self, session_id: str, rating: int, comment: str = "") -> dict[str, Any]:
        return await feedback_flow.add_feedback(self, session_id, rating, comment)

    # operator_flow
    async def _escalate(self, session_id: str, line: int | None, reason: str) -> None:
        return await operator_flow._escalate(self, session_id, line, reason)

    async def operator_take(self, session_id: str, operator_id: str, operator_name: str) -> None:
        return await operator_flow.operator_take(self, session_id, operator_id, operator_name)

    async def notify_assigned(self, item: dict[str, Any]) -> None:
        return await operator_flow.notify_assigned(self, item)

    async def ensure_session_open_for_upload(self, session_id: str) -> None:
        return await operator_flow.ensure_session_open_for_upload(self, session_id)

    async def user_attachment(self, session_id: str, file: StoredFile) -> dict[str, Any]:
        return await operator_flow.user_attachment(self, session_id, file)

    async def _screen_operator_text(self, session_id: str, operator_id: str, text: str) -> None:
        return await operator_flow._screen_operator_text(self, session_id, operator_id, text)

    async def operator_message(self, session_id: str, operator_id: str, text: str) -> None:
        return await operator_flow.operator_message(self, session_id, operator_id, text)

    async def operator_attachment(
        self,
        session_id: str,
        operator_id: str,
        file: StoredFile,
        caption: str = "",
        request_id: str | None = None,
    ) -> dict[str, Any]:
        return await operator_flow.operator_attachment(
            self, session_id, operator_id, file, caption, request_id
        )

    async def operator_close(self, session_id: str, resolved: bool = True) -> None:
        return await operator_flow.operator_close(self, session_id, resolved)

    # document_flow
    async def ensure_session_open_for_document(self, session_id: str) -> None:
        return await document_flow.ensure_session_open_for_document(self, session_id)

    async def contract_summary(self, session_id: str, filename: str, summary: str) -> dict[str, Any]:
        return await document_flow.contract_summary(self, session_id, filename, summary)
