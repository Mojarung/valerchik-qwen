"""Versioned copilot runs with cross-process single-flight and durable diagnostics."""

import asyncio
import hashlib
import json
import time
from dataclasses import asdict
from typing import Any

from app.models import AuditLog, CopilotRun, new_id
from app.runtime import AppRuntime
from app.services import copilot


async def generate(  # noqa: PLR0915
    rt: AppRuntime, sid: str, question: str, reply_to: str = ""
) -> copilot.CopilotDraft:
    s = await rt.chat._get_session(sid)
    config = [rt.dyn.get("llm.model"), rt.dyn.get("copilot.system_prompt"), copilot.PROMPT_VERSION]
    prompt_version = hashlib.sha256(json.dumps(config).encode()).hexdigest()[:16]
    records = rt.retriever._snap.records
    kb_version = hashlib.sha256(json.dumps(records, sort_keys=True, default=str).encode()).hexdigest()[:16]
    version = s.workflow_version or 0
    key = hashlib.sha256(f"{sid}:{version}:{prompt_version}:{kb_version}".encode()).hexdigest()[:32]
    token = new_id()
    lock = f"copilot:run:{key}"
    timeout = float(rt.dyn.get("copilot.timeout_s")) + 15
    from redis.exceptions import RedisError

    try:
        acquired = await rt.cache.set(lock, token, nx=True, ex=int(timeout) + 5)
    except RedisError:
        return copilot.CopilotDraft(
            text="", status="error", error="Сервис генерации временно недоступен. Повторите запрос."
        )
    if not acquired:
        owner_token = await rt.cache.get(lock)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            await asyncio.sleep(0.2)
            async with rt.db.session_factory() as db:
                run = await db.get(CopilotRun, key)
                if run and run.payload.get("generation") == owner_token:
                    return copilot.CopilotDraft(**run.payload["draft"])
        return copilot.CopilotDraft(text="", status="error", error="Генерация не завершилась. Повторите.")
    started = time.monotonic()
    try:
        history = [(m["role"], m["content"]) for m in await rt.chat.get_messages(sid)]
        result = await copilot.draft_reply(
            rt.dyn, rt.retriever, history, question, reply_to=reply_to, audience=s.audience or ""
        )
        result.run_id, result.version = key, version
        fresh = await rt.chat._get_session(sid)
        if (fresh.workflow_version or 0) != version:
            result.status = "stale"
            result.error = "В диалоге есть новые сообщения. Обновите подсказку."
        payload = {
            "draft": asdict(result),
            "generation": token,
            "model": config[0],
            "prompt_version": prompt_version,
            "kb_version": kb_version,
            "latency_ms": round((time.monotonic() - started) * 1000),
        }
        async with rt.db.session_factory() as db:
            existing = await db.get(CopilotRun, key)
            if existing:
                existing.payload = payload
            else:
                db.add(CopilotRun(id=key, session_id=sid, version=version, payload=payload))
            db.add(
                AuditLog(
                    actor="copilot",
                    action="copilot.generated",
                    entity=sid,
                    payload={k: v for k, v in payload.items() if k != "draft"},
                )
            )
            await db.commit()
        return result
    finally:
        # Compare/delete is atomic: an expired worker cannot release another worker's lock.
        from redis.exceptions import WatchError

        try:
            async with rt.cache.pipeline(transaction=True) as pipe:
                await pipe.watch(lock)
                if await pipe.get(lock) == token:
                    pipe.multi()
                    pipe.delete(lock)
                    await pipe.execute()
        except WatchError, RedisError:
            pass


def payload(draft: copilot.CopilotDraft) -> dict[str, Any]:
    data = asdict(draft)
    data["draft"] = data.pop("text")
    return data
