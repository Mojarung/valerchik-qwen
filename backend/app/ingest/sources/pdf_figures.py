"""PDF с иллюстрациями: docling размечает страницу и отдаёт картинки с подписями.

Источник живёт ровно при поднятом профиле `docs` — сервис разбора необязателен,
он тянет torch и веса. Когда его нет, `parse` отказывается, и тот же PDF достаётся
источнику document: текст из него вытащит markitdown, просто без скриншотов.

Отказ именно в `parse`, а не нулевой уверенностью в `sniff`: рантайм источник видит
только в контексте, а `sniff` спрашивают об одном файле. Ошибиться и уступить
очередь дешевле, чем занижать уверенность вслепую и терять иллюстрации там, где
сервис на самом деле поднят.
"""

from __future__ import annotations

import logging
import re
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any

from app.ingest.blob import FileBlob
from app.ingest.boilerplate import boilerplate_report
from app.ingest.chunking import MAX_CHUNK_CHARS, Section, answerless_heading, split_sections
from app.ingest.figures import figure_numbers
from app.ingest.questions import extract_keywords, fallback_question
from app.ingest.records import ArticleRecord, AssetRecord, Provenance, Record
from app.ingest.registry import IngestContext, register
from app.ingest.sources.base import SourceMismatch
from app.ingest.tables import glossary_terms
from app.services import kbkind

if TYPE_CHECKING:
    from app.adapters.docparse import ParsedPicture

log = logging.getLogger(__name__)

# Не единица: PDF без единой картинки не хуже разберёт document, поэтому оставляем
# зазор — источнику с точным совпадением формата есть куда встать выше.
PDF_CONFIDENCE = 0.85
_HTML_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)


def page_number(value: int | str | float | None) -> int | None:
    """Страница числом. Сервис разбора отдаёт её из JSON и приносит то строку, то дробь.

    Число обязательно: этот же ключ ложится в `KBArticle.images[].page`, по нему
    оператор ищет страницу в исходной методичке, а строка «7» и число 7 в JSONB
    сравниваются по-разному.
    """
    if value is None:
        return None
    try:
        return int(value)
    except TypeError, ValueError:
        return None


def picture_anchor(picture: ParsedPicture) -> dict[str, Any]:
    """Картинка → ключ привязки: номер рисунка из подписи и страница.

    Номер берём из самой подписи («Рисунок 26 – Выбор сертификата»), потому что в
    тексте стоит ровно та же ссылка «(Рисунок 26)»: авторскую связь не нужно
    угадывать по координатам. Подпись без номера оставляет ключ пустым — такую
    картинку привяжут по соседству или не привяжут вовсе, но выбрасывать её нельзя:
    в инструкциях шаг «нажмите вот тут» состоит из скриншота и двух слов.

    Из подписи с несколькими номерами («Рисунок 27 – продолжение рисунка 26»)
    берём тот, что стоит раньше: подпись начинается с номера самой картинки, а
    остальные — ссылки на соседние. Выбор «наименьшего» отдал бы скриншот 27-го
    шага тексту про 26-й, то есть показал бы человеку не ту кнопку.
    """
    caption = picture.caption or ""
    found = sorted(figure_numbers(caption))

    def position(key: str) -> int:
        # 0* — потому что figure_numbers нормализует номер («Рисунок 07» → рис:7),
        # а в подписи он остался с нулём
        match = re.search(rf"0*{key.split(':')[1]}\b", caption)
        return match.start() if match else len(caption)

    anchor: dict[str, Any] = {
        "figure": min(found, key=position) if found else "",
        "page": page_number(picture.page),
    }
    # Место в документе — для картинки без номера: по заголовку и абзацу перед
    # ней конвейер положит её к тому же куску текста (assets.bind_assets).
    if picture.anchor_heading:
        anchor["heading"] = picture.anchor_heading
    if picture.anchor_text:
        anchor["before"] = picture.anchor_text
    return anchor


