"""Иллюстрации из методичек: хранилище, привязка к кускам, отдача клиенту."""

import io

import pytest

from app.adapters.docparse import ParsedPicture
from app.services.ingest import attach_images, figure_numbers, split_sections

DOC = """# Регистрация по электронной подписи

Нажмите кнопку «Зарегистрироваться» (Рисунок 22), после чего откроется страница
регистрации по электронной подписи. Выберите сертификат и подтвердите выбор.

Рисунок 22 - Кнопка регистрации

Выбор сертификата

На странице «Выбор сертификата электронной подписи» необходимо выбрать сертификат
и нажать на кнопку «Выбрать электронную подпись» (Рисунок 26). Проверьте срок
действия сертификата перед подтверждением.
"""

IMAGES = [
    {
        "url": "/api/kb/media/kb/doc/0000.png",
        "caption": "Рисунок 22 - Кнопка регистрации",
        "page": 20,
        "figure": "рис:22",
    },
    {
        "url": "/api/kb/media/kb/doc/0001.png",
        "caption": "Рисунок 26 - Выбор сертификата",
        "page": 21,
        "figure": "рис:26",
    },
    {"url": "/api/kb/media/kb/doc/0002.png", "caption": "", "page": 21, "figure": None},
]


@pytest.mark.parametrize(
    "text,expected",
    [
        ("нажмите (Рисунок 26 (2))", {"рис:26"}),
        ("см. Рис. 3 и рисунок 4", {"рис:3", "рис:4"}),
        ("Таблица 12 — перечень", {"табл:12"}),
        ("в 2024 году подписано 26 контрактов", set()),  # число без слова «рисунок»
    ],
)
def test_figure_numbers(text, expected):
    assert figure_numbers(text) == expected


def test_images_attached_to_referencing_chunks():
    """Связь берём из авторской разметки: «(Рисунок 26)» в тексте и «Рисунок 26 -» в подписи."""
    sections = split_sections(DOC)
    attached = attach_images(sections, IMAGES)
    assert attached >= 2

    by_figure = {img["figure"] for s in sections for img in s.images}
    assert by_figure == {"рис:22", "рис:26"}
    # картинка без номера в подписи никуда не приклеилась: показать «какую-то
    # иллюстрацию» рядом с ответом хуже, чем не показать ничего
    assert all(img["figure"] is not None for s in sections for img in s.images)

    # каждая картинка попала именно в тот кусок, который на неё ссылается
    for section in sections:
        for image in section.images:
            assert image["figure"] in figure_numbers(section.text)


def test_no_images_no_crash():
    sections = split_sections(DOC)
    assert attach_images(sections, []) == 0
    assert all(not s.images for s in sections)


@pytest.mark.asyncio
async def test_local_storage_round_trip(tmp_path):
    from app.adapters.storage import LocalMediaStorage

    storage = LocalMediaStorage(tmp_path)
    url = await storage.put("kb/doc/0001.png", b"\x89PNG\r\n\x1a\n fake", "image/png")
    assert url == "/api/kb/media/kb/doc/0001.png"

    found = await storage.get("kb/doc/0001.png")
    assert found is not None
    data, content_type = found
    assert data.startswith(b"\x89PNG")
    assert content_type == "image/png"

    assert await storage.get("kb/doc/missing.png") is None
    # обход каталога наружу невозможен даже при подделанном ключе
    assert await storage.get("../../etc/passwd") is None

    assert await storage.delete_prefix("kb/doc") >= 1
    assert await storage.get("kb/doc/0001.png") is None


@pytest.mark.asyncio
async def test_store_pictures_extracts_figure_number(tmp_path):
    """Номер рисунка вытаскивается из подписи — по нему потом идёт привязка."""
    from app.adapters.storage import LocalMediaStorage
    from app.services.ingest import store_pictures

    class _Rt:
        media = LocalMediaStorage(tmp_path)

    pictures = [
        ParsedPicture(data=b"png-1", caption="Рисунок 26 - Выбор сертификата", page=21),
        ParsedPicture(data=b"png-2", caption="без номера", page=22),
    ]
    stored = await store_pictures(_Rt(), "instr-abc", pictures)
    assert [s["figure"] for s in stored] == ["рис:26", None]
    assert stored[0]["url"].startswith("/api/kb/media/kb/instr-abc/")


