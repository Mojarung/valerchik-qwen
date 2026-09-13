#!/usr/bin/env python
"""Генератор BPMN-схем проекта: docs/bpmn/*.bpmn + docs/bpmn/*.svg.

Схемы описаны здесь декларативно — сеткой «колонка × дорожка», а раскладка,
координаты фигур и точки изломов стрелок считаются кодом. Так .bpmn (для
Camunda Modeler / bpmn.io) и .svg (для README и презентации) всегда сходятся:
у них общий источник — один и тот же список узлов и переходов.

    uv run python scripts/bpmn.py        # из корня репозитория

Зависимостей нет: только стандартная библиотека.
"""

from __future__ import annotations

import html
import sys
import textwrap
from dataclasses import dataclass, field
from pathlib import Path
from xml.sax.saxutils import quoteattr

OUT = Path(__file__).resolve().parent.parent / "docs" / "bpmn"

# --- геометрия -------------------------------------------------------------
COL_W = 210  # шаг колонки
ROW_H = 110  # шаг ряда внутри дорожки
LANE_PAD = 24  # отступ от границы дорожки до первого ряда
TASK_W, TASK_H = 160, 74
EVENT_D = 36
GW_D = 50
HEADER_W = 30  # ширина полосы с названием дорожки
LEFT = HEADER_W + 20

TASK = "task"
EVENT_START = "start"
EVENT_END = "end"
EVENT_TIMER = "timer"
GATEWAY = "gateway"


@dataclass
class Node:
    key: str
    kind: str
    label: str
    col: float
    lane: int
    row: int = 0
    attached_to: str | None = None  # для граничного события
    doc: str = ""

    @property
    def size(self) -> tuple[int, int]:
        if self.kind == TASK:
            return TASK_W, TASK_H
        if self.kind == GATEWAY:
            return GW_D, GW_D
        return EVENT_D, EVENT_D


@dataclass
class Edge:
    src: str
    dst: str
    label: str = ""
    #  Как вести стрелку: "auto" — сама, "down"/"up" — сначала по вертикали,
    #  "around" — обводом снизу (для возвратов справа налево).
    route: str = "auto"


@dataclass
class Diagram:
    key: str
    title: str
    lanes: list[str]
    nodes: list[Node]
    edges: list[Edge]
    rows_per_lane: list[int] = field(default_factory=list)

    def node(self, key: str) -> Node:
        for n in self.nodes:
            if n.key == key:
                return n
        raise KeyError(key)

    def lane_height(self, lane: int) -> int:
        rows = self.rows_per_lane[lane] if self.rows_per_lane else 1
        return LANE_PAD * 2 + ROW_H * rows - (ROW_H - TASK_H)

    def lane_top(self, lane: int) -> int:
        return sum(self.lane_height(i) for i in range(lane))

    def box(self, node: Node) -> tuple[float, float, int, int]:
        """Левый верхний угол и размер фигуры."""
        w, h = node.size
        cx = LEFT + node.col * COL_W + TASK_W / 2
        cy = self.lane_top(node.lane) + LANE_PAD + node.row * ROW_H + TASK_H / 2
        if node.attached_to:  # граничное событие — на нижней грани
            host = self.node(node.attached_to)
            hx, hy, hw, hh = self.box(host)
            return hx + hw - EVENT_D * 1.4, hy + hh - EVENT_D / 2, w, h
        return cx - w / 2, cy - h / 2, w, h

    @property
    def width(self) -> int:
        right = max(self.box(n)[0] + self.box(n)[2] for n in self.nodes)
        return int(right + 60)

    @property
    def height(self) -> int:
        return sum(self.lane_height(i) for i in range(len(self.lanes)))


# --- маршрутизация стрелок -------------------------------------------------
# Центры ближе этого — на одной линии: стрелка идёт напрямую, без излома
ALIGNED_PX = 4


