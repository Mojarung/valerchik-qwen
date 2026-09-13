import { bump, path, prog, type Key } from "../anim";
import type { SlideDef, SlideProps } from "../deck/types";
import { C, elevation, font, radius } from "../theme";
import { Stage, TextBlock } from "../ui";

interface Node { id: string; x: number; y: number; w: number; title: string; sub: string; accent?: boolean }
const H = 104;
const cell = (row: number, i: number) => ({ x: 800 + i * 208, y: 460 + row * 160, w: 188 });

// Состав docker compose (базовый профиль плюс rerank, docs, voice).
// TODO: модель пока не локальная; формулировку про Qwen3.8 27B поправим, когда определимся.
const NODES: Node[] = [
  { id: "fe", x: 800, y: 140, w: 1020, title: "Виджет · расширение · админка · оператор", sub: "React 19 + Vite, nginx" },
  { id: "api", x: 1110, y: 300, w: 400, title: "API ×N", sub: "FastAPI, REST и WebSocket" },
  { id: "pg", ...cell(0, 0), title: "PostgreSQL 18", sub: "диалоги, статьи" },
  { id: "redis", ...cell(0, 1), title: "Redis 8", sub: "pub/sub, кэш" },
  { id: "qdrant", ...cell(0, 2), title: "Qdrant", sub: "векторы, blue/green" },
  { id: "tei", ...cell(0, 3), title: "TEI · BERTA", sub: "эмбеддинги" },
  { id: "worker", ...cell(0, 4), title: "worker", sub: "индекс, ретрейн" },
  { id: "llm", ...cell(1, 0), title: "Qwen3.8 27B", sub: "локально", accent: true },
  { id: "stt", ...cell(1, 1), title: "faster-whisper", sub: "речь → текст" },
  { id: "tts", ...cell(1, 2), title: "Piper", sub: "текст → речь" },
  { id: "docling", ...cell(1, 3), title: "docling", sub: "PDF с картинками" },
  { id: "mcp", ...cell(1, 4), title: "zakupki-mcp", sub: "каталог СТЕ, цены" },
  { id: "rerank", ...cell(2, 0), title: "реранкер", sub: "cross-encoder" },
  { id: "mcpout", ...cell(2, 1), title: "MCP-сервер", sub: "для внешних агентов" },
  { id: "backup", ...cell(2, 2), title: "backup", sub: "по расписанию" },
];
const byId = Object.fromEntries(NODES.map((n) => [n.id, n]));
const top = (id: string) => ({ x: byId[id].x + byId[id].w / 2, y: byId[id].y });

// Валерчик несёт вопрос по сервисам: интерфейс → API → эмбеддинги → векторы → модель → обратно
const VISITS: [string, number][] = [["fe", 10], ["api", 40], ["tei", 76], ["qdrant", 106], ["llm", 146], ["api", 186], ["fe", 218]];
const ROUTE: Key[] = VISITS.flatMap(([id, t], k) => {
  const p = top(id);
  const stay: Key[] = [{ t, ...p, size: 80, stop: true }, { t: t + 8, ...p, size: 80, stop: true }];
  if (k === VISITS.length - 1) return stay.slice(0, 1);
  const [nextId, nextT] = VISITS[k + 1];
  const q = top(nextId);
  return [...stay, { t: (t + 8 + nextT) / 2, x: (p.x + q.x) / 2, y: Math.min(p.y, q.y) - 90, size: 80 }];
});
const F = { scale: 232, live: 262 };

function ArchSlide({ f }: SlideProps) {
  const lit = (id: string) => Math.max(0, ...VISITS.filter(([v]) => v === id).map(([, t]) => bump(f, t + 4, 16)));
  const ghosts = prog(f, F.scale, F.scale + 16);
  const trunkX = 1310;

  return (
    <Stage>
      <TextBlock num="11" label="Архитектура"
        title="Всё крутится на своём железе"
        body="API без состояния масштабируется за nginx, события WebSocket расходятся через Redis. Настройки, промпты и пороги меняются в админке на лету сразу на всех инстансах. Тяжёлое уходит воркерам, индекс переключается blue/green, бэкапы снимаются по расписанию." />

      <svg width="1920" height="1080" style={{ position: "absolute", inset: 0 }}>
        <g stroke={C.outline} strokeWidth="2.5" fill="none">
          <path d={`M${trunkX} 236 V300`} />
          <path d={`M${trunkX} 404 V760`} />
          {[440, 600, 760].map((y) => <path key={y} d={`M894 ${y} H1726`} />)}
          {NODES.filter((n) => n.y >= 460).map((n) => <path key={n.id} d={`M${n.x + n.w / 2} ${n.y - 20} V${n.y}`} />)}
        </g>
      </svg>

      {[2, 1].map((k) => (
        <div key={k} style={{
          position: "absolute", left: byId.api.x + k * 14, top: byId.api.y + k * 12, width: byId.api.w, height: H,
          borderRadius: radius.l, background: C.surfaceHigh, border: `1.5px solid ${C.outline}`, opacity: ghosts,
        }} />
      ))}

      {NODES.map((n) => {
        const l = lit(n.id);
        return (
          <div key={n.id} style={{
            position: "absolute", left: n.x, top: n.y, width: n.w, height: H, borderRadius: radius.l, boxSizing: "border-box",
            background: l > 0.05 ? `color-mix(in oklch, ${C.primaryContainer} ${l * 100}%, ${C.surfaceLow})` : n.accent ? C.primaryContainer : C.surfaceLow,
            border: `1.5px solid ${n.accent || l > 0.3 ? C.primary : C.outline}`, padding: "18px 18px 0",
            boxShadow: l > 0.3 ? elevation[3] : elevation[1], transform: `scale(${1 + 0.04 * l})`,
          }}>
            <div style={{ fontSize: n.w > 300 ? 22 : 19, fontWeight: 700, whiteSpace: "nowrap" }}>
              {n.id === "api" && ghosts > 0.5 ? "API ×3" : n.title}
            </div>
            <div style={{ marginTop: 6, fontSize: 15, color: C.inkSoft, fontFamily: font.mono, whiteSpace: "nowrap" }}>{n.sub}</div>
          </div>
        );
      })}

      {f >= F.live && (
        <span style={{
          position: "absolute", left: byId.api.x + byId.api.w + 30, top: byId.api.y + 30, fontSize: 17, padding: "8px 14px",
          borderRadius: radius.s, background: C.ink, color: C.surface, opacity: prog(f, F.live, F.live + 12),
        }}>настройки на лету на всех инстансах</span>
      )}
    </Stage>
  );
}

export const ARCH_START = { ...top("fe"), size: 80 };

export const arch: SlideDef = {
  id: "arch",
  title: "Архитектура",
  demo: 300,
  Component: ArchSlide,
  mascot: (f) => ({ ...path(ROUTE, f), look: [0.4, 0.6], happy: prog(f, 226, 240) }),
};
