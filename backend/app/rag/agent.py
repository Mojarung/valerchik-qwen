"""ReAct-агент поддержки: модель сама ищет в базе знаний (search_kb), при необходимости уточняет
запрос и ищет снова, отвечает только по найденным фрагментам со ссылками [S1] и честно
отказывает, если ответа нет. История диалога идёт модели настоящими сообщениями.
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from pydantic_ai import Agent, RunContext
from pydantic_ai.exceptions import UsageLimitExceeded
from pydantic_ai.messages import ModelMessage, ModelRequest, ModelResponse, TextPart, UserPromptPart
from pydantic_ai.models.openai import OpenAIChatModelSettings
from pydantic_ai.toolsets import WrapperToolset
from pydantic_ai.usage import UsageLimits

from app.rag.text import lemmas
from app.services import answer_guard
from app.services import audience as audience_of
from app.services.agent import (
    _DATA,
    _FAILED,
    NO_ANSWER,
    AgentOutcome,
    AgentRequest,
    _operator_offers,
    _tool_result,
    asked_for_operator,
    build_model,
    llm_configured,
    products_from_tools,
)

if TYPE_CHECKING:
    from pydantic_ai.toolsets.abstract import ToolsetTool

    from app.dynamic_settings import DynamicSettings
    from app.rag.search import RagRetriever
    from app.services.retrieval import RetrievedDoc
    from app.services.routing import LineClassifier

log = logging.getLogger(__name__)

TOP_K = 6
PASSAGE_CHARS = 2200
HISTORY_MESSAGES = 10
HISTORY_CHARS = 1500
MAX_CITATIONS = 3
EXTRACTIVE_MIN_SCORE = 0.62
PROVIDER_FALLBACK_MIN_TITLE_SCORE = 0.55
_GENERIC_QUERY_WORDS = frozenset(lemmas("портал поставщик заказчик пользователь раздел страница"))
SHORT_TOPIC_WORDS = 2
MAX_OUTPUT_TOKENS = 1500
FOLLOW_UP_WORDS = 6
MIN_SUPPORT = 0.5
_QUOTED = re.compile(r"«([^«»]{2,80})»")


def _lexically_related(query: str, doc: RetrievedDoc) -> bool:
    """Не отдавать при офлайн-ответе уверенную, но предметно чужую статью."""
    query_words = set(lemmas(query)) - _GENERIC_QUERY_WORDS
    if len(query_words) <= 1:
        return True
    source_words = set(lemmas(f"{doc.question}\n{doc.answer}"))
    return bool(query_words & source_words)


SYSTEM_PROMPT = """Ты — Валерчик, ассистент техподдержки Портала поставщиков Москвы (zakupki.mos.ru). \
Помогаешь поставщикам и заказчикам. Пишешь по-русски, вежливо и по делу.

