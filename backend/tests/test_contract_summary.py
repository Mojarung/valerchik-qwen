"""Краткое содержание контракта: отдельная загрузка документа в чат бота.

Внешний сервис разбора (docling/docparse) в тестах не поднят — как и весь
остальной стенд, `client` живёт в lite-режиме без сети. Сценарий с реально
поднятым docparse проверяем фейковым клиентом (тот же приём, что и в
`test_source_pdf_figures.py`); сценарий деградации до markitdown — на
настоящем .docx, а для .pdf с упавшим docparse место самого markitdown
подменяем: PDF, который надёжно проходит через pdfplumber байт-в-байт, нечем
собрать в тесте, а веткование "docparse вернул None -> берём markitdown" эта
подмена проверяет ровно так же, как подмена самого docparse проверяет ветку
"docparse поднят".
"""

import io
import zipfile

PNG_1PX = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000a49444154789c6360000002000100ffff03000006000557bfabd4000000"
    "0049454e44ae426082"
)

FAKE_CONTRACT_MD = """# Общие положения

Настоящий контракт заключён в соответствии с законодательством о закупках.

# Предмет контракта

Поставка серверного оборудования в количестве 5 комплектов для нужд Заказчика.

# Цена контракта

Цена контракта составляет 3 200 000 (три миллиона двести тысяч) рублей, НДС включён.

# Стороны

Заказчик: ГКУ «Тестовый институт». Поставщик: ООО «Ромашка».

# Срок исполнения

Поставка осуществляется в течение 45 календарных дней с момента подписания контракта.

# Ответственность сторон

За нарушение сроков поставки предусмотрена неустойка 0,1% от цены контракта за каждый день просрочки.
"""


class _FakeDocParse:
    """Клиент разбора PDF, подменяющий сеть: тот же приём, что в test_source_pdf_figures.py."""

    def __init__(self, markdown: str | None) -> None:
        self.markdown = markdown
        self.calls: list[str] = []

    async def parse(self, filename: str, raw: bytes, *, do_ocr: bool = False):
        self.calls.append(filename)
        if self.markdown is None:
            return None
        from app.adapters.docparse import ParsedDocument

        return ParsedDocument(markdown=self.markdown, pictures=[], pages=3, headings=[], seconds=1.2)


_CONTENT_TYPES = (
    '<?xml version="1.0"?>'
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.'
    'relationships+xml"/><Default Extension="xml" ContentType="application/xml"/>'
    '<Override PartName="/word/document.xml" ContentType="application/vnd.'
    'openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>'
)
_RELS = (
    '<?xml version="1.0"?>'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/'
    '2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>'
)


def _docx(paragraphs: list[tuple[str, str]]) -> bytes:
    """Настоящий .docx из заголовков и абзацев — как в test_source_document.py."""
    body = "".join(
        f'<w:p><w:pPr><w:pStyle w:val="{style}"/></w:pPr><w:r><w:t>{text}</w:t></w:r></w:p>'
        for style, text in paragraphs
    )
    document = (
        '<?xml version="1.0"?><w:document xmlns:w="http://schemas.openxmlformats.org/'
        f'wordprocessingml/2006/main"><w:body>{body}</w:body></w:document>'
    )
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", _CONTENT_TYPES)
        archive.writestr("_rels/.rels", _RELS)
        archive.writestr("word/document.xml", document)
    return buffer.getvalue()


CONTRACT_DOCX = _docx(
    [
        ("Heading1", "Предмет контракта"),
        ("Normal", "Поставка компьютерной техники в количестве 10 штук для нужд заказчика."),
        ("Heading1", "Цена контракта"),
        ("Normal", "Общая цена контракта составляет 1 500 000 рублей, НДС не облагается."),
        ("Heading1", "Стороны"),
        ("Normal", "Заказчик — ГКУ «Тест». Поставщик — ООО «Ромашка»."),
        ("Heading1", "Срок исполнения"),
        ("Normal", "Поставка осуществляется в течение 30 календарных дней с даты подписания."),
    ]
)


def _new_session(client) -> str:
    return client.post("/api/chat/sessions").json()["session_id"]


def _last_two_messages(client, sid: str) -> tuple[dict, dict]:
    history = client.get(f"/api/chat/sessions/{sid}/messages").json()
    return history[-2], history[-1]


