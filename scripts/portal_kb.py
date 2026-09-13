"""База знаний Портала поставщиков → статьи Валерчика с ролями.

Портал отдаёт свой центр поддержки без авторизации тем же API, что и его
собственная страница /knowledgebase: три раздела — для поставщиков, для
заказчиков и общий, — каждая статья с категорией, услугой и HTML-текстом.
Скрипт забирает все три, убирает дубли (одна статья бывает в двух разделах),
превращает HTML в текст с сохранением списков и картинок и складывает в JSON
формата импорта (/api/kb/import) либо сразу отправляет на стенд.

    uv run --no-project python scripts/portal_kb.py --out portal-kb.json
    uv run --no-project python scripts/portal_kb.py --push http://localhost:8090 \\
        --user admin --password admin12345 --publish

Роль статьи берётся из разделов портала: только «поставщики» → supplier,
только «заказчики» → customer, всё остальное (общий раздел, обе стороны) → all.
Только стандартная библиотека — запускается где угодно.
"""

from __future__ import annotations

import argparse
import html
import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser

PORTAL = "https://zakupki.mos.ru"
API = f"{PORTAL}/newapi/api/KnowledgeBase/GetArticlesBySectionType"
SECTIONS = ("supplier", "customer", "general")
USER_AGENT = "ValerchikKBImporter/1.0 (+https://zakupki.mos.ru) Mozilla/5.0 (compatible)"

# Линии поддержки Валерчика: 1 — общие вопросы, 2 — техническая, 3 — закупочные процедуры
LINE_BY_CATEGORY = {
    "Закупочные процедуры": 3,
    "Работа с контрактами": 3,
    "Работа с СТЕ": 3,
    "Работа с СТЕ и офертами": 3,
    "Личный кабинет": 1,
    "Личный кабинет пользователя": 1,
    "Уполномоченный орган (Региональный Заказчик)": 1,
    "Карточка поставщика": 1,
    "Управление знаниями Портал поставщиков": 1,
}
MAX_ANSWER = 8000
# Короче — не статья, а огрызок разметки: пустой заголовок или ответ-заглушка
MIN_QUESTION_CHARS = 5
MIN_ANSWER_CHARS = 20
MAX_KEYWORDS = 20
BATCH = 200


class _Text(HTMLParser):
    """HTML статьи → текст: абзацы, списки и картинки, без разметки."""

    BLOCK = {"p", "div", "br", "li", "ul", "ol", "h1", "h2", "h3", "h4", "table", "tr"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.images: list[dict] = []
        self._list_depth = 0
        self._ordered: list[bool] = []
        self._counters: list[int] = []
        self._skip = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = dict(attrs)
        if tag in ("script", "style"):
            self._skip += 1
        elif tag == "img":
            src = (a.get("src") or "").strip()
            if src.startswith("//"):
                src = "https:" + src
            elif src.startswith("/"):
                src = PORTAL + src
            if src.startswith("http"):
                self.images.append({"url": src, "caption": (a.get("alt") or "").strip()})
        elif tag in ("ul", "ol"):
            self._ordered.append(tag == "ol")
            self._counters.append(0)
            self.parts.append("\n")
        elif tag == "li":
            if self._counters:
                self._counters[-1] += 1
            marker = f"{self._counters[-1]}." if self._ordered and self._ordered[-1] else "•"
            self.parts.append(f"\n{marker} ")
        elif tag in self.BLOCK:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style"):
            self._skip = max(0, self._skip - 1)
        elif tag in ("ul", "ol"):
            if self._ordered:
                self._ordered.pop()
                self._counters.pop()
            self.parts.append("\n")
        elif tag in self.BLOCK:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._skip:
            self.parts.append(data)

    def text(self) -> str:
        raw = "".join(self.parts)
        raw = raw.replace("\xa0", " ")
        lines = [re.sub(r"[ \t]+", " ", line).strip() for line in raw.split("\n")]
        out: list[str] = []
        for line in lines:
            if line:
                out.append(line)
            elif out and out[-1] != "":
                out.append("")
        return "\n".join(out).strip()


def html_to_text(source: str) -> tuple[str, list[dict]]:
    parser = _Text()
    parser.feed(html.unescape(source) if "&lt;" in source and "<" not in source else source)
    parser.close()
    return parser.text(), parser.images


def fetch(section: str) -> list[dict]:
    url = f"{API}?{urllib.parse.urlencode({'sectionType': section})}"
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
            "Referer": f"{PORTAL}/knowledgebase/main/{section}",
        },
    )
    with urllib.request.urlopen(req, timeout=60) as resp:  # адрес фиксирован, из ввода не собирается
        return json.loads(resp.read().decode("utf-8"))


