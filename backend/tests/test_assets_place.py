"""Картинка без номера в подписи находит свой кусок текста по месту в документе.

В методичках Портала шаг «нажмите вот тут» — скриншот без подписи. Пока привязка
знала только номера рисунков, такие картинки терялись все: на стенде из полутора
тысяч статей методичек ни одна не получила скриншота.
"""

from app.ingest.assets import MAX_IMAGES_PER_RECORD, bind_assets
from app.ingest.records import ArticleRecord, ImageRef


def sections() -> list[ArticleRecord]:
    return [
        ArticleRecord(
            question="Подписание контракта",
            answer="Откройте карточку контракта и нажмите «Подписать» (Рисунок 26).",
            category="Подписание контракта",
        ),
        ArticleRecord(
            question="Отзыв подписи",
            answer="Отозвать подпись можно до того, как её поставила вторая сторона.",
            category="Отзыв подписи",
        ),
        ArticleRecord(
            question="Отзыв подписи",
            answer="Позже отзыв оформляется только через обращение в поддержку портала.",
            category="Отзыв подписи",
        ),
    ]


def test_picture_without_number_goes_to_paragraph_under_its_heading():
    records = sections()
    numbered = ImageRef(url="/26.png", figure="рис:26")
    # Раздел «Отзыв подписи» порезан на два куска: картинка стояла после второго абзаца
    unnumbered = ImageRef(url="/x.png", heading="Отзыв подписи", before="Позже отзыв оформляется только")

    assert bind_assets(records, [numbered, unnumbered]) == 2
    assert records[0].images == [numbered]
    assert records[1].images == []
    assert records[2].images == [unnumbered]


def test_heading_alone_is_enough_when_paragraph_is_unknown():
    records = sections()
    image = ImageRef(url="/x.png", heading="Отзыв подписи")

    assert bind_assets(records, [image]) == 1
    assert records[1].images == [image]


def test_numbered_picture_is_not_given_out_twice():
    """Картинка с номером уже нашла свой кусок — по соседству её не дублируем."""
    records = sections()
    image = ImageRef(url="/26.png", figure="рис:26", heading="Отзыв подписи")

    assert bind_assets(records, [image]) == 1
    assert records[0].images == [image]
    assert all(not record.images for record in records[1:])


def test_place_binding_respects_the_limit_and_unknown_places():
    records = sections()
    crowd = [ImageRef(url=f"/{n}.png", heading="Подписание контракта") for n in range(5)]
    stray = ImageRef(url="/stray.png", heading="Раздела с таким названием нет")

    bind_assets(records, [*crowd, stray])

    assert len(records[0].images) == MAX_IMAGES_PER_RECORD
    assert all(stray not in record.images for record in records)