def test_media_endpoint_serves_and_404s(client, admin_headers):
    from app.adapters.storage import MEDIA_URL_PREFIX

    rt = client.app.state.rt
    import asyncio

    key = "kb/test/pic.png"
    asyncio.run(rt.media.put(key, b"\x89PNG\r\n\x1a\n payload", "image/png"))

    resp = client.get(MEDIA_URL_PREFIX + key)
    assert resp.status_code == 200
    assert resp.content.startswith(b"\x89PNG")
    assert "immutable" in resp.headers.get("cache-control", "")

    assert client.get(MEDIA_URL_PREFIX + "kb/test/nope.png").status_code == 404


def test_uploaded_file_is_queued_with_source_in_storage(client, admin_headers):
    """Файл уходит в хранилище, а не в строку БД: разбор идёт в воркере."""
    content = (
        "# Инструкция\n\nПодробный раздел про подписание контракта электронной подписью на портале.\n"
    ).encode()
    resp = client.post(
        "/api/kb/ingest/file",
        headers=admin_headers,
        files={"file": ("instrukciya.md", io.BytesIO(content), "text/markdown")},
        data={"title": "Инструкция", "use_llm": "false"},
    )
    assert resp.status_code == 200, resp.text
    job_id = resp.json()["job_id"]

    job = client.get(f"/api/kb/ingest/jobs/{job_id}", headers=admin_headers).json()
    assert job["status"] == "done", job
    assert job["created_articles"] >= 1


def test_schema_sync_adds_missing_columns(tmp_path):
    """Новая колонка в модели должна доезжать до уже созданной таблицы.

    create_all() существующие таблицы не трогает, поэтому без досинхронизации
    развёрнутый стенд падает на первом же запросе после обновления.
    """
    import sqlalchemy as sa

    from app.db import _add_missing_columns

    engine = sa.create_engine(f"sqlite:///{tmp_path / 'schema.db'}")
    with engine.begin() as conn:
        conn.exec_driver_sql("CREATE TABLE kb_articles (id VARCHAR(32) PRIMARY KEY)")
        _add_missing_columns(conn)
        columns = {c["name"] for c in sa.inspect(conn).get_columns("kb_articles")}
    assert {"images", "question", "answer", "keywords"} <= columns

    # повторный прогон ничего не ломает
    with engine.begin() as conn:
        _add_missing_columns(conn)


@pytest.mark.asyncio
async def test_storage_rejects_absolute_and_escaping_keys(tmp_path):
    """Обход каталога: «..» мало, на Windows абсолютный путь отбрасывает корень."""
    from app.adapters.storage import LocalMediaStorage

    storage = LocalMediaStorage(tmp_path / "media")
    for key in (
        "../../secret.txt",
        "C:/Windows/win.ini",
        r"C:\Windows\win.ini",
        "/etc/passwd",
        "kb/../../escape.png",
    ):
        assert await storage.get(key) is None, key


def test_media_endpoint_rejects_traversal(client):
    """Обход каталога через ссылку на файл.

    Голый «../..» в этот тест не годится: и httpx, и любой браузер схлопывают
    такой путь ещё до отправки, запрос уходит уже на «/etc/passwd» и попадает в
    SPA-обработчик — проверялась бы нормализация клиента, а не наша защита.
    Реальный вектор — кодированные сегменты: они доезжают до приложения как есть
    и раскодируются уже в параметре пути.
    """
    from app.adapters.storage import MEDIA_URL_PREFIX

    for key in (
        "..%2f..%2fetc%2fpasswd",
        "kb%2f..%2f..%2fescape.png",
        "%2e%2e%2f%2e%2e%2fsecret.txt",
        "C:/Windows/win.ini",
    ):
        resp = client.get(MEDIA_URL_PREFIX + key)
        assert resp.status_code in (400, 404), f"{key} -> {resp.status_code}"


def test_spa_does_not_serve_files_outside_dist(client):
    """SPA-обработчик не должен отдавать файлы за пределами собранного фронта."""
    resp = client.get("/../../../backend/app/config.py")
    assert resp.status_code in (200, 404)
    if resp.status_code == 200:
        assert "app_secret" not in resp.text.lower()


