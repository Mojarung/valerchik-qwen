from app.rag.agent import Deps, _provider_fallback
from app.services.retrieval import RetrievedDoc


class _Dyn:
    values = {
        "agent.unavailable_message": "LLM unavailable",
        "agent.no_answer_message": "No answer",
    }

    def get(self, key: str) -> object:
        return self.values[key]


def _deps(doc: RetrievedDoc) -> Deps:
    deps = Deps(
        retriever=object(),  # type: ignore[arg-type]
        classifier=object(),  # type: ignore[arg-type]
        dyn=_Dyn(),  # type: ignore[arg-type]
        audience="supplier",
        user_text="Как зарегистрироваться на Портале поставщиков?",
        operator_requested=False,
    )
    deps.shown["S1"] = doc
    return deps


def test_provider_failure_returns_exact_confident_article() -> None:
    doc = RetrievedDoc(
        article_id="registration",
        question="Как зарегистрироваться на Портале поставщиков?",
        answer="Откройте форму регистрации.",
        category="Регистрация",
        line=1,
        score=0.8,
        title_score=1.0,
    )

    outcome = _provider_fallback(_Dyn(), _deps(doc), "provider_error")  # type: ignore[arg-type]

    assert outcome.answered is True
    assert outcome.answer == doc.answer
    assert outcome.citations[0]["article_id"] == doc.article_id
    assert outcome.tool_trace[-1] == {
        "tool": "llm",
        "result": "unavailable",
        "reason": "provider_error",
    }


def test_provider_failure_does_not_substitute_weakly_related_hit() -> None:
    doc = RetrievedDoc(
        article_id="upd-definition",
        question="Что такое УПД?",
        answer="УПД — универсальный передаточный документ.",
        category="Документы",
        line=1,
        score=0.8,
        title_score=0.25,
    )

    outcome = _provider_fallback(_Dyn(), _deps(doc), "provider_error")  # type: ignore[arg-type]

    assert outcome.answered is False
    assert outcome.answer == "LLM unavailable"
    assert outcome.offer_operator is True
