"""Иллюстрации к ответу: только у цитат, которым ответ действительно верит."""

from types import SimpleNamespace

from app.chat import knowledge
from app.db import Database
from app.models import KBArticle, new_id

THRESHOLD = 0.4
STRONG = "a" * 32
WEAK = "b" * 32


async def _service(tmp_path):
    db = Database(f"sqlite+aiosqlite:///{tmp_path / 'db.sqlite3'}")
    await db.create_all()
    async with db.session_factory() as session:
        session.add(
            KBArticle(
                id=STRONG,
                question="Что означает УПД?",
                answer="УПД — универсальный передаточный документ.",
                category="Термины",
                images=[{"url": "/s.png", "caption": "Рисунок 1 - Карточка УПД"}],
            )
        )
        session.add(
            KBArticle(
                id=WEAK,
                question="11.5.8 Формирование УПД",
                answer="Порядок формирования.",
                category="Исполнение",
                images=[{"url": "/w.png", "caption": "Рисунок 357 - Предварительное скачивание файлов"}],
            )
        )
        session.add(KBArticle(id=new_id(), question="Без картинок", answer="Текст.", category="Прочее"))
        await session.commit()
    return SimpleNamespace(rt=SimpleNamespace(db=db, dyn=SimpleNamespace(get=lambda _key: THRESHOLD)))


async def test_weak_citation_does_not_bring_its_picture(tmp_path):
    """Слабая цитата стоит в источниках как «возможно, пригодится», но её картинка
    показывалась рядом с ответом наравне с главной: к определению «УПД —
    универсальный передаточный документ» приезжал «Рисунок 357 - Предварительное
    скачивание файлов исправительного УПД» из соседней статьи."""
    svc = await _service(tmp_path)
    citations = [{"article_id": STRONG, "score": 0.73}, {"article_id": WEAK, "score": 0.25}]

    try:
        await knowledge._attach_citation_images(svc, citations)
    finally:
        await svc.rt.db.dispose()

    assert citations[0]["images"] == [{"url": "/s.png", "caption": "Рисунок 1 - Карточка УПД"}]
    assert "images" not in citations[1]


async def test_confident_citations_keep_pictures_the_answer_talks_about(tmp_path):
    """Текст инструкций ссылается на иллюстрации прямо («нажмите (Рисунок 26 (2))»),
    поэтому у уверенных источников картинки остаются — в том числе не у первого,
    если ответ говорит о том, что на них изображено."""
    svc = await _service(tmp_path)
    citations = [{"article_id": STRONG, "score": 0.73}, {"article_id": WEAK, "score": 0.55}]
    answer = "Откройте карточку УПД и выполните предварительное скачивание файлов."

    try:
        await knowledge._attach_citation_images(svc, citations, answer)
    finally:
        await svc.rt.db.dispose()

    assert citations[0]["images"] and citations[1]["images"]


async def test_without_matching_captions_only_the_main_source_brings_a_picture(tmp_path):
    """Подпись ни одной картинки с ответом не совпала — одна картинка главного источника,
    а не все скриншоты всех уверенных цитат (53c4b34: не больше двух картинок под ответом)."""
    svc = await _service(tmp_path)
    citations = [{"article_id": STRONG, "score": 0.73}, {"article_id": WEAK, "score": 0.55}]

    try:
        await knowledge._attach_citation_images(svc, citations, "Обратитесь в службу поддержки.")
    finally:
        await svc.rt.db.dispose()

    assert citations[0]["images"] == [{"url": "/s.png", "caption": "Рисунок 1 - Карточка УПД"}]
    assert "images" not in citations[1]
