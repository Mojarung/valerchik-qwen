"""Передача диалога человеку и всё, что делает оператор.

Эскалация, назначение, сообщения и вложения обеих сторон, закрытие обращения.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import select

from app.chat.errors import ChatError
from app.models import AuditLog, Message
from app.services import guardrails, lines, support_flow
from app.uploads import StoredFile

if TYPE_CHECKING:
    from app.services.chat import ChatService
    from app.services.routing import RoutePrediction

log = logging.getLogger(__name__)


async def _route(svc: ChatService, session_id: str, reason: str) -> tuple[RoutePrediction | None, str]:
    """Тема и адресат обращения для сообщения человеку и карточки в очереди.

    Если линию выбрал вызывающий (агент или принятое предложение), её и держим:
    человеку уже пообещали именно её. Если передан None, маршрут выбирает линию
    по исходному вопросу — так работает перевод после оценки «Ответ не помог».
    Здесь также определяются тема и адресат по регламенту.
    Сбой классификатора эскалацию не ломает: без темы обращение всё равно
    уходит в очередь, просто без подписи. Вторым значением — тема, уже
    записанная у диалога: её не затираем.
    """
    try:
        session = await svc._get_session(session_id)
        history = await svc.get_messages(session_id)

        questions = [
            m["content"]
            for m in history
            if m["role"] == "user"
            and m["content"].strip().lower() not in {"да", "нет", "давайте", "хорошо", "ок"}
        ]
        substantive = questions[-4:]
        latest_question = substantive[-1] if substantive else ""
        # Feedback carries new diagnostic details not persisted as user messages.
        if reason and reason not in substantive:
            substantive = [item for item in substantive if item not in reason] + [reason]
        context = "\n".join(substantive + ([session.page_url] if session.page_url else []))
        prediction = await svc.rt.classifier.predict(
            context or reason, audience=session.audience or "", dyn=svc.rt.dyn
        )
        # A direct rule is evaluated on the latest substantive user turn. Older
        # history still helps the classifier, but must not make a new payment
        # question look like the earlier blocked-account incident.
        rule_text = latest_question or reason or context
        addressee, rule = lines.addressee_by_rules(rule_text, session.audience or "")
        if rule and addressee != lines.LINE:
            from app.services.routing import RoutePrediction

            target = lines.parse_target(addressee, svc.rt.dyn.get("routing.addressees") or {})
            prediction = RoutePrediction(
                line=target.line,
                proba=1.0,
                source="rules",
                topic=prediction.topic,
                topic_proba=prediction.topic_proba,
                addressee=target.addressee,
                addressee_label=lines.addressee_label(
                    svc.rt.dyn.get("routing.addressees") or {}, target.addressee
                ),
                rule=rule,
            )
    except Exception:
        log.exception("Не удалось определить тему и адресата обращения %s", session_id)
        return None, ""
    return prediction, session.topic or ""


def _route_notice(svc: ChatService, line: int, route: RoutePrediction) -> str:
    """«Передаю обращение: линия 2 — …» или «Ваш вопрос решает СКК — …» — по настройкам."""
    dyn = svc.rt.dyn
    label = lines.line_label(dyn.get("routing.lines") or {}, line)
    if route.external:
        how = lines.addressee_how(dyn.get("routing.addressees") or {}, route.addressee)
        text = lines.fill(
            str(dyn.get("routing.external_notice") or ""),
            addressee=route.addressee_label,
            how=how,
            line=line,
            line_label=label,
        )
        return " ".join(text.split())
    # Тема другой линии в подписи сбивала бы: «линия 3 — Закупки (Электронное исполнение)»
    topic = route.topic if route.line == line else ""
    return lines.transfer_notice(str(dyn.get("routing.transfer_notice") or ""), line, label, topic)


def _labels(svc: ChatService, line: int, addressee: str) -> dict[str, str]:
    """Подписи адресата по текущим настройкам — те же, что в сообщении о передаче."""
    dyn = svc.rt.dyn
    return lines.route_labels(
        dyn.get("routing.lines") or {}, dyn.get("routing.addressees") or {}, line, addressee
    )


async def route_labels(svc: ChatService, session_id: str, line: int) -> dict[str, str]:
    """Кому передано обращение — для диалога, восстановленного после перезагрузки.

    Внешнего адресата у диалога не храним: он записан в сообщении о передаче,
    оттуда и берём (последнее — если передавали не раз). Подписи — из настроек
    заново: название линии могло смениться, пока человек ждал.
    """
    return (await routes_labels(svc, {session_id: line}))[session_id]


async def routes_labels(svc: ChatService, line_by_session: dict[str, int]) -> dict[str, dict[str, str]]:
    """То же для многих диалогов сразу — список «Моих обращений» одним запросом."""
    if not line_by_session:
        return {}
    async with svc.rt.db.session_factory() as db:
        notes = (
            await db.execute(
                select(Message.session_id, Message.meta)
                .where(Message.session_id.in_(line_by_session), Message.role == "system")
                .order_by(Message.created_at.desc())
            )
        ).all()
    addressee: dict[str, str] = {}
    for session_id, meta in notes:
        if (meta or {}).get("kind") == "routing":
            addressee.setdefault(session_id, str(meta.get("addressee") or lines.LINE))
    return {
        session_id: _labels(svc, line, addressee.get(session_id, lines.LINE))
        for session_id, line in line_by_session.items()
    }


async def _escalate(svc: ChatService, session_id: str, line: int | None, reason: str) -> None:
    route, known_topic = await _route(svc, session_id, reason)
    selected_line = route.line if route else 1
    uncertain = (
        route is None
        or route.source in ("unsure", "default")
        or route.proba < float(svc.rt.dyn.get("routing.line_min_confidence") or 0.46)
    )
    if uncertain:
        selected_line = 1
    async with svc.rt.db.session_factory() as db:
        s = await support_flow.lock_session(db, session_id)
        if s.status in support_flow.SUPPORT_STATUSES or s.status in ("resolved", "closed_violation"):
            return
        s.routing = {
            "line": selected_line,
            "topic": route.topic if route else "",
            "confidence": route.proba if route else 0,
            "source": route.source if route else "default",
            "reason": route.rule if route else "",
            "needs_triage": uncertain,
            "addressee": route.addressee if route else lines.LINE,
        }
        s.topic = (route.topic if route else known_topic)[:128]
        if route and route.external:
            # Keep the resolved direction across reloads, but do not put an
            # external referral into our queue or pretend that we delivered it.
            s.line = selected_line
            support_flow.notice(
                db,
                s,
                _route_notice(svc, selected_line, route),
                "routing",
                {"addressee": route.addressee, **_labels(svc, selected_line, route.addressee)},
            )
            support_flow.changed(db, s, "router", "external_referral")
        else:
            s.status, s.line, s.escalation_reason = "waiting_operator", selected_line, reason
            s.escalated_at = s.escalated_at or datetime.now(UTC)
            support_flow.changed(db, s, "router", "escalate")
            label = lines.line_label(svc.rt.dyn.get("routing.lines") or {}, selected_line)
            support_flow.notice(
                db,
                s,
                f"Обращение принято в очередь: {label}."
                + (" Нужно уточнить направление." if uncertain else ""),
                "routing",
                {"addressee": lines.LINE, **_labels(svc, selected_line, lines.LINE)},
            )
            support_flow.emit(
                db,
                s.id,
                f"session:{s.id}",
                {"type": "escalated", "line": selected_line, **_labels(svc, selected_line, lines.LINE)},
            )
        await db.commit()


async def _announce_route(svc: ChatService, session_id: str, line: int, route: RoutePrediction) -> None:
    """Человек видит, кому адресовано обращение: нашей линии или иному лицу по регламенту."""
    text = _route_notice(svc, line, route)
    if not text:  # администратор выключил сообщение, оставив шаблон пустым
        return
    msg = await svc._save_message(
        session_id,
        "system",
        text,
        meta={
            "kind": "routing",
            "line": line,
            "topic": route.topic if route.line == line else "",
            "addressee": route.addressee,
            **_labels(svc, line, route.addressee),
            "rule": route.rule,
            "router": route.source,
            "proba": round(route.proba, 3),
        },
    )
    await svc.rt.ws.publish(f"session:{session_id}", svc._msg_event(msg))


async def operator_take(svc: ChatService, session_id: str, operator_id: str, operator_name: str) -> None:
    await support_flow.transition(svc.rt, session_id, operator_id, "take")


async def notify_assigned(svc: ChatService, item: dict[str, Any]) -> None:
    """Обращение назначено оператору диспетчером.

    В диалог ничего не пишем: назначение — это ещё не приход человека, и
    «оператор подключился» до первой его реплики вводило бы в заблуждение.
    Событие адресное (комната operator:<id>) — попап должен всплыть у того,
    кому обращение досталось, а не у всей смены; очередь обновляем отдельным
    общим событием.
    """
    await svc.rt.ws.publish(f"operator:{item['operator_id']}", {"type": "assigned", **item})
    await svc.rt.ws.publish(
        "operators",
        {
            "type": "ticket_assigned",
            "session_id": item["session_id"],
            "operator_id": item["operator_id"],
            "operator_name": item["operator_name"],
        },
    )


async def ensure_session_open_for_upload(svc: ChatService, session_id: str) -> None:
    """Можно ли вообще что-то грузить в этот диалог.

    Отдельным методом, чтобы роутер спрашивал это ДО записи файла на диск:
    проверка «внутри» сохранения означала, что файл уже лежит, когда клиент
    получает отказ.
    """
    session = await svc._get_session(session_id)
    if session.status not in support_flow.SUPPORT_STATUSES:
        raise ChatError(409, "Прикрепить файл можно, только когда с вами работает оператор")


async def user_attachment(svc: ChatService, session_id: str, file: StoredFile) -> dict[str, Any]:
    """Скриншот от пользователя — только пока диалог ведёт оператор.

    Показать ошибку картинкой быстрее и точнее, чем описывать её словами, но
    принимать файлы в любой момент нельзя: это открытая загрузка без
    авторизации. Поэтому окно узкое — обращение уже передано человеку,
    который эту картинку и просит.
    """
    # Роутер спрашивает то же самое до записи файла; здесь — страховка на
    # случай другого вызывающего и защита от гонки со сменой статуса.
    await svc.ensure_session_open_for_upload(session_id)
    msg = await svc._save_message(
        session_id,
        "user",
        "",
        meta={"attachment": file.as_attachment("image")},
    )
    await svc.rt.ws.publish(f"session:{session_id}", svc._msg_event(msg))
    return {"status": "ok", "message_id": msg.id, "url": file.url}


async def _screen_operator_text(svc: ChatService, session_id: str, operator_id: str, text: str) -> None:
    """Мат в реплике оператора — не отправляем и пишем в аудит.

    Guardrails до сих пор стояли только на входящих сообщениях: всё, что
    писал оператор, уходило клиенту как есть. Оператор, обругавший клиента,
    хуже клиента, обругавшего бота, — здесь замечать это нужно тем более.

    Действие принципиально другое, чем для клиента. Диалог не закрываем:
    пострадал бы клиент, который ни при чём. Реплику просто не пропускаем и
    говорим об этом оператору, а факт кладём в журнал — это уже вопрос к
    человеку, и админ должен его видеть.

    Проверяем только мат — корни и словарь производных, как у клиента.
    Токсичность и нейросеть — оценочные, со скользящим порогом, и ложная
    блокировка сухого, но допустимого ответа мешала бы работать; для входящих
    они уместны, для исходящих цена ошибки выше.
    """
    if not svc.rt.dyn.get("guardrails.operator_check_enabled"):
        return
    if not svc.rt.dyn.get("guardrails.profanity_enabled"):
        return
    verdict = guardrails.detect_profanity(
        text,
        svc.rt.dyn.get("guardrails.profanity_extra_roots"),
        svc.rt.dyn.get("guardrails.profanity_exceptions"),
        check_layout=bool(svc.rt.dyn.get("guardrails.layout_profanity_check")),
        lexicon=bool(svc.rt.dyn.get("guardrails.lexicon_enabled")),
    )
    if not verdict.hit:
        return
    async with svc.rt.db.session_factory() as db:
        db.add(
            AuditLog(
                actor=operator_id,
                action="operator.profanity_blocked",
                entity=session_id,
                payload={"matched": verdict.matched, "text": text[:200]},
            )
        )
        await db.commit()
    log.warning("Реплика оператора %s не отправлена: мат %s", operator_id, verdict.matched)
    raise ChatError(
        422,
        "Сообщение не отправлено: в тексте нецензурная лексика. Переформулируйте — клиент этого не увидел.",
    )


async def operator_message(svc: ChatService, session_id: str, operator_id: str, text: str) -> None:
    await support_flow.send_message(svc.rt, session_id, operator_id, text)


async def operator_attachment(  # noqa: PLR0913
    svc: ChatService,
    session_id: str,
    operator_id: str,
    file: StoredFile,
    caption: str = "",
    request_id: str | None = None,
) -> dict[str, Any]:
    """Файл или картинка от оператора — отдельным сообщением в диалоге.

    Сам файл уже лежит в хранилище (его кладёт роутер, чтобы не тащить
    мегабайты через сервисный слой); здесь только запись в переписку и
    рассылка. Ссылка идёт в meta, а не в текст: так фронт покажет картинку
    картинкой, а документ — плашкой со скачиванием, не разбирая markdown.
    """
    kind = "image" if file.content_type.startswith("image/") else "file"
    result = await support_flow.send_message(
        svc.rt, session_id, operator_id, caption, request_id=request_id, attachment=file.as_attachment(kind)
    )
    return {"status": "ok", "message_id": result["message_id"], "url": file.url}


async def operator_close(svc: ChatService, session_id: str, resolved: bool = True) -> None:
    session = await svc._get_session(session_id)
    # Повторное «Завершить» (двойной клик, вторая вкладка) — не новое закрытие:
    # иначе человек получал просьбу оценить консультацию дважды подряд.
    if session.status in ("resolved", "closed_violation"):
        return
    await svc._update_session(session_id, status="resolved", closed_at=datetime.now(UTC))
    ask = str(svc.rt.dyn.get("feedback.request_message"))
    msg = await svc._save_message(session_id, "system", ask, meta={"kind": "feedback_request"})
    await svc.rt.ws.publish(f"session:{session_id}", svc._msg_event(msg))
    await svc.rt.ws.publish(f"session:{session_id}", {"type": "session_closed", "reason": "resolved"})
    await svc.rt.ws.publish("operators", {"type": "ticket_closed", "session_id": session_id})
