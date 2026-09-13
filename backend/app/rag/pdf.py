"""PDF-инструкция → фрагменты по разделам оглавления, с картинками на своих местах и терминами."""

import hashlib
import io
import re
from collections import Counter
from dataclasses import dataclass, field

import pypdfium2 as pdfium
from PIL import Image

from app.rag.passages import KIND_GLOSSARY, KIND_SECTION, KIND_TERM, Block, Figure, Passage, chunk_blocks

MIN_IMAGE_PT = 70
MIN_IMAGE_PX = 90
MAX_IMAGE_SIDE = 1600
WEBP_QUALITY = 80
LINE_TOLERANCE_PT = 3.0
WORD_GAP_PT = 1.5  # зазор между кусками строки, при котором это разные слова или ячейки
TOC_SCAN_PAGES = 10
TOC_PAGE_MIN_LINES = 3
REPEAT_SHARE = 0.3
REPEAT_MIN_PAGES = 4
LOGO_MIN_PAGES = 4
HEADING_MAX_CHARS = 130
SUBHEADING_MAX_CHARS = 120
MIN_SECTION_CHARS = 60
CAPTION_MAX_CHARS = 220

_TOC = re.compile(r"^(?P<num>(?:\d+\.)*\d+\.?)?\s*(?P<title>\S.*?)\s*[.…]{3,}[\s.…]*(?P<page>\d{1,3})$")
_TOC_TAIL = re.compile(r"^[.…\s]{3,}\d{1,3}$")
_TOC_LEADER = re.compile(r"[.…]{4,}\s*\d{1,3}$")
_NUMBERED = re.compile(r"^(?P<num>\d{1,2}(?:\.\d{1,2}){0,4})\.?\s*(?P<title>[^\d\s.].*)$")
_BULLET = re.compile(r"^[-•●▪◦‣⁃−–—•·-]\s*")
_PRIVATE_USE = re.compile(r"[-]")
_LIST_START = re.compile(r"^(?:• |\d{1,2}[.)]\s|[а-яa-z]\)\s)")
_CAPTION = re.compile(r"^(?:Рисунок|Рис\.)\s*\d+", re.IGNORECASE)
_TABLE = re.compile(r"^(?:[−–-]\s*)?Таблица\s*\d+", re.IGNORECASE)
_GLOSSARY = re.compile(r"термин|сокращени|глоссари", re.IGNORECASE)
_TERM = re.compile(r"^(?:\d{1,3}\.?\s+)?(?P<abbr>[A-ZА-ЯЁ][A-ZА-ЯЁ0-9]{1,9})\s+(?P<def>[A-ZА-ЯЁ].{3,})$")
_NORM = re.compile(r"[^a-zа-яё0-9]+")
_ENDS_CLAUSE = (",", ";", ":", "(")


def _norm(text: str) -> str:
    return _NORM.sub(" ", text.lower().replace("ё", "е")).strip()


def doc_title(filename: str) -> str:
    stem = filename.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    stem = stem.rsplit(".", 1)[0] if "." in stem else stem
    return " ".join(stem.replace("_", " ").split())


def audience_for(title: str) -> str:
    low = title.lower()
    if "заказчик" in low:
        return "customer"
    if any(word in low for word in ("поставщик", "оферт", "yml", "актирован", "доверенност", "мчд")):
        return "supplier"
    return "all"


@dataclass
class _Line:
    text: str
    top: float


@dataclass
class _Toc:
    by_num: dict[str, list[str]] = field(default_factory=dict)
    plain: dict[str, str] = field(default_factory=dict)
    pages: set[int] = field(default_factory=set)

    def __bool__(self) -> bool:
        return bool(self.by_num or self.plain)


@dataclass
class _Section:
    num: str
    title: str
    path: list[str]
    blocks: list[Block] = field(default_factory=list)
    raw: list[str] = field(default_factory=list)


Rect = tuple[float, float, float, float]