def audience_of(section_types: dict | None) -> str:
    keys = {k for k in (section_types or {}) if k in ("supplier", "customer")}
    if keys == {"supplier"}:
        return "supplier"
    if keys == {"customer"}:
        return "customer"
    return "all"


def keywords_of(article: dict) -> list[str]:
    words: list[str] = []
    for value in (article.get("serviceName"), article.get("taskName")):
        if value and value.strip():
            words.append(value.strip().lower())
    tags = article.get("tags") or ""
    words.extend(t.strip().lower() for t in re.split(r"[,;]", tags) if t.strip())
    seen: dict[str, None] = {}
    for w in words:
        seen.setdefault(w[:64], None)
    return list(seen)[:MAX_KEYWORDS]


def collect(sections: tuple[str, ...] = SECTIONS) -> list[dict]:
    """Все статьи разделов → элементы импорта без дублей (по staticId)."""
    seen: dict[int, dict] = {}
    for section in sections:
        try:
            payload = fetch(section)
        except (urllib.error.URLError, ValueError) as exc:
            print(f"раздел {section}: не получен ({exc})", file=sys.stderr)
            continue
        for category in payload:
            category_name = (category.get("categoryName") or "Портал поставщиков").strip()
            for service in category.get("articlesByService") or []:
                for article in service.get("articles") or []:
                    static_id = article.get("staticId")
                    if not static_id or static_id in seen:
                        continue
                    question = (article.get("largeName") or "").strip()
                    body_html = article.get("selfServicePortal") or article.get("previewText") or ""
                    answer, images = html_to_text(body_html)
                    if len(question) < MIN_QUESTION_CHARS or len(answer) < MIN_ANSWER_CHARS:
                        continue
                    seen[static_id] = {
                        "question": question[:1000],
                        "answer": answer[:MAX_ANSWER],
                        "category": category_name[:128],
                        "keywords": keywords_of(article),
                        "line": LINE_BY_CATEGORY.get(category_name, 1),
                        "status": "draft",
                        "audience": audience_of(article.get("sectionTypes")),
                        "images": images[:10],
                    }
    return list(seen.values())


def push(base: str, items: list[dict], user: str, password: str) -> int:
    base = base.rstrip("/")
    token = _login(base, user, password)
    imported = 0
    for start in range(0, len(items), BATCH):
        chunk = items[start : start + BATCH]
        body = json.dumps({"items": chunk}, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            f"{base}/api/kb/import",
            data=body,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {token}",
            },
        )
        with urllib.request.urlopen(req, timeout=600) as resp:  # адрес задан оператором
            result = json.loads(resp.read().decode("utf-8"))
        imported += int(result.get("imported", 0))
        print(f"отправлено {min(start + BATCH, len(items))}/{len(items)}", file=sys.stderr)
    return imported


def _login(base: str, user: str, password: str) -> str:
    body = json.dumps({"username": user, "password": password}).encode("utf-8")
    req = urllib.request.Request(
        f"{base}/api/auth/login", data=body, method="POST", headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=30) as resp:  # адрес задан оператором
        return json.loads(resp.read().decode("utf-8"))["token"]


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--out", help="куда сохранить JSON для /api/kb/import")
    parser.add_argument("--push", metavar="URL", help="адрес стенда Валерчика для прямого импорта")
    parser.add_argument("--user", default="admin")
    parser.add_argument("--password", default="admin12345")
    parser.add_argument(
        "--publish",
        action="store_true",
        help="сразу active, а не черновики (по умолчанию статьи публикует человек)",
    )
    parser.add_argument(
        "--sections", default=",".join(SECTIONS), help="какие разделы забирать: supplier,customer,general"
    )
    args = parser.parse_args()
    if not args.out and not args.push:
        parser.error("нужно --out или --push")

    items = collect(tuple(s.strip() for s in args.sections.split(",") if s.strip()))
    if args.publish:
        for item in items:
            item["status"] = "active"
    by_role = {
        role: sum(1 for i in items if i["audience"] == role) for role in ("supplier", "customer", "all")
    }
    print(
        f"статей: {len(items)} (поставщикам {by_role['supplier']}, "
        f"заказчикам {by_role['customer']}, всем {by_role['all']})",
        file=sys.stderr,
    )

    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            json.dump(items, fh, ensure_ascii=False, indent=1)
        print(f"сохранено: {args.out}", file=sys.stderr)
    if args.push:
        imported = push(args.push, items, args.user, args.password)
        print(f"импортировано на {args.push}: {imported}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