@register
class PdfFiguresSource:
    name = "pdf_figures"
    priority = 30

    async def sniff(self, blob: FileBlob) -> float:
        return PDF_CONFIDENCE if blob.suffix == ".pdf" else 0.0

    async def parse(self, blob: FileBlob, ctx: IngestContext) -> AsyncIterator[Record]:
        if ctx.rt is None or getattr(ctx.rt, "docparse", None) is None:
            raise SourceMismatch("сервис разбора PDF не поднят")
        # Локально: app.services.ingest тянет модели и половину сервисов, а импорт
        # модуля источника случается при старте приложения у всех источников сразу.
        from app.ingest.documents import extract_document

        markdown, pictures = await extract_document(
            ctx.rt,
            blob.name,
            blob.data,
            do_ocr=bool(ctx.params.get("do_ocr")),
        )

        # Картинки идут первыми: конвейер сначала кладёт их в хранилище, а потом
        # привязывает ссылки к статьям — обратный порядок заставил бы его ждать.
        for picture in pictures:
            anchor = picture_anchor(picture)
            page = anchor["page"]
            yield AssetRecord(
                data=picture.data,
                # Пустая строка, а не None: подпись едет в ImageRef и дальше в JSON
                # статьи, где её читают как текст.
                caption=picture.caption or "",
                anchor=anchor,
                # authored=True: скриншот сделал автор методички, и показывать его
                # можно как есть — в отличие от текста, картинку мы не пересказываем.
                origin=Provenance(
                    file=blob.name,
                    locator=f"стр. {page}" if page else "",
                    source_name=self.name,
                    authored=True,
                ),
            )

        # Размер куска задают при импорте — тем же параметром, что и у источника
        # document: методичка и её же скан обязаны резаться одинаково, иначе один
        # документ приезжает в базу двумя разными наборами статей.
        max_chars = ctx.params.get("max_chunk_chars") or MAX_CHUNK_CHARS
        # docling обычно сам не выводит колонтитулы в markdown, но номер страницы и
        # шапка, которую он не опознал, проскакивают. Чистка та же, что у document,
        # — по той же причине: один документ режется одинаково любым путём.
        # «<!-- image -->» — пометка docling на месте картинки: сама картинка едет
        # отдельной записью, а пометка в тексте статьи доходила до ответа человеку
        markdown = _HTML_COMMENT.sub("", markdown)
        markdown, removed = boilerplate_report(markdown)
        if removed:
            log.info("Файл %s: убрано строк колонтитулов и номеров страниц — %d", blob.name, len(removed))
        for index, section in enumerate(split_sections(markdown, max_chars), start=1):
            question = fallback_question(section)
            # Та же таблица терминов, что и у document: разбираем на определения,
            # иначе «что такое УПД» не находит ничего — термин лежит в теле.
            terms = glossary_terms(section.heading or question, section.body)
            for term in terms:
                yield ArticleRecord(
                    question=term.question,
                    answer=term.answer,
                    category=ctx.params.get("category") or (section.heading or ""),
                    line=ctx.params.get("line"),
                    audience=ctx.params.get("audience") or "all",
                    keywords=extract_keywords(Section(heading=term.question, body=term.answer)),
                    phrasings=term.phrasings,
                    origin=Provenance(
                        file=blob.name,
                        locator=f"фрагмент {index}, термин",
                        source_name=self.name,
                        authored=False,
                    ),
                )
            yield ArticleRecord(
                question=question,
                answer=section.body,
                # Категорию задают при импорте; без неё сгодится заголовок раздела
                category=ctx.params.get("category") or (section.heading or ""),
                line=ctx.params.get("line"),
                audience=ctx.params.get("audience") or "all",
                keywords=extract_keywords(section),
                # authored=False: абзац писали как кусок инструкции, а не как ответ
                # на вопрос, поэтому дословно отдавать его пользователю нельзя.
                origin=Provenance(
                    file=blob.name,
                    locator=f"фрагмент {index}",
                    source_name=self.name,
                    authored=False,
                    # Подпись к рисунку, оглавление и разобранная таблица терминов
                    # ответом быть не могут; для рубрик и разбора они полезны.
                    kind=(kbkind.REFERENCE if terms or answerless_heading(question) else kbkind.ANSWER),
                ),
            )