def waypoints(d: Diagram, e: Edge) -> list[tuple[float, float]]:
    sx, sy, sw, sh = d.box(d.node(e.src))
    tx, ty, tw, th = d.box(d.node(e.dst))
    s_cx, s_cy = sx + sw / 2, sy + sh / 2
    t_cx, t_cy = tx + tw / 2, ty + th / 2

    if e.route == "down" or (e.route == "auto" and abs(t_cx - s_cx) < ALIGNED_PX):
        if abs(t_cx - s_cx) < ALIGNED_PX:  # строго вниз/вверх
            return [(s_cx, sy + sh), (t_cx, ty)] if t_cy > s_cy else [(s_cx, sy), (t_cx, ty + th)]
        # вниз и вбок: изломом, а не по диагонали — диагоналей в BPMN не бывает
        return [(s_cx, sy + sh), (s_cx, t_cy), (tx, t_cy)]

    if e.route == "around":
        # Возврат обводом. Полоса ведётся по нижней кромке дорожки, а не «под
        # фигурами»: иначе линия ушла бы в чужую дорожку или за край схемы.
        y = _lane_floor(d, e)
        return [(s_cx, sy + sh), (s_cx, y), (t_cx, y), (t_cx, ty + th)]

    if t_cx > s_cx:
        start = (sx + sw, s_cy)
        if abs(t_cy - s_cy) < ALIGNED_PX:
            return [start, (tx, t_cy)]
        # уступом: вправо до середины промежутка, потом по вертикали
        mid = (sx + sw + tx) / 2
        if e.route == "late":  # излом вплотную к цели, чтобы не резать чужие фигуры
            mid = tx - 34
        return [start, (mid, s_cy), (mid, t_cy), (tx, t_cy)]

    # справа налево (возврат в цикле)
    y = _lane_floor(d, e)
    return [(s_cx, sy + sh), (s_cx, y), (t_cx, y), (t_cx, ty + th)]


def _lane_floor(d: Diagram, e: Edge) -> float:
    """Высота обводной полосы: нижняя кромка самой глубокой из двух дорожек."""
    lane = max(d.node(e.src).lane, d.node(e.dst).lane)
    return d.lane_top(lane) + d.lane_height(lane) - 12


# --- BPMN 2.0 XML ----------------------------------------------------------
BPMN_NS = (
    'xmlns:bpmn="http://www.omg.org/spec/BPMN/20100524/MODEL" '
    'xmlns:bpmndi="http://www.omg.org/spec/BPMN/20100524/DI" '
    'xmlns:dc="http://www.omg.org/spec/DD/20100524/DC" '
    'xmlns:di="http://www.omg.org/spec/DD/20100524/DI" '
    'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" '
    'targetNamespace="https://tenderhack.ru/valerchik"'
)

ELEMENT = {
    TASK: "task",
    EVENT_START: "startEvent",
    EVENT_END: "endEvent",
    GATEWAY: "exclusiveGateway",
}


def to_bpmn(d: Diagram) -> str:
    pid, cid = f"Process_{d.key}", f"Collab_{d.key}"
    out: list[str] = ['<?xml version="1.0" encoding="UTF-8"?>', f"<bpmn:definitions {BPMN_NS}>"]
    out.append(f'  <bpmn:collaboration id="{cid}">')
    out.append(
        f'    <bpmn:participant id="Participant_{d.key}" name={quoteattr(d.title)} processRef="{pid}" />'
    )
    out.append("  </bpmn:collaboration>")
    out.append(f'  <bpmn:process id="{pid}" isExecutable="false">')

    out += _lane_set(d)

    out += _flow_nodes(d)

    for i, e in enumerate(d.edges):
        name = f" name={quoteattr(e.label)}" if e.label else ""
        out.append(f'    <bpmn:sequenceFlow id="Flow_{i}"{name} sourceRef="{e.src}" targetRef="{e.dst}" />')
    out.append("  </bpmn:process>")

    out += _diagram_interchange(d, cid)
    out.append("</bpmn:definitions>")
    return "\n".join(out) + "\n"


def _lane_set(d: Diagram) -> list[str]:
    """Дорожки процесса и узлы в каждой из них."""
    out: list[str] = []
    out.append(f'    <bpmn:laneSet id="LaneSet_{d.key}">')
    for i, lane in enumerate(d.lanes):
        out.append(f'      <bpmn:lane id="Lane_{i}" name={quoteattr(lane)}>')
        for n in d.nodes:
            if n.lane == i:
                out.append(f"        <bpmn:flowNodeRef>{n.key}</bpmn:flowNodeRef>")
        out.append("      </bpmn:lane>")
    out.append("    </bpmn:laneSet>")
    return out