def test_ingest_jobs_limit_validated(client, admin_headers):
    """limit=-1 доезжал до SQL как LIMIT -1 и ронял PostgreSQL."""
    assert client.get("/api/kb/ingest/jobs?limit=-1", headers=admin_headers).status_code == 422
    assert client.get("/api/kb/ingest/jobs?limit=0", headers=admin_headers).status_code == 422
    assert client.get("/api/kb/ingest/jobs?limit=101", headers=admin_headers).status_code == 422
    assert client.get("/api/kb/ingest/jobs?limit=5", headers=admin_headers).status_code == 200


def test_job_timestamps_have_timezone(client, admin_headers):
    """SQLite теряет часовой пояс, и браузер счёл бы время местным."""
    jobs = client.get("/api/kb/ingest/jobs", headers=admin_headers).json()
    for job in jobs:
        assert job["created_at"].endswith("+00:00") or "+" in job["created_at"][-6:], job


def test_figures_and_tables_are_not_mixed():
    """Нумерация рисунков и таблиц независима: «Таблица 3» не должна тянуть «Рисунок 3»."""
    from app.services.ingest import Section, attach_images, figure_numbers

    assert figure_numbers("см. Таблица 3") == {"табл:3"}
    assert figure_numbers("см. Рисунок 3") == {"рис:3"}

    sections = [Section(heading=None, body="Перечень приведён в Таблице 3 настоящего документа.")]
    images = [
        {
            "url": "/api/kb/media/kb/d/0.png",
            "caption": "Рисунок 3 - Главная страница",
            "page": 1,
            "figure": "рис:3",
        }
    ]
    assert attach_images(sections, images) == 0
    assert sections[0].images == []


# --- вложения оператора ---

PNG_1PX = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000a49444154789c6360000002000100ffff03000006000557bfabd4000000"
    "0049454e44ae426082"
)


def _escalated_session(client) -> str:
    """Диалог, доведённый до оператора: вложения принимаются только в таком."""
    sid = client.post("/api/chat/sessions").json()["session_id"]
    client.post(
        f"/api/chat/sessions/{sid}/messages", json={"text": "Позовите оператора, как зарегистрироваться?"}
    )
    return sid


def test_operator_sends_image_into_dialog(client, operator_headers):
    sid = _escalated_session(client)
    client.post(f"/api/operator/sessions/{sid}/take", headers=operator_headers)

    resp = client.post(
        f"/api/operator/sessions/{sid}/attachment",
        headers=operator_headers,
        files={"file": ("shot.png", PNG_1PX, "image/png")},
        data={"caption": "Вот эта кнопка"},
    )
    assert resp.status_code == 200, resp.text
    url = resp.json()["url"]

    # сообщение видно в переписке, картинка приехала в meta, а не в текст
    history = client.get(f"/api/chat/sessions/{sid}/messages").json()
    last = history[-1]
    assert last["role"] == "operator"
    assert last["content"] == "Вот эта кнопка"
    attachment = last["meta"]["attachment"]
    assert attachment["kind"] == "image"
    assert attachment["name"] == "shot.png"
    assert attachment["size"] == len(PNG_1PX)

    # и сам файл отдаётся
    media = client.get(url)
    assert media.status_code == 200
    assert media.headers["content-type"].startswith("image/png")
    assert media.content == PNG_1PX


def test_operator_document_is_forced_to_download(client, operator_headers):
    """Документ отдаём вложением с nosniff: иначе чужой файл с нашего домена
    браузер может исполнить как страницу."""
    sid = _escalated_session(client)
    client.post(f"/api/operator/sessions/{sid}/take", headers=operator_headers)
    resp = client.post(
        f"/api/operator/sessions/{sid}/attachment",
        headers=operator_headers,
        files={"file": ("smeta.csv", b"a;b;c\n1;2;3\n", "text/csv")},
    )
    assert resp.json()["url"]
    media = client.get(resp.json()["url"])
    assert media.headers["content-disposition"] == "attachment"
    assert media.headers["x-content-type-options"] == "nosniff"


def test_attachment_format_and_size_guards(client, operator_headers):
    sid = _escalated_session(client)
    client.post(f"/api/operator/sessions/{sid}/take", headers=operator_headers)

    # html и svg исполняются браузером — не принимаем
    for name, payload in (
        ("page.html", b"<script>alert(1)</script>"),
        ("logo.svg", b"<svg xmlns='http://www.w3.org/2000/svg'/>"),
    ):
        resp = client.post(
            f"/api/operator/sessions/{sid}/attachment",
            headers=operator_headers,
            files={"file": (name, payload, "text/plain")},
        )
        assert resp.status_code == 415, name

    assert (
        client.post(
            f"/api/operator/sessions/{sid}/attachment",
            headers=operator_headers,
            files={"file": ("empty.png", b"", "image/png")},
        ).status_code
        == 422
    )

    big = b"x" * (15 * 1024 * 1024 + 1)
    assert (
        client.post(
            f"/api/operator/sessions/{sid}/attachment",
            headers=operator_headers,
            files={"file": ("big.png", big, "image/png")},
        ).status_code
        == 413
    )


