"""Архивная статья не отвечает, даже если её точка осталась в векторном индексе.

Переимпорт отправляет прошлую версию документа в архив в базе, но вектор в
Qdrant остаётся. Поиск брал роль и вид статьи из этой точки и мог поднять
архивный ответ: на стенде так в индексе висела тысяча старых ответов выгрузки.
"""

from app.adapters.embeddings import HashEmbedder
from app.adapters.vectorstore import MemoryStore, VectorDoc
from app.dynamic_settings import SETTINGS
from app.services.retrieval import Retriever


class _Dyn:
    def get(self, key):
        return SETTINGS[key].default


def _article(article_id: str, question: str, answer: str) -> dict:
    return {
        "article_id": article_id,
        "question": question,
        "answer": answer,
        "category": "Работа с СТЕ",
        "line": 1,
        "keywords": [],
        "audience": "all",
        "updated_at": "",
        "kind": "answer",
    }


ACTIVE = _article("a" * 32, "Как исправить ошибку в СТЕ?", "Нажмите «Сообщить об ошибке» в карточке СТЕ.")
ARCHIVED = _article("b" * 32, "Как исправить ошибку в СТЕ?", "Старая версия ответа из прошлой выгрузки.")


async def test_archived_vector_point_is_not_an_answer(tmp_path):
    embedder = HashEmbedder()
    store = MemoryStore(tmp_path)
    await store.ensure_ready(embedder.dim)
    vectors = await embedder.embed([ACTIVE["question"], ARCHIVED["question"]])
    # В векторном хранилище обе точки: архивную при замене версии никто не удалил
    await store.upsert(
        [
            VectorDoc(id=ACTIVE["article_id"], vector=vectors[0], payload=ACTIVE),
            VectorDoc(id=ARCHIVED["article_id"], vector=vectors[1], payload=ARCHIVED),
        ]
    )
    retriever = Retriever(embedder, store, _Dyn())
    retriever.load([ACTIVE])  # среди активных в базе — только новая версия

    docs = await retriever.search("как исправить ошибку в сте")

    assert [d.article_id for d in docs] == [ACTIVE["article_id"]]