def _flow_nodes(d: Diagram) -> list[str]:
    """Элементы процесса со ссылками на входящие и исходящие переходы."""
    out: list[str] = []
    incoming: dict[str, list[str]] = {n.key: [] for n in d.nodes}
    outgoing: dict[str, list[str]] = {n.key: [] for n in d.nodes}
    for i, e in enumerate(d.edges):
        outgoing[e.src].append(f"Flow_{i}")
        incoming[e.dst].append(f"Flow_{i}")

    for n in d.nodes:
        refs = "".join(f"<bpmn:incoming>{f}</bpmn:incoming>" for f in incoming[n.key])
        refs += "".join(f"<bpmn:outgoing>{f}</bpmn:outgoing>" for f in outgoing[n.key])
        doc = f"<bpmn:documentation>{html.escape(n.doc)}</bpmn:documentation>" if n.doc else ""
        if n.kind == EVENT_TIMER:
            out.append(
                f'    <bpmn:boundaryEvent id="{n.key}" name={quoteattr(n.label)} '
                f'attachedToRef="{n.attached_to}">{doc}{refs}'
                f'<bpmn:timerEventDefinition id="Timer_{n.key}" /></bpmn:boundaryEvent>'
            )
            continue
        tag = ELEMENT[n.kind]
        out.append(f'    <bpmn:{tag} id="{n.key}" name={quoteattr(n.label)}>{doc}{refs}</bpmn:{tag}>')
    return out


def _diagram_interchange(d: Diagram, cid: str) -> list[str]:
    """DI — координаты фигур и стрелок: без него схему не откроет ни один редактор."""
    out: list[str] = []
    out.append(f'  <bpmndi:BPMNDiagram id="Diagram_{d.key}">')
    out.append(f'    <bpmndi:BPMNPlane id="Plane_{d.key}" bpmnElement="{cid}">')
    out.append(
        f'      <bpmndi:BPMNShape id="Shape_Participant_{d.key}" '
        f'bpmnElement="Participant_{d.key}" isHorizontal="true">'
    )
    out.append(f'        <dc:Bounds x="0" y="0" width="{d.width + HEADER_W}" height="{d.height}" />')
    out.append("      </bpmndi:BPMNShape>")
    for i, _lane in enumerate(d.lanes):
        out.append(f'      <bpmndi:BPMNShape id="Shape_Lane_{i}" bpmnElement="Lane_{i}" isHorizontal="true">')
        out.append(
            f'        <dc:Bounds x="{HEADER_W}" y="{d.lane_top(i)}" '
            f'width="{d.width}" height="{d.lane_height(i)}" />'
        )
        out.append("      </bpmndi:BPMNShape>")
    for n in d.nodes:
        x, y, w, h = d.box(n)
        out.append(f'      <bpmndi:BPMNShape id="Shape_{n.key}" bpmnElement="{n.key}">')
        out.append(f'        <dc:Bounds x="{x:.0f}" y="{y:.0f}" width="{w}" height="{h}" />')
        out.append("      </bpmndi:BPMNShape>")
    for i, e in enumerate(d.edges):
        out.append(f'      <bpmndi:BPMNEdge id="Edge_{i}" bpmnElement="Flow_{i}">')
        for x, y in waypoints(d, e):
            out.append(f'        <di:waypoint x="{x:.0f}" y="{y:.0f}" />')
        out.append("      </bpmndi:BPMNEdge>")
    out.append("    </bpmndi:BPMNPlane>")
    out.append("  </bpmndi:BPMNDiagram>")
    return out


# --- SVG -------------------------------------------------------------------
PALETTE = {
    "line": "#3c4048",
    "lane": "#f7f7fa",
    "lane_alt": "#ffffff",
    "task": "#ffffff",
    "task_line": "#2f6f4f",
    "start": "#2f8f5b",
    "end": "#c2242c",
    "gw": "#f4b73f",
    "ink": "#1f2430",
    "soft": "#6b7280",
}


def wrap(label: str, width: int) -> list[str]:
    return textwrap.wrap(label, width=width) or [""]


def to_svg(d: Diagram) -> str:
    w, h = d.width + HEADER_W, d.height
    s: list[str] = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" width="{w}" height="{h}" '
        f'font-family="Segoe UI, Arial, sans-serif">',
        '<defs><marker id="a" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" '
        'markerHeight="7" orient="auto-start-reverse">'
        f'<path d="M0 0 L10 5 L0 10 z" fill="{PALETTE["line"]}"/></marker></defs>',
        f'<rect width="{w}" height="{h}" fill="#ffffff"/>',
    ]
    s += _svg_lanes(d)

    # переходы — под фигурами, чтобы стрелка не лезла поверх текста
    s += _svg_edges(d)

    for n in d.nodes:
        s += _svg_node(d, n)

    s.append("</svg>")
    return "\n".join(s) + "\n"