def _joined_text(textpage: pdfium.PdfTextPage, rects: list[Rect]) -> str:
    """Текст строки из её прямоугольников.

    Куски без зазора между ними — один отрезок, его текст pdfium берёт целиком и сам
    ставит пробелы (иначе слово, набранное двумя шрифтами, распадалось: «П оле»).
    Между отрезками с зазором — пробел: в таблице термин из соседней ячейки иначе
    прилипал к строке определения («Офертадоговора») и терялся для поиска.
    """
    spans: list[list[float]] = []
    for left, bottom, right, top in sorted(rects, key=lambda r: r[0]):
        last = spans[-1] if spans else None
        if last and left - last[2] <= WORD_GAP_PT:
            last[1], last[2], last[3] = min(last[1], bottom), max(last[2], right), max(last[3], top)
        else:
            spans.append([left, bottom, right, top])
    parts = (
        " ".join(textpage.get_text_bounded(left=left, bottom=bottom, right=right, top=top).split())
        for left, bottom, right, top in spans
    )
    return " ".join(part for part in parts if part)


def _page_lines(page: pdfium.PdfPage) -> list[_Line]:
    """Строки страницы сверху вниз: прямоугольники текста, чей центр внутри строки, —
    одна строка; точки-заполнители оглавления остаются в строке своего пункта."""
    textpage = page.get_textpage()
    try:
        rects = sorted((textpage.get_rect(i) for i in range(textpage.count_rects())), key=lambda r: -r[3])
        rows: list[tuple[list[float], list[Rect]]] = []
        for rect in rects:
            _, bottom, _, top = rect
            center = (bottom + top) / 2
            if rows and rows[-1][0][0] - LINE_TOLERANCE_PT <= center <= rows[-1][0][1] + LINE_TOLERANCE_PT:
                box, members = rows[-1]
                box[0], box[1] = min(box[0], bottom), max(box[1], top)
                members.append(rect)
            else:
                rows.append(([bottom, top], [rect]))
        lines = []
        for (_, top), members in rows:
            text = _joined_text(textpage, members)
            if text:
                lines.append(_Line(text, top))
        return lines
    finally:
        textpage.close()


def _encode(image: Image.Image) -> tuple[bytes, str]:
    if image.mode not in ("RGB", "L"):
        rgba = image.convert("RGBA")
        image = Image.new("RGB", rgba.size, (255, 255, 255))
        image.paste(rgba, mask=rgba.split()[3])
    image.thumbnail((MAX_IMAGE_SIDE, MAX_IMAGE_SIDE))
    digest = hashlib.md5(image.convert("L").resize((24, 24)).tobytes(), usedforsecurity=False).hexdigest()
    buf = io.BytesIO()
    image.convert("RGB").save(buf, "WEBP", quality=WEBP_QUALITY, method=4)
    return buf.getvalue(), digest


def _page_images(page: pdfium.PdfPage, page_no: int) -> list[tuple[float, Figure]]:
    found: list[tuple[float, Figure]] = []
    for obj in page.get_objects(filter=[pdfium.raw.FPDF_PAGEOBJ_IMAGE], max_depth=4):
        try:
            left, bottom, right, top = obj.get_bounds()
            if right - left < MIN_IMAGE_PT or top - bottom < MIN_IMAGE_PT / 2:
                continue
            image = obj.get_bitmap(render=False).to_pil()
            if image.width < MIN_IMAGE_PX or image.height < MIN_IMAGE_PX / 2:
                continue
            data, digest = _encode(image)
        except Exception:  # noqa: BLE001 — битая или экзотическая картинка не должна ронять разбор документа
            continue
        found.append((top, Figure(data=data, digest=digest, page=page_no)))
    return found


def _parse_toc(pages: list[list[_Line]]) -> _Toc:
    toc = _Toc()
    for page_no, lines in enumerate(pages[:TOC_SCAN_PAGES]):
        texts = [line.text for line in lines]
        merged: list[str] = []
        for text in texts:
            if _TOC_TAIL.match(text) and merged:
                merged[-1] = f"{merged[-1]} {text}"
            else:
                merged.append(text)
        entries = [m for m in (_TOC.match(t) for t in merged) if m]
        if len(entries) < TOC_PAGE_MIN_LINES:
            continue
        toc.pages.add(page_no)
        for m in entries:
            title = m["title"].strip()
            if m["num"]:
                toc.by_num.setdefault(m["num"].rstrip("."), []).append(title)
            else:
                toc.plain[_norm(title)] = title
    return toc