def test_attachment_needs_dialog_in_work(client, operator_headers):
    """В диалог, который оператор не ведёт, файл не уходит."""
    sid = client.post("/api/chat/sessions").json()["session_id"]
    resp = client.post(
        f"/api/operator/sessions/{sid}/attachment",
        headers=operator_headers,
        files={"file": ("shot.png", PNG_1PX, "image/png")},
    )
    assert resp.status_code == 403


def test_user_sends_screenshot_to_operator(client, operator_headers):
    """Показать ошибку картинкой быстрее, чем описывать словами."""
    sid = _escalated_session(client)
    client.post(f"/api/operator/sessions/{sid}/take", headers=operator_headers)

    resp = client.post(
        f"/api/chat/sessions/{sid}/attachment", files={"file": ("screen.png", PNG_1PX, "image/png")}
    )
    assert resp.status_code == 200, resp.text

    history = client.get(f"/api/chat/sessions/{sid}/messages").json()
    last = history[-1]
    assert last["role"] == "user"
    # Имя файла в текст не подставляем: у скриншота из буфера это «{GUID}.png»
    assert last["content"] == ""
    assert last["meta"]["attachment"]["kind"] == "image"
    assert client.get(resp.json()["url"]).status_code == 200


def test_user_attachment_only_while_operator_works(client):
    """Открытая загрузка файлов в любой момент — это файлообменник, а не чат."""
    sid = client.post("/api/chat/sessions").json()["session_id"]
    resp = client.post(
        f"/api/chat/sessions/{sid}/attachment", files={"file": ("screen.png", PNG_1PX, "image/png")}
    )
    assert resp.status_code == 409


def test_user_attachment_images_only(client, operator_headers):
    sid = _escalated_session(client)
    client.post(f"/api/operator/sessions/{sid}/take", headers=operator_headers)
    for name in ("smeta.pdf", "archive.zip", "page.html"):
        resp = client.post(
            f"/api/chat/sessions/{sid}/attachment",
            files={"file": (name, b"data-data-data", "application/pdf")},
        )
        assert resp.status_code == 415, name


def test_operator_attachment_without_caption_has_no_filename_text(client, operator_headers):
    """Имя файла не должно попадать в текст сообщения — в диалоге это мусор."""
    sid = _escalated_session(client)
    client.post(f"/api/operator/sessions/{sid}/take", headers=operator_headers)
    sent = client.post(
        f"/api/operator/sessions/{sid}/attachment",
        headers=operator_headers,
        files={"file": ("{234BCA72-C0AF-4055-9DAC-59254046CF2B}.png", PNG_1PX, "image/png")},
    )
    assert sent.status_code == 200, sent.text

    last = client.get(f"/api/chat/sessions/{sid}/messages").json()[-1]
    assert last["content"] == ""
    # фигурные скобки вычищает санитайзер имени — остаётся безопасное имя
    assert last["meta"]["attachment"]["name"].endswith(".png")
    assert "234BCA72" in last["meta"]["attachment"]["name"]

    # но в очереди оператора превью не пустое — видно, что пришёл файл
    queue = client.get("/api/operator/sessions?status=queue", headers=operator_headers).json()
    ticket = next(t for t in queue if t["session_id"] == sid)
    assert ticket["last_message"].startswith("📎")


