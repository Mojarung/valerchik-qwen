"""Неформализованная методичка: режем по заголовкам, вопрос формулирует LLM.

Записи помечаются `authored=False`: вопрос придумали мы, а не автор документа.
Абзац инструкции писался как часть связного текста, а не как ответ конкретному
человеку, и отдавать его дословно нельзя — флаг несёт это знание дальше по
конвейеру, где решают, цитировать ответ или пересказывать.

Уверенность средняя намеренно. За текст источник берётся почти всегда, но
структурированная выгрузка (таблица «вопрос — ответ», лог обращений) про свой
файл знает больше нарезки по абзацам и обязана перехватывать его у нас.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator

from app.ingest.blob import FileBlob
from app.ingest.boilerplate import boilerplate_report
from app.ingest.chunking import MAX_CHUNK_CHARS, Section, answerless_heading, split_sections
from app.ingest.questions import extract_keywords, fallback_question, generate_question
from app.ingest.records import ArticleRecord, Provenance
from app.ingest.registry import IngestContext, register
from app.ingest.sources.base import SourceMismatch
from app.ingest.tables import glossary_terms
from app.services import kbkind

log = logging.getLogger(__name__)

# Выше порога MIN_CONFIDENCE, но ниже уверенности источников, узнающих свой
# формат по структуре, а не по факту «это читаемый текст».
TEXT_CONFIDENCE = 0.45

# Двоичные снаружи, текстовые внутри: docx и его родня — это zip, и в первых же
# байтах у них \x00, по которому blob.is_binary честно говорит «не текст». Отказ
# по этому признаку означал бы, что методичка в самом ходовом формате не берётся
# никем: markitdown разворачивает её в markdown с заголовками, а нарезка ждёт
# ровно такой вход. Таблиц и картинок в списке нет намеренно — у них свои
# источники, понимающие структуру, а не только текст. PDF здесь запасным
# вариантом: pdf_figures уверен сильнее и заберёт файл первым, а когда сервис
# разбора не поднят и он откажется, текст всё равно достанется нам.
CONVERTIBLE_SUFFIXES = (".docx", ".doc", ".rtf", ".odt", ".pptx", ".epub", ".pdf")

# Потолок одновременных обращений к модели: методичка на сорок страниц даёт под
# сотню кусков, и без ограничителя мы выстрелили бы ими в LLM разом.
MAX_PARALLEL_QUESTIONS = 8


@register
class DocumentSource:
    name = "document"
    priority = 0

    async def sniff(self, blob: FileBlob) -> float:
        if blob.is_binary:
            # Разворачивать архив прямо здесь нельзя: sniff спрашивают у всех
            # источников подряд и в том же цикле событий, где живёт отчёт о
            # прогрессе задачи. Верим расширению, а осечку конвертера ловим в parse.
            return TEXT_CONFIDENCE if blob.suffix in CONVERTIBLE_SUFFIXES else 0.0
        try:
            sections = split_sections(blob.text, MAX_CHUNK_CHARS)
        except Exception:
            log.info("Файл %s не читается как текст", blob.name, exc_info=True)
            return 0.0
        # Пустая нарезка — это не «сложный документ», а нечего разбирать: пусть
        # файл достанется следующему источнику, а не нам с нулём записей.
        return TEXT_CONFIDENCE if sections else 0.0

    async def parse(self, blob: FileBlob, ctx: IngestContext) -> AsyncIterator[ArticleRecord]:
        try:
            # Конвертер синхронный и на сорока страницах работает секунды: в цикле
            # событий воркера это заморозило бы и обновление прогресса, и остальные
            # задачи. Для txt и md представление уже посчитано в sniff, поток пустой.
            text = await asyncio.to_thread(lambda: blob.text)
        except Exception as exc:
            # Отказ, а не падение: файл должен достаться следующему источнику, а в
            # логе остаться причина, а не трассировка «источник упал».
            raise SourceMismatch(f"{blob.name} не разворачивается в текст: {exc}") from exc
        # Колонтитулы — до нарезки: шапка на каждой странице PDF режет абзац
        # посередине фразы и попадает в каждый кусок, сближая их все между собой.
        text, removed = boilerplate_report(text)
        if removed:
            log.info("Файл %s: убрано строк колонтитулов и номеров страниц — %d", blob.name, len(removed))
        sections = split_sections(text, ctx.params.get("max_chunk_chars") or MAX_CHUNK_CHARS)
        questions = await self._questions(sections, ctx)
        category = ctx.params.get("category") or ""
        for index, (section, question) in enumerate(zip(sections, questions, strict=True), start=1):
            # Таблица терминов расходится на статью-определение на каждый термин:
            # целиком она отвечает только на «перечень терминов», а спрашивают
            # «что такое УПД». Сама таблица остаётся справочником, чтобы не
            # соперничать с разобранными определениями.
            terms = glossary_terms(section.heading or question, section.body)
            for term in terms:
                yield ArticleRecord(
                    question=term.question,
                    answer=term.answer,
                    category=category or (section.heading or ""),
                    line=ctx.params.get("line"),
                    audience=ctx.params.get("audience", "all"),
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
                # Рубрика из параметров важнее заголовка: её задал человек, который
                # знает, что грузит; заголовок раздела — всё, что про принадлежность
                # куска сообщает сам документ.
                category=category or (section.heading or ""),
                line=ctx.params.get("line"),
                audience=ctx.params.get("audience", "all"),
                keywords=extract_keywords(section),
                # Номер куска, а не строка файла: после конвертации в markdown
                # исходной нумерации строк уже нет, а вот «фрагмент 7» на защите
                # находится в выдаче разбора за секунду.
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

    async def _questions(self, sections: list[Section], ctx: IngestContext) -> list[str]:
        """Вопросы сразу ко всем кускам.

        Пачкой, а не по одному внутри выдачи: обращение к модели идёт секунды, и
        последовательный проход по сотне кусков превращает импорт одной методички
        в несколько минут ожидания. Семафор держит нагрузку на модель в рамках.
        """
        # rt нет — некому спрашивать; use_llm=False ставят, когда формулировки не
        # нужны (массовая заливка, тесты), и тогда заголовок честнее выдумки.
        if not (ctx.params.get("use_llm", True) and ctx.rt is not None):
            return [fallback_question(section) for section in sections]

        limit = asyncio.Semaphore(MAX_PARALLEL_QUESTIONS)

        runtime = ctx.rt
        if runtime is None:
            raise ValueError("Разбор документа нейросетью требует рантайма приложения")

        async def ask(section: Section) -> str:
            async with limit:
                return await generate_question(runtime.dyn, section)

        return list(await asyncio.gather(*(ask(section) for section in sections)))