def _svg_lanes(d: Diagram) -> list[str]:
    """Дорожки: фон в полоску и полоса с повёрнутым названием."""
    s: list[str] = []
    for i, lane in enumerate(d.lanes):
        top, lh = d.lane_top(i), d.lane_height(i)
        fill = PALETTE["lane"] if i % 2 == 0 else PALETTE["lane_alt"]
        s.append(
            f'<rect x="{HEADER_W}" y="{top}" width="{d.width}" height="{lh}" fill="{fill}" '
            f'stroke="{PALETTE["line"]}" stroke-width="1"/>'
        )
        s.append(
            f'<rect x="0" y="{top}" width="{HEADER_W}" height="{lh}" fill="#eceef3" '
            f'stroke="{PALETTE["line"]}" stroke-width="1"/>'
        )
        s.append(
            f'<text x="{HEADER_W / 2}" y="{top + lh / 2}" transform="rotate(-90 '
            f'{HEADER_W / 2} {top + lh / 2})" text-anchor="middle" font-size="13" '
            f'font-weight="600" fill="{PALETTE["ink"]}">{html.escape(lane)}</text>'
        )
    return s


def _svg_edges(d: Diagram) -> list[str]:
    """Переходы со стрелками и подписями на белой подложке."""
    s: list[str] = []
    for e in d.edges:
        pts = waypoints(d, e)
        path = " ".join(("M" if i == 0 else "L") + f"{x:.0f} {y:.0f}" for i, (x, y) in enumerate(pts))
        s.append(
            f'<path d="{path}" fill="none" stroke="{PALETTE["line"]}" stroke-width="1.6" '
            f'marker-end="url(#a)"/>'
        )
        if e.label:
            mx, my = pts[len(pts) // 2]
            if len(pts) % 2 == 0:
                px, py = pts[len(pts) // 2 - 1]
                mx, my = (mx + px) / 2, (my + py) / 2
            s.append(
                f'<rect x="{mx - len(e.label) * 3.4 - 4}" y="{my - 17}" '
                f'width="{len(e.label) * 6.8 + 8}" height="14" fill="#ffffff" opacity="0.92"/>'
            )
            s.append(
                f'<text x="{mx}" y="{my - 6}" text-anchor="middle" font-size="11" '
                f'fill="{PALETTE["soft"]}">{html.escape(e.label)}</text>'
            )
    return s


def _svg_node(d: Diagram, n: Node) -> list[str]:
    """Фигура узла по его виду — задача, развилка или событие — с подписью."""
    s: list[str] = []
    x, y, nw, nh = d.box(n)
    cx, cy = x + nw / 2, y + nh / 2
    if n.kind == TASK:
        s.append(
            f'<rect x="{x:.0f}" y="{y:.0f}" width="{nw}" height="{nh}" rx="10" '
            f'fill="{PALETTE["task"]}" stroke="{PALETTE["task_line"]}" stroke-width="1.6"/>'
        )
        lines = wrap(n.label, 22)
        top = cy - (len(lines) - 1) * 7
        for i, line in enumerate(lines):
            s.append(
                f'<text x="{cx:.0f}" y="{top + i * 14:.0f}" text-anchor="middle" '
                f'dominant-baseline="middle" font-size="11.5" fill="{PALETTE["ink"]}">'
                f"{html.escape(line)}</text>"
            )
    elif n.kind == GATEWAY:
        s.append(
            f'<path d="M{cx} {y} L{x + nw} {cy} L{cx} {y + nh} L{x} {cy} z" '
            f'fill="{PALETTE["gw"]}" stroke="{PALETTE["line"]}" stroke-width="1.6"/>'
        )
        s.append(
            f'<path d="M{cx - 9} {cy - 9} L{cx + 9} {cy + 9} M{cx + 9} {cy - 9} '
            f'L{cx - 9} {cy + 9}" stroke="{PALETTE["ink"]}" stroke-width="2.4"/>'
        )
        for i, line in enumerate(wrap(n.label, 20)):
            s.append(
                f'<text x="{cx:.0f}" y="{y - 8 - (len(wrap(n.label, 20)) - 1 - i) * 13:.0f}" '
                f'text-anchor="middle" font-size="11" font-weight="600" '
                f'fill="{PALETTE["ink"]}">{html.escape(line)}</text>'
            )
    else:
        colour = PALETTE["start"] if n.kind == EVENT_START else PALETTE["end"]
        width = 2 if n.kind == EVENT_START else 4
        if n.kind == EVENT_TIMER:
            colour, width = PALETTE["ink"], 2
        s.append(
            f'<circle cx="{cx:.0f}" cy="{cy:.0f}" r="{EVENT_D / 2}" fill="#ffffff" '
            f'stroke="{colour}" stroke-width="{width}"/>'
        )
        if n.kind == EVENT_TIMER:
            s.append(
                f'<circle cx="{cx:.0f}" cy="{cy:.0f}" r="{EVENT_D / 2 - 4}" fill="none" '
                f'stroke="{colour}" stroke-width="1.4"/>'
            )
            s.append(
                f'<path d="M{cx} {cy - 7} L{cx} {cy} L{cx + 6} {cy + 3}" fill="none" '
                f'stroke="{colour}" stroke-width="1.8"/>'
            )
        lines = wrap(n.label, 18)
        base = cy + EVENT_D / 2 + 14
        for i, line in enumerate(lines):
            s.append(
                f'<text x="{cx:.0f}" y="{base + i * 13:.0f}" text-anchor="middle" '
                f'font-size="11" fill="{PALETTE["ink"]}">{html.escape(line)}</text>'
            )
    return s


# --- сами схемы ------------------------------------------------------------
def support_flow() -> Diagram:
    """Обращение пользователя: от вопроса до оценки ответа."""
    n = [
        Node(
            "StartQuestion",
            EVENT_START,
            "Пользователь задал вопрос",
            0,
            0,
            doc="Веб-чат, встроенный виджет на стороннем сайте или страница /widget.",
        ),
        Node(
            "CheckAbuse",
            TASK,
            "Проверить сообщение на нецензурную лексику",
            1,
            1,
            doc="Нормализация обфускаций: гомоглифы, разделители, латиница, цензура звёздочками.",
        ),
        Node("GwAbuse", GATEWAY, "Нарушение правил общения?", 2, 1),
        Node("NotifyAbuse", TASK, "Уведомить о нарушении и завершить обращение", 2, 1, row=1),
        Node("EndAbuse", EVENT_END, "Обращение закрыто по правилам общения", 3, 1, row=1),
        Node(
            "Normalize",
            TASK,
            "Восстановить раскладку, учесть раздел портала",
            3,
            1,
            doc="«Rfr pfhtubcnhbhjdfnmcz» → «Как зарегистрироваться». Раздел портала приходит "
            "от виджета и влияет на ранжирование.",
        ),
        Node(
            "SearchKb",
            TASK,
            "Найти ответ в базе знаний: вектор + BM25 + RRF",
            4,
            1,
            doc="Гибридный поиск: BERTA через TEI в Qdrant, лексический BM25, слияние Reciprocal "
            "Rank Fusion, опционально cross-encoder реранкер.",
        ),
        Node("GwConfidence", GATEWAY, "Уверенность выше порога?", 5, 1),
        Node(
            "RecordGap",
            TASK,
            "Зафиксировать вопрос как пробел базы знаний",
            5,
            1,
            row=1,
            doc="Повторы группируются: пробел с частотой одним кликом становится статьёй.",
        ),
        Node("OfferOperator", TASK, "Сообщить, что ответа нет, и предложить оператора", 6, 1, row=1),
        Node("GwAmbiguous", GATEWAY, "Тема определяется однозначно?", 6, 1),
        Node("AskClarify", TASK, "Задать уточняющий вопрос", 7, 1, row=1),
        Node("Answer", TASK, "Ответить с источниками и уверенностью", 7, 1),
        Node(
            "RateAnswer",
            TASK,
            "Оценить ответ",
            8,
            0,
            doc="Палец вверх/вниз в чате и оценка 1–5 при закрытии обращения.",
        ),
        Node("GwHelped", GATEWAY, "Ответ помог?", 9, 1),
        Node("EndAuto", EVENT_END, "Решено без оператора", 10, 0),
        Node(
            "Classify",
            TASK,
            "Определить линию поддержки",
            10,
            1,
            doc="Softmax-регрессия на эмбеддингах, дообучается на статьях и реальных эскалациях.",
        ),
        Node("Enqueue", TASK, "Поставить обращение в очередь линии", 11, 1),
        Node("Assign", TASK, "Назначить свободному сотруднику с минимальной нагрузкой", 12, 1),
        Node("TakeCase", TASK, "Принять назначение", 13, 2),
        Node("AcceptTimeout", EVENT_TIMER, "60 секунд", 13, 2, attached_to="TakeCase"),
        Node("Requeue", TASK, "Вернуть в очередь без сброса времени ожидания", 13, 2, row=1),
        Node("Copilot", TASK, "Сводка, проверки, источники и черновик", 14, 2),
        Node("OperatorReply", TASK, "Ответить или запросить сведения", 15, 2),
        Node("ProposeSolution", TASK, "Предложить решение", 16, 2),
        Node("ConfirmSolution", GATEWAY, "Пользователь подтвердил решение?", 17, 0),
        Node("EndOperator", EVENT_END, "Решение подтверждено", 18, 0),
    ]
    e = [
        Edge("StartQuestion", "CheckAbuse"),
        Edge("CheckAbuse", "GwAbuse"),
        Edge("GwAbuse", "NotifyAbuse", "да", route="down"),
        Edge("NotifyAbuse", "EndAbuse"),
        Edge("GwAbuse", "Normalize", "нет"),
        Edge("Normalize", "SearchKb"),
        Edge("SearchKb", "GwConfidence"),
        Edge("GwConfidence", "RecordGap", "нет", route="down"),
        Edge("RecordGap", "OfferOperator"),
        Edge("GwConfidence", "GwAmbiguous", "да"),
        Edge("GwAmbiguous", "AskClarify", "нет", route="down"),
        Edge("AskClarify", "SearchKb", route="around"),
        Edge("GwAmbiguous", "Answer", "да"),
        Edge("Answer", "RateAnswer"),
        Edge("RateAnswer", "GwHelped"),
        Edge("GwHelped", "EndAuto", "да"),
        Edge("GwHelped", "Classify", "нет"),
        Edge("OfferOperator", "Classify", "нужен оператор", route="late"),
        Edge("Classify", "Enqueue"),
        Edge("Enqueue", "Assign"),
        Edge("Assign", "TakeCase"),
        Edge("AcceptTimeout", "Requeue", route="down"),
        Edge("Requeue", "Assign", route="around"),
        Edge("TakeCase", "Copilot"),
        Edge("Copilot", "OperatorReply"),
        Edge("OperatorReply", "ProposeSolution"),
        Edge("ProposeSolution", "ConfirmSolution"),
        Edge("ConfirmSolution", "EndOperator", "Помогло"),
        Edge("ConfirmSolution", "Copilot", "Не решено", route="around"),
    ]
    return Diagram(
        key="Support",
        title="Обработка обращения в службу поддержки Портала поставщиков",
        lanes=["Пользователь", "Валерчик (ИИ-агент)", "Оператор поддержки"],
        nodes=n,
        edges=e,
        rows_per_lane=[2, 2, 2],
    )


def kb_flow() -> Diagram:
    """Структурирование неформализованной базы знаний."""
    n = [
        Node(
            "StartUpload",
            EVENT_START,
            "Загружена методичка или регламент",
            0,
            0,
            doc="pdf, docx, xlsx, xls, pptx, html, md, txt.",
        ),
        Node("Parse", TASK, "Разобрать документ, извлечь текст и иллюстрации", 1, 1),
        Node("GwTable", GATEWAY, "Это готовая выгрузка «вопрос — ответ»?", 2, 1),
        Node("DirectImport", TASK, "Импортировать пары напрямую", 3, 1, row=1),
        Node("Split", TASK, "Нарезать по заголовкам и абзацам", 3, 1),
        Node(
            "MakeQuestion",
            TASK,
            "Сформулировать вопрос к каждому фрагменту",
            4,
            1,
            doc="LLM формулирует вопрос, который фрагмент закрывает.",
        ),
        Node(
            "LinkImages",
            TASK,
            "Привязать иллюстрации по авторской нумерации",
            5,
            1,
            doc="«нажмите (Рисунок 26 (2))» — связь берётся из текста, а не угадывается.",
        ),
        Node("ClassifyLine", TASK, "Определить линию поддержки и категорию", 6, 1),
        Node(
            "Index",
            TASK,
            "Проиндексировать: blue/green через алиасы Qdrant",
            7,
            1,
            doc="Поиск не прерывается: алиас переключается на готовую коллекцию.",
        ),
        Node("GwPublish", GATEWAY, "Публиковать сразу?", 8, 1),
        Node("Draft", TASK, "Оставить черновиком", 8, 1, row=1),
        Node("Review", TASK, "Проверить и опубликовать", 9, 0),
        Node("EndReady", EVENT_END, "База знаний обновлена без простоя", 10, 1),
    ]
    e = [
        Edge("StartUpload", "Parse"),
        Edge("Parse", "GwTable"),
        Edge("GwTable", "DirectImport", "да", route="down"),
        Edge("DirectImport", "ClassifyLine", route="late"),
        Edge("GwTable", "Split", "нет"),
        Edge("Split", "MakeQuestion"),
        Edge("MakeQuestion", "LinkImages"),
        Edge("LinkImages", "ClassifyLine"),
        Edge("ClassifyLine", "Index"),
        Edge("Index", "GwPublish"),
        Edge("GwPublish", "Draft", "нет", route="down"),
        Edge("Draft", "Review"),
        Edge("Review", "EndReady"),
        Edge("GwPublish", "EndReady", "да"),
    ]
    return Diagram(
        key="Knowledge",
        title="Структурирование неформализованной базы знаний",
        lanes=["Администратор", "Система"],
        nodes=n,
        edges=e,
        rows_per_lane=[1, 2],
    )


def quality_flow() -> Diagram:
    """Оценка специалистов и выявление системных проблем."""
    n = [
        Node("StartPeriod", EVENT_START, "Отчёт по расписанию или по кнопке", 0, 0),
        Node("Collect", TASK, "Собрать отзывы и оценки за период", 1, 1),
        Node("Sentiment", TASK, "Определить тональность комментариев", 2, 1),
        Node(
            "Cluster",
            TASK,
            "Сгруппировать негатив по эмбеддингам",
            3,
            1,
            doc="Одна проблема в трёх формулировках — один кластер, а не три редких жалобы.",
        ),
        Node(
            "Rank",
            TASK,
            "Посчитать рейтинг специалистов, включая ИИ",
            4,
            1,
            doc="Средняя оценка, доля негатива, тональность; ИИ ранжируется наравне с людьми.",
        ),
        Node(
            "Conclusion",
            TASK,
            "Сформировать содержательное заключение",
            5,
            1,
            doc="Системные проблемы, а не список жалоб: шаблон либо генерация через LLM.",
        ),
        Node("GwSystemic", GATEWAY, "Найдены системные проблемы?", 6, 1),
        Node("Actions", TASK, "Пополнить базу знаний и обучение операторов", 7, 0),
        Node("EndReport", EVENT_END, "Отчёт в админ-панели", 8, 1),
    ]
    e = [
        Edge("StartPeriod", "Collect"),
        Edge("Collect", "Sentiment"),
        Edge("Sentiment", "Cluster"),
        Edge("Cluster", "Rank"),
        Edge("Rank", "Conclusion"),
        Edge("Conclusion", "GwSystemic"),
        Edge("GwSystemic", "Actions", "да"),
        Edge("Actions", "EndReport", route="late"),
        Edge("GwSystemic", "EndReport", "нет"),
    ]
    return Diagram(
        key="Quality",
        title="Оценка качества работы специалистов по отзывам",
        lanes=["Администратор", "Система"],
        nodes=n,
        edges=e,
        rows_per_lane=[1, 1],
    )


def main() -> None:
    # Консоль Windows по умолчанию в cp1251: без этого падает не генерация, а печать отчёта.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    OUT.mkdir(parents=True, exist_ok=True)
    for build in (support_flow, kb_flow, quality_flow):
        d = build()
        name = d.key.lower()
        (OUT / f"{name}.bpmn").write_text(to_bpmn(d), encoding="utf-8")
        (OUT / f"{name}.svg").write_text(to_svg(d), encoding="utf-8")
        print(f"{name}: {len(d.nodes)} элементов, {len(d.edges)} переходов → docs/bpmn/{name}.bpmn + .svg")


if __name__ == "__main__":
    main()