def test_upload_filename_cannot_escape_its_folder(client, operator_headers):
    r"""Обход каталога через имя файла с обратными слешами.

    На Linux `Path("..\..\kb\doc\0000.png").name` возвращает строку целиком —
    обратный слеш там не разделитель. Ключ уезжал в чужой раздел хранилища и
    перезаписывал иллюстрацию методички, которую видят все пользователи.
    """
    from app.adapters.storage import safe_filename

    assert safe_filename("..\\..\\kb\\doc-x\\shot.png") == "shot.png"
    assert safe_filename("../../etc/passwd") == "passwd"
    assert safe_filename("....//evil.png") == "evil.png"
    assert safe_filename("") == "file"

    sid = _escalated_session(client)
    client.post(f"/api/operator/sessions/{sid}/take", headers=operator_headers)
    resp = client.post(
        f"/api/chat/sessions/{sid}/attachment",
        files={"file": (r"..\..\kb\doc-victim\0000.png", PNG_1PX, "image/png")},
    )
    assert resp.status_code == 200
    # файл лёг в папку своей сессии, а не в раздел методичек
    assert resp.json()["url"].startswith(f"/api/kb/media/chat/{sid}/")
    assert ".." not in resp.json()["url"]


def test_upload_is_rejected_before_touching_disk(client):
    """Отказ должен приходить ДО записи: иначе файл сохранялся даже для
    несуществующего диалога, и загрузка была открыта кому угодно."""
    missing = "0" * 32  # формат правильный, диалога нет
    resp = client.post(
        f"/api/chat/sessions/{missing}/attachment", files={"file": ("shot.png", PNG_1PX, "image/png")}
    )
    assert resp.status_code == 404

    rt = client.app.state.rt
    import asyncio

    assert asyncio.run(rt.media.get(f"chat/{missing}/shot.png")) is None


@pytest.mark.asyncio
async def test_storage_rejects_cross_section_keys(tmp_path):
    """Внутри хранилища разделы — тоже граница: ключ не должен уходить из chat/ в kb/."""
    from app.adapters.storage import LocalMediaStorage

    storage = LocalMediaStorage(tmp_path / "media")
    await storage.put("kb/doc/0000.png", b"original", "image/png")
    for key in ("chat/s1/../../kb/doc/0000.png", r"chat/s1/..\..\kb/doc/0000.png"):
        with pytest.raises(ValueError):
            await storage.put(key, b"defaced", "image/png")
    data, _ = await storage.get("kb/doc/0000.png")
    assert data == b"original"


# --- ссылки на пропавшие рисунки и привязка по соседству ---------------------


def test_dangling_figure_references_are_removed():
    """Картинки нет — ссылки на неё в тексте быть не должно: человек читал бы
    «см. скриншот» в никуда, а модель переносила бы это в ответ. Ссылки на
    приложенные картинки и на таблицы остаются."""
    from app.services.ingest import Section, strip_dangling_figures

    section = Section(
        heading="Подписание",
        body=(
            "Нажмите «Подписать» (Рисунок 3). Выберите сертификат, как на Рисунке 4.\n"
            "Рисунок 3 – Кнопка подписания\n"
            "Сроки указаны в Таблице 2 (см. рис. 3)."
        ),
        images=[{"url": "/api/kb/media/kb/doc/0004.png", "figure": "рис:4", "caption": "Рисунок 4"}],
    )
    removed = strip_dangling_figures(section)
    assert removed == 3
    assert "Рисунок 3" not in section.body and "рис. 3" not in section.body
    assert "Рисунке 4" in section.body  # картинка на месте — ссылка тоже
    assert "Таблице 2" in section.body  # таблицы лежат в тексте разметкой
    assert "Нажмите «Подписать». Выберите" in section.body  # без висящего пробела перед точкой
    assert "Кнопка подписания" not in section.body  # строка-подпись ушла целиком

    # без пропавших рисунков текст не трогаем вовсе
    untouched = Section(
        heading=None, body="Нажмите кнопку, как на Рисунке 4.", images=[{"url": "x", "figure": "рис:4"}]
    )
    assert strip_dangling_figures(untouched) == 0
    assert untouched.body == "Нажмите кнопку, как на Рисунке 4."


