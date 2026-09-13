"""Картинки: сохранение в хранилище и привязка к записям.

Три способа привязки по убыванию надёжности. Явная ссылка (колонка «скриншот»,
путь в JSON) — это то, что написал автор данных, ей верим безусловно. Авторская
нумерация («Рисунок 26») — почти так же надёжно, так работает импорт методичек.
Соседство в документе — догадка, и применяется последней.

Картинка может быть ответом целиком: в выгрузках поддержки на «где эта кнопка»
отвечают скриншотом. Запись с пустым текстом и картинкой — валидная статья, а не
брак; отбраковку по пустому телу снимает `chunking._is_junk(has_images=True)`.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from app.ingest.chunking import _normalized_text
from app.ingest.figures import figure_numbers
from app.ingest.records import ArticleRecord, AssetRecord, ImageRef

if TYPE_CHECKING:
    from app.runtime import AppRuntime

log = logging.getLogger(__name__)

MAX_IMAGES_PER_RECORD = 3
CONTENT_TYPES = {
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "gif": "image/gif",
    "webp": "image/webp",
    "bmp": "image/bmp",
}


def _extension(asset: AssetRecord) -> str:
    ref = str(asset.anchor.get("ref") or "")
    if "." in ref:
        candidate = ref.rsplit(".", 1)[1].lower()
        if candidate in CONTENT_TYPES:
            return candidate
    if asset.data[:8].startswith(b"\x89PNG"):
        return "png"
    if asset.data[:3] == b"\xff\xd8\xff":
        return "jpg"
    return "png"


async def store_assets(rt: AppRuntime, slug: str, assets: list[AssetRecord]) -> list[ImageRef]:
    """Сохранить картинки в хранилище (том данных или S3) и вернуть ссылки."""
    stored: list[ImageRef] = []
    for index, asset in enumerate(assets):
        extension = _extension(asset)
        key = f"kb/{slug}/{index:04d}.{extension}"
        try:
            url = await rt.media.put(key, asset.data, CONTENT_TYPES[extension])
        except Exception:
            log.exception("Картинка %s не сохранена", key)
            continue
        stored.append(
            ImageRef(
                url=url,
                caption=asset.caption,
                page=asset.anchor.get("page"),
                figure=str(asset.anchor.get("figure") or ""),
                # Исходное имя отдельным полем, а не подписью: таблица ссылается на
                # «shot.png», в хранилище картинка лежит под порядковым номером, а
                # подпись у неё к этому моменту уже своя — человеческая. Пока имя
                # пытались положить в caption через «or», оно не доезжало никогда:
                # подпись непустая, и привязка по явной ссылке не находила ничего.
                ref=str(asset.anchor.get("ref") or ""),
                heading=str(asset.anchor.get("heading") or ""),
                before=str(asset.anchor.get("before") or ""),
            )
        )
    return stored


def has_visual_answer(record: ArticleRecord) -> bool:
    """Ответ — картинка: текста нет, изображение есть."""
    return not record.answer.strip() and bool(record.images)


def _index_by_name(images: list[ImageRef]) -> dict[str, ImageRef]:
    """Картинки по исходному имени: и полным путём, и последним сегментом, и подписью.

    Таблица ссылается на «screens/a.png», а папка скриншотов — на «a.png».
    """
    by_name: dict[str, ImageRef] = {}
    for image in images:
        for key in (image.ref, image.ref.rsplit("/", 1)[-1], image.caption):
            if key:
                by_name.setdefault(key, image)
    return by_name


def _explicit(refs: list[str], images: list[ImageRef], by_name: dict[str, ImageRef]) -> list[ImageRef]:
    """Картинки по явным ссылкам источника, без повторов.

    Одна и та же картинка находится и по url, и по полному имени, и по
    последнему сегменту — поэтому склеиваем по url, иначе один скриншот
    уехал бы в статью тремя копиями.
    """
    candidates = [image for image in images if image.url in refs]
    candidates += [by_name[name] for ref in refs for name in (ref, ref.rsplit("/", 1)[-1]) if name in by_name]
    chosen: list[ImageRef] = []
    seen: set[str] = set()
    for image in candidates:
        if image.url not in seen:
            seen.add(image.url)
            chosen.append(image)
    return chosen


def bind_assets(
    records: list[ArticleRecord], images: list[ImageRef], refs: dict[int, list[str]] | None = None
) -> int:
    """Привязать картинки к записям. Возвращает число привязок.

    `refs` — явные ссылки: {id(record): [url или имя файла, ...]}. Их источник знает
    точно, поэтому они перебивают и нумерацию, и соседство.
    """
    if not images:
        return 0
    bound = 0
    by_figure = {image.figure: image for image in images if image.figure}
    by_name = _index_by_name(images)
    for record in records:
        chosen = _explicit((refs or {}).get(id(record)) or [], images, by_name)
        if not chosen:
            wanted = figure_numbers(f"{record.question}\n{record.answer}")
            chosen = [by_figure[number] for number in sorted(wanted) if number in by_figure]
        if chosen:
            record.images = chosen[:MAX_IMAGES_PER_RECORD]
            bound += len(record.images)
    return bound + _bind_by_place(records, images)


def _bind_by_place(records: list[ArticleRecord], images: list[ImageRef]) -> int:
    """Третий способ — соседство: картинка без номера идёт к куску, под чьим
    заголовком и после чьего абзаца она стояла в документе.

    В методичках Портала таких картинок большинство: шаг «нажмите вот тут» — это
    скриншот без подписи. Пока привязка знала только номера, они терялись все.
    Картинки, уже привязанные явно или по номеру, второй раз не раздаём.
    """
    placed = {image.url for record in records for image in record.images}
    bound = 0
    for image in images:
        if image.url in placed or not (image.heading or image.before):
            continue
        target = _record_by_place(records, image)
        if target is None or len(target.images) >= MAX_IMAGES_PER_RECORD:
            continue
        target.images.append(image)
        placed.add(image.url)
        bound += 1
    return bound


def _record_by_place(records: list[ArticleRecord], image: ImageRef) -> ArticleRecord | None:
    """Кусок с тем же заголовком, а среди одноимённых — тот, где есть сам абзац.

    Заголовок в методичке повторяется у нескольких кусков (длинный раздел режется
    на части), поэтому одного заголовка мало; абзац без заголовка — тоже годится.
    """
    heading = _normalized_text(image.heading)
    before = _normalized_text(image.before)[:80]
    named = [
        record
        for record in records
        if heading and heading in (_normalized_text(record.category), _normalized_text(record.question))
    ]
    if before:
        for record in named or records:
            if before in _normalized_text(record.answer):
                return record
    return named[0] if named else None