def test_contract_summary_via_docparse(client, monkeypatch):
    """PDF, docling поднят: выжимка приходит через docparse и содержит ключевые пункты."""
    sid = _new_session(client)
    rt = client.app.state.rt
    fake = _FakeDocParse(FAKE_CONTRACT_MD)
    monkeypatch.setattr(rt, "docparse", fake)

    resp = client.post(
        f"/api/chat/sessions/{sid}/document",
        files={"file": ("contract.pdf", b"%PDF-1.7\n%fake contract bytes", "application/pdf")},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["status"] == "ok"
    summary = data["summary"]
    assert summary

    # LLM не настроена в тестовом стенде — выжимка собрана из найденных
    # разделов, поэтому в ней должны звучать их заголовки.
    for expected in ("Предмет контракта", "Цена контракта", "Срок исполнения"):
        assert expected in summary, summary

    # docparse действительно вызывался с этим файлом
    assert fake.calls == ["contract.pdf"]

    # в переписке видна и реплика о вложении, и ответ с выжимкой
    user_msg, bot_msg = _last_two_messages(client, sid)
    assert user_msg["role"] == "user"
    assert user_msg["meta"]["attachment"]["kind"] == "document"
    assert user_msg["meta"]["attachment"]["name"] == "contract.pdf"
    assert bot_msg["role"] == "assistant"
    assert bot_msg["meta"]["kind"] == "contract_summary"
    assert bot_msg["content"] == summary


def test_contract_summary_falls_back_to_markitdown_when_docparse_down(client, monkeypatch):
    """docparse.parse вернул None — выжимка всё равно приходит, через markitdown."""
    sid = _new_session(client)
    rt = client.app.state.rt
    fake = _FakeDocParse(None)
    monkeypatch.setattr(rt, "docparse", fake)
    # Реальный PDF, который проходит pdfplumber байт-в-байт, в тесте не
    # собрать — а веткование "docparse не ответил -> берём markitdown"
    # проверяет именно то, что вызывается этот конвертер, а не то, как он
    # сам справляется с PDF (это забота его собственных тестов).
    monkeypatch.setattr("app.ingest.documents.extract_text_from_file", lambda filename, raw: FAKE_CONTRACT_MD)

    resp = client.post(
        f"/api/chat/sessions/{sid}/document",
        files={"file": ("contract.pdf", b"%PDF-1.7\n%fake contract bytes", "application/pdf")},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["summary"]
    assert "Предмет контракта" in data["summary"]

    # docparse пробовали первым и получили отказ, прежде чем взяли markitdown
    assert fake.calls == ["contract.pdf"]


def test_contract_summary_real_docx_end_to_end(client):
    """.docx без единой подмены: настоящий markitdown, настоящее склеивание разделов.

    В lite-режиме rt.docparse и так None (DOCPARSE_URL пуст), а .docx его и не
    спрашивает — сценарий целиком проходит боевым кодом, LLM выключена, значит
    работает и офлайн-фолбэк на заголовки разделов.
    """
    sid = _new_session(client)
    resp = client.post(
        f"/api/chat/sessions/{sid}/document",
        files={
            "file": (
                "contract.docx",
                CONTRACT_DOCX,
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            )
        },
    )
    assert resp.status_code == 200, resp.text
    summary = resp.json()["summary"]
    for expected in ("Предмет контракта", "Цена контракта", "Стороны", "Срок исполнения"):
        assert expected in summary, summary

    _, bot_msg = _last_two_messages(client, sid)
    assert bot_msg["meta"]["kind"] == "contract_summary"


def test_document_upload_rejects_unsupported_type(client):
    sid = _new_session(client)
    for name, payload in (
        ("virus.exe", b"MZ\x90\x00"),
        ("photo.png", PNG_1PX),
        ("archive.zip", b"PK\x03\x04"),
    ):
        resp = client.post(
            f"/api/chat/sessions/{sid}/document",
            files={"file": (name, payload, "application/octet-stream")},
        )
        assert resp.status_code == 415, name


def test_document_upload_rejects_too_large_file(client):
    sid = _new_session(client)
    from app.routers.chat import MAX_USER_DOCUMENT_BYTES

    big = b"%PDF-1.7\n" + b"x" * MAX_USER_DOCUMENT_BYTES
    resp = client.post(
        f"/api/chat/sessions/{sid}/document",
        files={"file": ("huge.pdf", big, "application/pdf")},
    )
    assert resp.status_code == 413


def test_document_upload_rejects_empty_file(client):
    sid = _new_session(client)
    resp = client.post(
        f"/api/chat/sessions/{sid}/document",
        files={"file": ("empty.pdf", b"", "application/pdf")},
    )
    assert resp.status_code == 422


def test_document_upload_needs_open_dialog(client, monkeypatch):
    """В завершённый диалог документ не принимаем — как и обычное сообщение."""
    sid = _new_session(client)
    rt = client.app.state.rt
    monkeypatch.setattr(rt, "docparse", _FakeDocParse(FAKE_CONTRACT_MD))
    import asyncio

    asyncio.run(rt.chat._update_session(sid, status="resolved"))
    resp = client.post(
        f"/api/chat/sessions/{sid}/document",
        files={"file": ("contract.pdf", b"%PDF-1.7\n%fake", "application/pdf")},
    )
    assert resp.status_code == 409


def test_document_upload_missing_session_is_rejected_before_parsing(client, monkeypatch):
    missing = "0" * 32
    rt = client.app.state.rt
    fake = _FakeDocParse(FAKE_CONTRACT_MD)
    monkeypatch.setattr(rt, "docparse", fake)
    resp = client.post(
        f"/api/chat/sessions/{missing}/document",
        files={"file": ("contract.pdf", b"%PDF-1.7\n%fake", "application/pdf")},
    )
    assert resp.status_code == 404
    assert fake.calls == []