def test_unnumbered_pictures_attach_by_anchor():
    """Подписи без номера — большинство в реальных методичках. Сервис разбора
    отдаёт, под каким заголовком и после какого абзаца картинка стояла, и по
    этому кусок находится даже среди одноимённых."""
    filler = " Дальше в методичке идёт подробное описание каждого поля формы и возможных ошибок."
    sections = split_sections(
        f"## Вход\nВведите логин и пароль на форме входа, затем нажмите кнопку «Войти».{filler}\n\n"
        f"## Подписание\nОткройте контракт и нажмите «Подписать» в правом верхнем углу.{filler}\n\n"
        f"## Подписание\nПосле подписания статус сменится на «Подписан» в течение минуты.{filler}\n"
    )
    assert [s.heading for s in sections] == ["Вход", "Подписание", "Подписание"]
    images = [
        {
            "url": "/m/0.png",
            "caption": "",
            "page": 2,
            "figure": None,
            "anchor": {"heading": "Подписание", "before": "Откройте контракт и нажмите «Подписать»"},
        },
        {
            "url": "/m/1.png",
            "caption": "",
            "page": 3,
            "figure": None,
            "anchor": {"heading": "Подписание", "before": "После подписания статус сменится"},
        },
        {
            "url": "/m/2.png",
            "caption": "Скриншот",
            "page": 1,
            "figure": None,
            "anchor": {"heading": "Нет такого раздела", "before": "и абзаца такого нет"},
        },
        {"url": "/m/3.png", "caption": "Без якоря", "page": 1, "figure": None},
    ]
    assert attach_images(sections, images) == 2
    assert [img["url"] for img in sections[1].images] == ["/m/0.png"]
    assert [img["url"] for img in sections[2].images] == ["/m/1.png"]
    assert not sections[0].images


def test_manual_illustration_upload_and_editing(client, admin_headers):
    """Скриншот к статье руками: загрузка, отдача, и правка статьи, которая не
    теряет картинки, пока редактор их не прислал явно."""
    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
    up = client.post(
        "/api/kb/media", headers=admin_headers, files={"file": ("shot.png", io.BytesIO(png), "image/png")}
    )
    assert up.status_code == 200, up.text
    url = up.json()["url"]
    assert url.startswith("/api/kb/media/kb/manual/") and url.endswith(".png")
    served = client.get(url)
    assert served.status_code == 200 and served.headers["content-type"] == "image/png"

    # заголовок запроса не указ: тип определяется по содержимому
    fake = client.post(
        "/api/kb/media",
        headers=admin_headers,
        files={"file": ("shot.png", io.BytesIO(b"<html>"), "image/png")},
    )
    assert fake.status_code == 415

    body = {
        "question": "Где кнопка подписания контракта?",
        "answer": "В карточке контракта, справа сверху.",
        "category": "Проверка картинок",
        "keywords": ["подписание"],
        "line": 1,
        "images": [{"url": url, "caption": "Кнопка «Подписать»", "figure": "рис:1"}],
    }
    article_id = client.post("/api/kb", headers=admin_headers, json=body).json()["id"]

    def images_of() -> list:
        listing = client.get(
            "/api/kb", headers=admin_headers, params={"query": "кнопка подписания", "page": 1}
        ).json()["items"]
        return next(a for a in listing if a["id"] == article_id)["images"]

    assert [i["url"] for i in images_of()] == [url]
    # импорт без поля images (старые выгрузки) картинки не трогает
    without = {k: v for k, v in body.items() if k != "images"}
    client.put(f"/api/kb/{article_id}", headers=admin_headers, json=without)
    assert [i["url"] for i in images_of()] == [url]
    # а редактор с пустым списком убирает их сознательно
    client.put(f"/api/kb/{article_id}", headers=admin_headers, json={**body, "images": []})
    assert images_of() == []
    client.delete(f"/api/kb/{article_id}", headers=admin_headers, params={"purge": "true"})


@pytest.mark.asyncio
async def test_ingest_without_pictures_strips_figure_references(client):
    """Сервис разбора не поднят — картинок нет, и ссылки на них в статьях тоже."""
    from app.services.ingest import IngestRequest, ingest_document

    rt = client.app.state.rt
    result = await ingest_document(
        rt,
        IngestRequest(
            title="Проверка ссылок",
            use_llm=False,
            text=(
                "## Подписание акта\nНажмите «Подписать» (Рисунок 12) и выберите сертификат, "
                "как показано на Рисунке 13.\nРисунок 12 – Кнопка подписания\n"
            ),
        ),
    )
    assert result["created"] == 1
    from sqlalchemy import select

    from app.models import KBArticle

    async with rt.db.session_factory() as db:
        article = (
            await db.execute(select(KBArticle).where(KBArticle.id == result["article_ids"][0]))
        ).scalar_one()
        assert "Рисунок 12" not in article.answer and "Рисунке 13" not in article.answer
        assert "иллюстрации в исходном документе" in article.answer
        assert article.question == "Подписание акта"
        await db.delete(article)
        await db.commit()