Как работать:
1. К сообщению пользователя приложены фрагменты базы знаний, найденные по его словам. Если они отвечают \
на вопрос — отвечай по ним сразу.
2. Если фрагменты не отвечают на вопрос или сообщение — продолжение диалога («а как его подписать?», \
«а для заказчика?»), вызови search_kb. Запрос формулируй сам: полным вопросом по сути, местоимения раскрой \
из истории, сокращения оставляй как есть. Не больше двух дополнительных поисков.
3. Отвечай ТОЛЬКО по найденным фрагментам. Не добавляй от себя шаги, кнопки, разделы, сроки, суммы, \
телефоны и ссылки, которых нет во фрагментах. Не додумывай.
4. Прежде чем отказать, обязательно поищи ещё раз через search_kb: исправь опечатки, замени слова \
синонимами, сформулируй короче. Если и тогда во фрагментах нет ответа — ответь ровно одним словом: NO_ANSWER.
5. Если ответ есть лишь частично — дай то, что есть, и прямо скажи, чего в базе знаний нет.
6. После утверждения ставь ссылку на фрагмент, из которого оно взято: [S1], [S2].
7. Оформление: сразу к сути, без вступлений и повтора вопроса. Инструкции — нумерованными шагами, \
названия кнопок, полей и разделов — в «кавычках», как во фрагменте. В «кавычках» — только то, что \
дословно стоит во фрагменте; своё пояснение, пример или перефраз пиши без кавычек. На вопрос «что такое» — \
расшифровка и одна-две фразы из фрагментов о том, где и зачем это используется на портале.
8. Учитывай роль собеседника. Инструкции для другой роли не выдавай за его инструкции.
9. Приветствие, благодарность или вопрос о тебе — ответь одной короткой фразой без поиска.
10. Инструмент escalate_to_operator — только если человек сам просит оператора или живого человека."""

MCP_NOTE = """Дополнительно доступны инструменты внешнего каталога портала (поиск товаров и цен). \
Если человек ищет товар, цену или предложение — вызови их и ответь по их результату, без NO_ANSWER. \
Интерфейс сам показывает найденные товары карточками: не перечисляй их таблицей и ссылками, \
напиши коротко, что нашлось и как уточнить запрос. Не показывай служебные идентификаторы."""

RETRY_NOTE = """Пользователь сообщил, что прошлый ответ не помог. Заново выполни search_kb с другой \
формулировкой. Прошлый ответ не источник фактов."""

# Ссылки на фрагменты: [S1], [S1, S3], а также как их пишет модель — кириллической «С»
# и жирным: [С1], [**S1**]
_CITE = re.compile(r"\s*[\[【]\s*\**\s*([SС]\d+(?:\s*\**\s*[,;]\s*\**\s*[SС]?\d+)*)\s*\**\s*[\]】]")
_SID = re.compile(r"[SС]?(\d+)")
# Что остаётся от фразы, когда из неё вырезана ссылка: «см. .», «()», «,,»
_EMPTY_REFERENCE = re.compile(
    r"\(\s*(?:см\.?)?\s*\)|\s*[–—-]?\s*\bсм\.?\s*(?=[.,;:!?)\n]|$)|\s+[–—]\s*(?=[.,;:!?]|$)", re.MULTILINE
)
_DOUBLE_PUNCT = re.compile(r"([,;:])(?:\s*[,;:])+|,(?=\.)")
_SMALLTALK = re.compile(
    r"^\W*(?:привет|здравствуй|добр\w+ (?:день|утро|вечер)|спасибо|благодар|кто ты|что ты умеешь|"
    r"ты кто|пока|до свидания|ок|окей|хорошо|понятно|ясно|отлично|супер)\b",
    re.IGNORECASE,
)
_SPACE_BEFORE_PUNCT = re.compile(r"[ \t]+([.,;:!?])")
# Служебные пометки, которые модель иногда переносит из вызовов инструментов: «[Searched]», «[Source]»
_TOOL_MARK = re.compile(
    r"\s*\[(?:Searched|Source|Tool|Search|link|ссылка|источник)[^\]]{0,40}\](?!\()", re.IGNORECASE
)


@dataclass
class Deps:
    retriever: RagRetriever
    classifier: LineClassifier
    dyn: DynamicSettings
    audience: str
    user_text: str
    operator_requested: bool
    shown: dict[str, RetrievedDoc] = field(default_factory=dict)
    sid_of: dict[str, str] = field(default_factory=dict)
    tool_trace: list[dict[str, Any]] = field(default_factory=list)
    external_calls: list[tuple[str, str]] = field(default_factory=list)
    escalated: bool = False
    line: int | None = None
    escalation_message: str = ""

    def remember(self, doc: RetrievedDoc) -> str:
        sid = self.sid_of.get(doc.article_id)
        if sid is None:
            sid = f"S{len(self.sid_of) + 1}"
            self.sid_of[doc.article_id] = sid
            self.shown[sid] = doc
        return sid

    @property
    def searched(self) -> bool:
        return any(step.get("tool") == "search_kb" for step in self.tool_trace)

    @property
    def top_score(self) -> float:
        return max((d.score for d in self.shown.values()), default=0.0)


class _Recorded(WrapperToolset[Deps]):
    """Тулсет внешнего сервера (MCP), который записывает, чем кончился каждый вызов:
    данные, пустой ответ или сбой. Исключение обрывает прогон, поэтому пишем в deps сразу."""

    async def call_tool(
        self, name: str, tool_args: dict[str, Any], ctx: RunContext[Deps], tool: ToolsetTool[Deps]
    ) -> Any:
        try:
            result = await super().call_tool(name, tool_args, ctx, tool)
        except BaseException:
            ctx.deps.external_calls.append((name, _FAILED))
            raise
        ctx.deps.external_calls.append((name, _tool_result(result)))
        return result


def _instructions(request: AgentRequest, audience: str) -> str:
    parts = [SYSTEM_PROMPT]
    if audience_of.is_known(audience):
        parts.append(f"Собеседник — {audience_of.label(audience).lower()} ({audience_of.HINTS[audience]}).")
    else:
        parts.append(
            "Роль собеседника неизвестна. Если ответ различается для поставщика и заказчика — "
            "дай оба варианта кратко, каждый со своей подписью."
        )
    if request.page_context:
        parts.append(f"Пользователь пишет со страницы портала: {request.page_context}")
    if request.response_style:
        parts.append(RETRY_NOTE)
    if request.response_style == "steps":
        parts.append("Объясни простыми словами короткими нумерованными шагами.")
    if request.mcp_toolsets:
        parts.append(MCP_NOTE)
    return "\n\n".join(parts)


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1].rsplit(" ", 1)[0] + "…"


def _history(history: list[tuple[str, str]]) -> list[ModelMessage]:
    """Реплики диалога → сообщения модели: чередование «пользователь — ассистент», начиная с пользователя."""
    turns: list[tuple[str, str]] = []
    for role, text in history[-HISTORY_MESSAGES:]:
        if role not in ("user", "assistant") or not text.strip():
            continue
        text = _clip(text.strip(), HISTORY_CHARS)
        if turns and turns[-1][0] == role:
            turns[-1] = (role, f"{turns[-1][1]}\n{text}")
        else:
            turns.append((role, text))
    while turns and turns[0][0] != "user":
        turns.pop(0)
    return [
        ModelRequest(parts=[UserPromptPart(content=text)])
        if role == "user"
        else ModelResponse(parts=[TextPart(content=text)])
        for role, text in turns
    ]


def _passage(sid: str, doc: RetrievedDoc) -> str:
    text = _clip(answer_guard.sanitize(doc.answer), PASSAGE_CHARS)
    return f"[{sid}] {doc.category} › {doc.question}\n{text}"


def _register_tools(agent: Agent[Deps, str]) -> None:
    @agent.tool
    async def search_kb(ctx: RunContext[Deps], query: str) -> str:
        """Поиск по базе знаний портала: инструкции, термины, решения техподдержки.

        query — вопрос по сути одной фразой.
        """
        docs = await ctx.deps.retriever.search(query, top_k=TOP_K, audience=ctx.deps.audience)
        ctx.deps.tool_trace.append(
            {
                "tool": "search_kb",
                "query": query,
                "found": len(docs),
                "top_score": max((d.score for d in docs), default=0.0),
            }
        )
        if not docs:
            return "Ничего не найдено."
        return "\n\n".join(_passage(ctx.deps.remember(doc), doc) for doc in docs)

    @agent.tool
    async def escalate_to_operator(ctx: RunContext[Deps], reason: str) -> dict[str, Any]:
        """Передать диалог живому оператору. Только если человек сам попросил оператора."""
        if not ctx.deps.operator_requested:
            ctx.deps.tool_trace.append({"tool": "escalate_to_operator", "reason": reason, "declined": True})
            return {
                "status": "declined",
                "message": "Человек не просил оператора. Ответь по search_kb или верни NO_ANSWER.",
            }
        return await _escalate(ctx.deps, reason)


async def _escalate(deps: Deps, reason: str) -> dict[str, Any]:
    """Перевод на оператора: линию выбирает классификатор по словам человека."""
    prediction = await deps.classifier.predict(deps.user_text or reason, audience=deps.audience)
    lines: dict[str, Any] = deps.dyn.get("routing.lines") or {}
    label = prediction.addressee_label or (lines.get(str(prediction.line)) or {}).get("label", "поддержка")
    message = str(deps.dyn.get("agent.escalation_message")).format(line=prediction.line, line_label=label)
    deps.escalated = True
    deps.line = prediction.line
    deps.escalation_message = message
    deps.tool_trace.append(
        {
            "tool": "escalate_to_operator",
            "reason": reason,
            "line": prediction.line,
            "proba": round(prediction.proba, 3),
            "router": prediction.source,
        }
    )
    return {"status": "escalated", "line": prediction.line, "line_label": label, "message": message}


def _citation(doc: RetrievedDoc) -> dict[str, Any]:
    return {
        "article_id": doc.article_id,
        "question": doc.question,
        "category": doc.category,
        "score": doc.score,
    }


def _refusal(dyn: DynamicSettings, deps: Deps, key: str = "agent.no_answer_message") -> AgentOutcome:
    return AgentOutcome(
        answer=str(dyn.get(key)),
        answered=False,
        confidence=deps.top_score,
        citations=[],
        escalated=False,
        line=None,
        tool_trace=deps.tool_trace,
        offer_operator=True,
    )


def _provider_fallback(dyn: DynamicSettings, deps: Deps, failure: str) -> AgentOutcome:
    """Answer from an exact KB article when the model provider is unavailable.

    The model is still attempted first.  On a provider outage we only expose a
    verbatim article when both the hybrid score and the title-to-question score
    are strong.  This avoids turning an unrelated first search hit into an
    authoritative answer while keeping basic, explicitly covered questions
    available during quota and network failures.
    """
    deps.tool_trace.append({"tool": "llm", "result": "unavailable", "reason": failure})
    candidates = sorted(
        deps.shown.values(),
        key=lambda doc: (doc.title_score, doc.score),
        reverse=True,
    )
    if candidates:
        top = candidates[0]
        if (
            top.score >= EXTRACTIVE_MIN_SCORE
            and top.title_score >= PROVIDER_FALLBACK_MIN_TITLE_SCORE
            and _lexically_related(deps.user_text, top)
        ):
            return AgentOutcome(
                answer=answer_guard.sanitize(top.answer),
                answered=True,
                confidence=top.score,
                citations=[_citation(top)],
                escalated=False,
                line=None,
                tool_trace=deps.tool_trace,
            )
    return _refusal(dyn, deps, "agent.unavailable_message")


def _cited(output: str, deps: Deps) -> list[RetrievedDoc]:
    order: list[str] = []
    for group in _CITE.findall(output):
        for number in _SID.findall(group):
            sid = f"S{number}"
            if sid in deps.shown and sid not in order:
                order.append(sid)
    return [deps.shown[sid] for sid in order]


def _doc_words(doc: RetrievedDoc) -> set[str]:
    return set(lemmas(f"{doc.question}\n{doc.category}\n{doc.answer}"))


def _quotes_grounded(text: str, deps: Deps) -> bool:
    """Кнопки, разделы и статусы в «кавычках» взяты из фрагментов, а не придуманы моделью.

    Сверка по леммам: модель вправе склонять и сокращать («Добавить исполнение» →
    «кнопку Добавить исполнения»), но не вправе вводить слова, которых во фрагментах нет.
    """
    quoted = [set(lemmas(q)) for q in _QUOTED.findall(text)]
    quoted = [q for q in quoted if q]
    if not quoted:
        return True
    source = set().union(*(_doc_words(d) for d in deps.shown.values()))
    missing = sum(1 for q in quoted if not q <= source)
    return missing * 2 <= len(quoted)


def _support(text: str, doc: RetrievedDoc) -> float:
    """Доля значимых слов ответа, которые есть во фрагменте."""
    words = set(lemmas(text))
    return len(words & _doc_words(doc)) / len(words) if words else 0.0


async def _grounded_prompt(deps: Deps, request: AgentRequest) -> str:
    """Сообщение модели: вопрос и фрагменты, найденные по нему заранее.

    Поиск по вопросу нужен всегда, поэтому он делается до модели: она не может
    ответить «по памяти», а на ясный вопрос отвечает за один вызов вместо двух.
    Короткое продолжение ищется вместе с прошлым вопросом человека.
    """
    text = request.user_text
    if request.follow_up_of:
        text = f"{request.follow_up_of}\n{text}"
    last_user = next((t for role, t in reversed(request.history) if role == "user"), "")
    # Склейку с прошлым вопросом уже собрал чат (follow_up_of) — второй раз его не дописываем
    glue = last_user and not request.follow_up_of and len(request.user_text.split()) <= FOLLOW_UP_WORDS
    query = f"{last_user}\n{text}" if glue else text
    docs = await deps.retriever.search(query, top_k=TOP_K, audience=deps.audience)
    deps.tool_trace.append(
        {
            "tool": "search_kb",
            "query": query,
            "found": len(docs),
            "top_score": max((d.score for d in docs), default=0.0),
            "auto": True,
        }
    )
    if not docs:
        return text
    found = "\n\n".join(_passage(deps.remember(doc), doc) for doc in docs)
    return f"{text}\n\n---\nФрагменты базы знаний, найденные по этому сообщению:\n\n{found}"


def _clean(output: str) -> str:
    text = _CITE.sub("", _TOOL_MARK.sub("", output))
    text = _EMPTY_REFERENCE.sub("", answer_guard.sanitize(text))
    text = _DOUBLE_PUNCT.sub(r"\1", _SPACE_BEFORE_PUNCT.sub(r"\1", text))
    return _SPACE_BEFORE_PUNCT.sub(r"\1", text).strip().rstrip(",;")


def _outcome(dyn: DynamicSettings, deps: Deps, output: str, products: list[dict[str, Any]]) -> AgentOutcome:
    if deps.escalated:
        return AgentOutcome(
            answer=deps.escalation_message,
            answered=False,
            confidence=deps.top_score,
            citations=[],
            escalated=True,
            line=deps.line,
            tool_trace=deps.tool_trace,
        )
    results = {kind for _, kind in deps.external_calls}
    if results == {_FAILED}:
        return _refusal(dyn, deps, "mcp.unavailable_message")
    helped = _DATA in results
    if not output.strip() or NO_ANSWER in output:
        return _refusal(dyn, deps)
    docs = _cited(output, deps)
    text = _clean(output)
    if not text:
        return _refusal(dyn, deps)
    if not helped:
        if not docs and deps.shown and not _SMALLTALK.search(deps.user_text):
            # Ссылку модель может забыть; тогда источник — фрагмент, на который ответ опирается
            best = max(deps.shown.values(), key=lambda d: _support(text, d))
            if _support(text, best) >= MIN_SUPPORT:
                docs = [best]
        if not docs and not _SMALLTALK.search(deps.user_text):
            log.warning("Ответ не опирается на фрагменты базы знаний — отказ")
            return _refusal(dyn, deps)
        if docs and not _quotes_grounded(text, deps):
            log.warning("В ответе названия, которых нет во фрагментах, — отказ")
            return _refusal(dyn, deps)
    return AgentOutcome(
        answer=text,
        answered=True,
        confidence=max((d.score for d in docs), default=0.0),
        citations=[_citation(d) for d in docs[:MAX_CITATIONS]],
        escalated=False,
        line=None,
        tool_trace=deps.tool_trace,
        products=products,
    )


async def _extractive(dyn: DynamicSettings, deps: Deps, request: AgentRequest) -> AgentOutcome:
    """Без модели: лучший фрагмент как есть, если он уверенно про вопрос."""
    query = f"{request.follow_up_of}\n{request.user_text}".strip()
    docs = await deps.retriever.search(query, top_k=3, audience=deps.audience)
    deps.tool_trace.append({"tool": "search_kb", "query": query, "found": len(docs)})
    if not docs:
        return _refusal(dyn, deps)
    top = docs[0]
    threshold = EXTRACTIVE_MIN_SCORE
    query_words = set(lemmas(request.user_text)) - _GENERIC_QUERY_WORDS
    exact_short_topic = (
        audience_of.is_known(deps.audience)
        and 0 < len(query_words) <= SHORT_TOPIC_WORDS
        and bool(query_words & set(lemmas(top.question)))
        and top.score >= float(dyn.get("agent.confidence_threshold")) * 0.75
    )
    if top.score < threshold and not exact_short_topic:
        return _refusal(dyn, deps)
    if exact_short_topic and not _lexically_related(query, top):
        return _refusal(dyn, deps)
    return AgentOutcome(
        answer=answer_guard.sanitize(top.answer),
        answered=True,
        confidence=top.score,
        citations=[_citation(top)],
        escalated=False,
        line=None,
        tool_trace=deps.tool_trace,
    )


def _model_settings(dyn: DynamicSettings) -> OpenAIChatModelSettings:
    # Сколько модели «думать» перед ответом — настройка, а не константа: Qwen с
    # рассуждениями втрое медленнее и чаще дописывает от себя, но на сложном вопросе
    # их можно включить из админки, не пересобирая образ
    effort = str(dyn.get("agent.reasoning_effort") or "none")
    return OpenAIChatModelSettings(openai_reasoning_effort=effort, max_tokens=MAX_OUTPUT_TOKENS)  # type: ignore[typeddict-item]


async def run_support_agent(
    dyn: DynamicSettings, retriever: RagRetriever, classifier: LineClassifier, request: AgentRequest
) -> AgentOutcome:
    audience = audience_of.normalize(request.audience)
    deps = Deps(
        retriever=retriever,
        classifier=classifier,
        dyn=dyn,
        audience=audience,
        user_text=request.user_text,
        operator_requested=asked_for_operator(request.user_text, request.history, _operator_offers(dyn)),
    )
    if not llm_configured(dyn):
        # Без модели просьбу об операторе исполняем сами. С моделью решает она: «оператор ЭДО»
        # в вопросе — термин, а не просьба, и переводить такой вопрос на человека нельзя.
        if deps.operator_requested:
            await _escalate(deps, request.user_text)
            return _outcome(dyn, deps, "", [])
        return await _extractive(dyn, deps, request)
    agent: Agent[Deps, str] = Agent(
        build_model(dyn),
        deps_type=Deps,
        instructions=_instructions(request, audience),
        retries=1,
        toolsets=[_Recorded(toolset) for toolset in request.mcp_toolsets or []],
        model_settings=_model_settings(dyn),
    )
    _register_tools(agent)
    prompt = await _grounded_prompt(deps, request)
    limits = UsageLimits(
        request_limit=int(dyn.get("agent.max_steps")) or None,
        tool_calls_limit=int(dyn.get("agent.max_tool_calls")) or None,
    )
    output, products = NO_ANSWER, []
    provider_failure: str | None = None
    try:
        result = await asyncio.wait_for(
            agent.run(prompt, deps=deps, message_history=_history(request.history), usage_limits=limits),
            timeout=request.timeout_s,
        )
        output, products = (result.output or "").strip(), products_from_tools(result.all_messages())
    except TimeoutError:
        log.warning("Агент не уложился в %.0f с", request.timeout_s)
        provider_failure = "timeout"
    except UsageLimitExceeded as exceeded:
        log.warning("Агент исчерпал предел шагов: %s", exceeded)
        provider_failure = "usage_limit"
    except Exception:
        log.exception("Ошибка агента — отказ")
        provider_failure = "provider_error"
    deps.tool_trace.extend(
        {"tool": name, "source": "mcp", "result": kind} for name, kind in deps.external_calls
    )
    if provider_failure:
        return _provider_fallback(dyn, deps, provider_failure)
    return _outcome(dyn, deps, output, products)
