"""Коды ошибок портала в поиске: «РДИК 0182» в вопросе находит инструкцию к
РДИК_0182, а не к соседнему коду, написанной по тому же шаблону.

На замере по реальным обращениям СТП бот на «ошибка РДИК 0182» отвечал статьёй
про РДИК_0210: инструкции по ошибкам близки по словам, и ни векторы, ни лексика
номер кода не различают.
"""

from app.adapters.embeddings import HashEmbedder
from app.adapters.vectorstore import MemoryStore
from app.dynamic_settings import SETTINGS
from app.services.retrieval import (
    CODE_CONFLICT_FACTOR,
    CODE_MATCH_SCORE,
    CodeIndex,
    RetrievedDoc,
    Retriever,
    canonical_codes,
)


class _Dyn:
    def __init__(self, **overrides):
        self.overrides = overrides

    def get(self, key):
        return self.overrides[key] if key in self.overrides else SETTINGS[key].default


def _article(article_id: str, question: str, answer: str, keywords: tuple[str, ...] = ()) -> dict:
    return {
        "article_id": article_id,
        "question": question,
        "answer": answer,
        "category": "Электронное исполнение",
        "line": 2,
        "keywords": list(keywords),
        "audience": "all",
        "updated_at": "",
        "kind": "answer",
    }


TEMPLATE = "Для устранения ошибки {code} проверьте сведения в документе исполнения и повторите отправку."
WRONG, RIGHT, KEYWORD_ONLY, NO_CODE = "a" * 32, "b" * 32, "c" * 32, "d" * 32
ARTICLES = [
    _article(WRONG, "Как устранить ошибку РДИК_0210?", TEMPLATE.format(code="РДИК_0210") + " Ставка НДС."),
    _article(RIGHT, "Как устранить ошибку РДИК_0182?", TEMPLATE.format(code="РДИК_0182") + " Цена упаковки."),
    _article(
        KEYWORD_ONLY,
        "Сведения о контракте не дошли до портала",
        "Заказчику нужно обратиться в службу поддержки ЕАИСТ.",
        keywords=("Ошибка РДИК_ИК_0011: сведения о контракте",),
    ),
    _article(
        NO_CODE, "Как подписать УПД в 2024 году?", "Подпишите УПД электронной подписью в карточке исполнения."
    ),
]


def _retriever(tmp_path) -> Retriever:
    retriever = Retriever(HashEmbedder(), MemoryStore(tmp_path), _Dyn())
    retriever.load(ARTICLES)
    return retriever


def test_articles_are_read_in_portal_spelling_only():
    assert canonical_codes("Ошибка РДИК_ИК_0011, затем РДИК_0182.") == {"РДИК_ИК_0011", "РДИК_0182"}
    # Без подчёркивания в тексте статьи код не считается: так пишет не портал
    assert canonical_codes("рдик 0182") == frozenset()


def test_question_forms_are_recognized_for_known_prefixes(tmp_path):
    codes = _retriever(tmp_path).codes
    assert codes.asked("ошибка рдик 0182") == {"РДИК_0182"}
    assert codes.asked("РДИК-0210 при отправке") == {"РДИК_0210"}
    assert codes.asked("выдаёт РДИК ИК 0011") == {"РДИК_ИК_0011"}
    # «УПД 2024» — не код: префикса УПД среди кодов базы нет
    assert codes.asked("как подписать УПД 2024") == frozenset()


async def test_same_code_wins_over_template_neighbour(tmp_path):
    docs = await _retriever(tmp_path).search("ошибка рдик 0182 что делать")

    assert docs[0].article_id == RIGHT
    assert docs[0].score >= CODE_MATCH_SCORE
    assert all(d.score < docs[0].score for d in docs[1:])


async def test_code_named_only_in_keywords_is_found(tmp_path):
    docs = await _retriever(tmp_path).search("РДИК ИК 0011")

    assert docs[0].article_id == KEYWORD_ONLY
    assert docs[0].score >= CODE_MATCH_SCORE


def test_weighing_rules(tmp_path):
    retriever = _retriever(tmp_path)
    docs = {aid: RetrievedDoc(aid, "", "", "", 1, score=0.6) for aid in (WRONG, RIGHT, NO_CODE)}

    retriever._weigh_error_codes(docs, frozenset({"РДИК_0182"}))

    assert docs[RIGHT].score == CODE_MATCH_SCORE
    # Другой код того же семейства — ловушка: уверенность падает
    assert docs[WRONG].score == round(0.6 * CODE_CONFLICT_FACTOR, 4)
    # Статья без кодов — не про ошибку вовсе, её уверенность код не трогает
    assert docs[NO_CODE].score == 0.6


async def test_question_without_codes_is_untouched(tmp_path):
    retriever = _retriever(tmp_path)
    with_codes = await retriever.search("как подписать упд в 2024 году")
    retriever.codes = CodeIndex()
    without_codes = await retriever.search("как подписать упд в 2024 году")

    assert [(d.article_id, d.score) for d in with_codes] == [(d.article_id, d.score) for d in without_codes]