def _prefix_of(full: str, part: str) -> bool:
    a, b = _norm(full), _norm(part)
    return bool(b) and a.startswith(b)


def _heading(text: str, toc: _Toc, chapter: str) -> tuple[str, str, str] | None:
    """(номер, заголовок, недостающий хвост заголовка) — или None, если строка не заголовок."""
    if len(text) > HEADING_MAX_CHARS or _TOC_LEADER.search(text):
        return None
    m = _NUMBERED.match(text)
    if m:
        num, title = m["num"], m["title"].strip()
        for full in toc.by_num.get(num, []):
            if _prefix_of(full, title):
                rest = full[len(title) :].strip() if len(_norm(full)) > len(_norm(title)) + 2 else ""
                return num, full, rest
        plausible = (
            len(text) <= SUBHEADING_MAX_CHARS
            and not text.endswith(_ENDS_CLAUSE)
            and title[:1].isupper()
            and not _CAPTION.match(title)
            and not _TABLE.match(title)
        )
        if plausible and "." in num and (not toc or num.split(".")[0] == chapter):
            return num, title, ""
        if plausible and not toc and "." not in num and len(title.split()) >= 2 and not text.endswith("."):  # noqa: PLR2004
            return num, title, ""
        return None
    title = toc.plain.get(_norm(text))
    return ("", title, "") if title else None


def _line_text(item: str) -> str:
    """Строка без служебных глифов шрифта; маркер списка — единый «•»."""
    line = _BULLET.sub("• ", item) if _BULLET.match(item) and len(item) > 2 else item  # noqa: PLR2004
    return _PRIVATE_USE.sub("", line).strip()


def _starts_paragraph(buf: str, line: str) -> bool:
    return bool(
        not buf
        or _LIST_START.match(line)
        or _CAPTION.match(line)
        or _CAPTION.match(buf)
        or _TABLE.match(line)
        or (buf.endswith((".", ":", ";", "!", "?")) and line[:1].isupper())
    )


def _join(buf: str, line: str) -> str:
    """Продолжение абзаца; перенос слова по слогам склеивается."""
    if buf.endswith("-") and len(buf) > 1 and buf[-2].isalpha() and line[:1].islower():
        return buf[:-1] + line
    return f"{buf} {line}"


def _attach_captions(blocks: list[Block]) -> None:
    """Подпись «Рисунок N – …» под картинкой становится её подписью."""
    for index, (_, block) in enumerate(blocks[:-1]):
        following = blocks[index + 1][1]
        if isinstance(block, Figure) and isinstance(following, str) and _CAPTION.match(following):
            block.caption = following[:CAPTION_MAX_CHARS]


def _paragraphs(items: list[Block]) -> list[Block]:
    """Строки страницы → абзацы; картинки остаются между абзацами на своих местах."""
    out: list[Block] = []
    buf, buf_page = "", 0
    for page, item in items:
        if isinstance(item, Figure):
            if buf:
                out.append((buf_page, buf))
                buf = ""
            out.append((page, item))
            continue
        line = _line_text(item)
        if not line:
            continue
        if _starts_paragraph(buf, line):
            if buf:
                out.append((buf_page, buf))
            buf, buf_page = line, page
        else:
            buf = _join(buf, line)
    if buf:
        out.append((buf_page, buf))
    _attach_captions(out)
    return out


def _terms(raw: list[str]) -> dict[str, str]:
    found: dict[str, str] = {}
    for line in raw:
        m = _TERM.match(line.strip())
        if not m or not any(ch.islower() for ch in m["def"]):
            continue
        found.setdefault(m["abbr"], m["def"].strip().rstrip(".;,"))
    return found


def _read(data: bytes) -> tuple[list[list[_Line]], list[list[tuple[float, Figure]]]]:
    pdf = pdfium.PdfDocument(data)
    lines: list[list[_Line]] = []
    images: list[list[tuple[float, Figure]]] = []
    try:
        for page_no in range(len(pdf)):
            page = pdf[page_no]
            try:
                lines.append(_page_lines(page))
                images.append(_page_images(page, page_no))
            finally:
                page.close()
    finally:
        pdf.close()
    return lines, images


def _noise(pages: list[list[_Line]], images: list[list[tuple[float, Figure]]]) -> tuple[set[str], set[str]]:
    """Колонтитулы (строки почти на каждой странице) и логотипы (одна картинка на многих страницах)."""
    per_page = Counter(text for lines in pages for text in {_norm(line.text) for line in lines})
    limit = max(REPEAT_MIN_PAGES, int(len(pages) * REPEAT_SHARE))
    repeated = {text for text, count in per_page.items() if count >= limit}
    logos = Counter(d for page in images for d in {f.digest for _, f in page})
    return repeated, {digest for digest, count in logos.items() if count >= LOGO_MIN_PAGES}


class _Outline:
    """Разделы документа по мере чтения строк: заголовок открывает раздел, остальное идёт в текущий."""

    def __init__(self, toc: _Toc, repeated: set[str]) -> None:
        self.toc = toc
        self.repeated = repeated
        self.sections: list[_Section] = []
        self._stack: list[tuple[str, str]] = []
        self._current: _Section | None = None
        self._chapter = ""
        self._tail = ""

    def figure(self, page_no: int, figure: Figure) -> None:
        if self._current is not None:
            self._current.blocks.append((page_no, figure))

    def line(self, page_no: int, text: str) -> None:
        key = _norm(text)
        if not key or key.isdigit() or key in self.repeated:
            return
        # Заголовок из оглавления, перенесённый на вторую строку: хвост уже в заголовке
        tail, self._tail = self._tail, ""
        if tail and _prefix_of(tail, text):
            return
        head = _heading(text, self.toc, self._chapter)
        if head is not None:
            self._open(*head)
        elif self._current is not None:
            self._current.blocks.append((page_no, text))
            self._current.raw.append(text)

    def _open(self, num: str, title: str, tail: str) -> None:
        self._tail = tail
        if num:
            self._chapter = num.split(".")[0]
            depth = num.count(".") + 1
            while self._stack and (not self._stack[-1][0] or self._stack[-1][0].count(".") + 1 >= depth):
                self._stack.pop()
        else:
            self._stack = []
        self._current = _Section(num=num, title=title, path=[t for _, t in self._stack])
        self.sections.append(self._current)
        self._stack.append((num, f"{num} {title}".strip()))


def _sections(data: bytes) -> tuple[list[_Section], _Toc]:
    pages, images = _read(data)
    toc = _parse_toc(pages)
    repeated, logos = _noise(pages, images)
    outline = _Outline(toc, repeated)
    for page_no, (lines, figures) in enumerate(zip(pages, images, strict=True)):
        if page_no in toc.pages:
            continue
        items: list[tuple[float, int, _Line | Figure]] = [(line.top, 0, line) for line in lines]
        items += [(top, 1, figure) for top, figure in figures if figure.digest not in logos]
        for _, _kind, item in sorted(items, key=lambda it: (-it[0], it[1])):
            if isinstance(item, Figure):
                outline.figure(page_no, item)
            else:
                outline.line(page_no, item.text.strip())
    return outline.sections, toc


def parse_pdf(data: bytes, filename: str) -> list[Passage]:
    title = doc_title(filename)
    audience = audience_for(title)
    sections, _ = _sections(data)
    passages: list[Passage] = []
    terms: dict[str, str] = {}
    for section in sections:
        heading = f"{section.num} {section.title}".strip()
        if _GLOSSARY.search(section.title):
            terms.update(_terms(section.raw))
        blocks = _paragraphs(section.blocks)
        if sum(len(b) for _, b in blocks if isinstance(b, str)) < MIN_SECTION_CHARS:
            continue
        path = " › ".join([*section.path, heading])
        for text, figures, pages in chunk_blocks(blocks):
            passages.append(
                Passage(
                    title=heading,
                    text=text,
                    doc=title,
                    path=path,
                    kind=KIND_GLOSSARY if _GLOSSARY.search(section.title) else KIND_SECTION,
                    audience=audience,
                    pages=pages,
                    figures=figures,
                )
            )
    for abbr, definition in terms.items():
        passages.append(
            Passage(
                title=f"Что такое {abbr}?",
                text=f"{abbr} — {definition}.",
                doc=title,
                path="Термины и сокращения",
                kind=KIND_TERM,
                audience="all",
                extra={"abbr": abbr, "definition": definition},
            )
        )
    return passages
