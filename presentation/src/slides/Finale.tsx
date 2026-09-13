import { interpolate } from "remotion";
import { bump, cyc, hop, lerp, prog, std } from "../anim";
import type { SlideDef, SlideProps } from "../deck/types";
import { C, elevation, font, radius } from "../theme";
import { Chip, Stage, TextBlock } from "../ui";

// числа из репозитория: pytest, .github/workflows, docker compose с профилями rerank, docs, voice, scripts/portal_kb.py
const STATS: [number, string][] = [
  [364, "теста backend проходят зелёными"],
  [7, "задач CI на каждый push"],
  [13, "контейнеров в полном профиле"],
  [326, "статей центра поддержки портала готовы к импорту"],
];
// TODO: Qwen3.8 27B — временная формулировка по просьбе команды, сейчас модель не локальная; поправить до показа
const STACK = ["Qwen3.8 27B", "FastAPI", "pydantic-ai", "Qdrant", "BERTA", "BM25 + RRF", "docling", "faster-whisper", "Piper", "Chrome MV3"];

const MASCOT = { x: 1660, y: 1010, size: 320 };
const PETALS = [C.primary, "#f3a3b2", C.gold, "#e46f8a", "#f7c2cc"];

/** цветок из зала: стебель, листок, пять лепестков */
function Flower({ color, size = 58 }: { color: string; size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 40 40" style={{ overflow: "visible", display: "block" }}>
      <path d="M20 20 Q21 30 19 39" stroke="#5f9a57" strokeWidth="2.4" fill="none" strokeLinecap="round" />
      <path d="M20 30 Q27 26 29 30 Q24 33 20 30" fill="#77b36c" />
      {[0, 72, 144, 216, 288].map((a) => (
        <ellipse key={a} cx="20" cy="12.5" rx="5.2" ry="7.5" fill={color} stroke="#fff" strokeWidth="1" transform={`rotate(${a} 20 18)`} />
      ))}
      <circle cx="20" cy="18" r="4" fill={C.gold} stroke="#fff" strokeWidth="1" />
    </svg>
  );
}

// Бросок: откуда из зала летит, куда падает к лапам, когда
const THROWS = Array.from({ length: 16 }, (_, k) => ({
  from: { x: 700 + ((k * 197) % 1200), y: 1160 },
  to: { x: 1500 + ((k * 53) % 300), y: 1000 + (k % 3) * 16 },
  at: 18 + k * 6,
  spin: (k % 2 ? 1 : -1) * (300 + (k * 37) % 240),
  lie: -70 + ((k * 41) % 140),
  color: PETALS[k % PETALS.length],
}));
const FLIGHT = 32;

function FinaleSlide({ f, g }: SlideProps) {
  const thanks = prog(f, 96, 118);
  const c = cyc(g);
  return (
    <Stage>
      <TextBlock num="13" label="Под капотом"
        title="Поднимается одной командой"
        body="docker compose up поднимает весь стек. Отвечает Qwen3.8 27B на своём сервере, ищет BERTA, PDF разбирает docling, голос слушает faster-whisper и озвучивает Piper. Наружу не уходит ничего." />

      <div style={{
        position: "absolute", left: 800, top: 140, width: 1000, display: "grid",
        gridTemplateColumns: "repeat(2, minmax(0, 1fr))", gap: 24,
      }}>
        {STATS.map(([value, label], k) => {
          const t = prog(f, 6 + k * 8, 40 + k * 8);
          const shown = Math.round(interpolate(t, [0, 1], [0, value]));
          return (
            <div key={label} style={{
              background: k === 0 ? C.primaryContainer : C.surfaceLow, borderRadius: radius.xl,
              padding: "30px 34px", boxShadow: elevation[1], opacity: Math.min(1, t * 3),
              transform: `translateY(${(1 - Math.min(1, t * 2)) * 30}px)`,
            }}>
              <div style={{
                fontSize: 96, fontWeight: 800, letterSpacing: "-0.04em", lineHeight: 1,
                color: k === 0 ? C.onPrimaryContainer : C.ink, fontVariantNumeric: "tabular-nums",
              }}>{shown}</div>
              <div style={{ marginTop: 14, fontSize: 24, color: C.inkSoft, lineHeight: 1.35 }}>{label}</div>
            </div>
          );
        })}
      </div>

      <div style={{ position: "absolute", left: 800, top: 664, width: 680, display: "flex", flexWrap: "wrap", gap: 12 }}>
        {STACK.map((name, k) => {
          const t = prog(f, 40 + k * 4, 54 + k * 4);
          return (
            <Chip key={name} tone={k === 0 ? "primary" : "neutral"} style={{ fontFamily: font.mono, fontSize: 19, opacity: t, transform: `scale(${0.85 + 0.15 * t})` }}>
              {name}
            </Chip>
          );
        })}
      </div>

      <div style={{
        position: "absolute", left: 120, top: 800, fontSize: 120, fontWeight: 800, letterSpacing: "-0.045em",
        color: C.primary, opacity: thanks, transform: `translateY(${(1 - thanks) * 40}px)`,
      }}>Спасибо!</div>

      {/* цветы из зала: сначала охапкой, в покое по одному, не накапливаясь */}
      {THROWS.map((t, k) => {
        const u = std(f, t.at, t.at + FLIGHT);
        if (u <= 0) return null;
        const x = lerp(t.from.x, t.to.x, u);
        const y = lerp(t.from.y, t.to.y, u) - hop(u, 520 + (k % 4) * 60);
        const rot = u < 1 ? t.spin * u : t.lie;
        return (
          <div key={k} style={{ position: "absolute", left: x - 29, top: y - 29, transform: `rotate(${rot}deg)`, zIndex: u >= 1 ? 0 : 2 }}>
            <Flower color={t.color} />
          </div>
        );
      })}
      {f >= 150 && [0, 1, 2].map((k) => {
        const ph = (c + k / 3) % 1;
        // в покое летят низко, чтобы не пересекать карточки с цифрами
        const from = { x: 1150 + k * 250, y: 1160 };
        const to = { x: 1560 + k * 70, y: 1010 };
        return (
          <div key={`loop${k}`} style={{
            position: "absolute", left: lerp(from.x, to.x, ph) - 26, top: lerp(from.y, to.y, ph) - hop(ph, 360) - 26,
            transform: `rotate(${ph * 420 * (k % 2 ? 1 : -1)}deg)`, opacity: Math.sin(Math.PI * ph),
          }}>
            <Flower color={PETALS[(k + 1) % PETALS.length]} size={52} />
          </div>
        );
      })}
    </Stage>
  );
}

export const finale: SlideDef = {
  id: "finale",
  title: "Под капотом",
  demo: 170,
  Component: FinaleSlide,
  mascot: (f, g) => {
    // Кланяется, пока летят букеты, и не перестаёт в покое: поклон раз в 45 кадров,
    // а 45 делит длину покоя, поэтому петля покоя склеивается без шва.
    const beat = (((g % 45) + 45) % 45) / 45;
    const start = prog(f, 24, 38);
    const bow = start * bump(beat, 0.5, 0.34);
    // каждый следующий поклон в другую сторону: между поклонами он разворачивается,
    // в самом поклоне смотрит строго в одну сторону; полный цикл 90 кадров = покой
    const turn = Math.cos(Math.PI * (g / 45 - 0.5));
    const facing = Math.sign(turn) * Math.abs(turn) ** 0.3;
    return {
      ...MASCOT, acc: "party", happy: 1, bow, flip: start > 0 ? lerp(1, facing, start) : undefined,
      look: [-0.7, 0.1 + 0.5 * bow],
      squash: 1 - 0.06 * bow,
    };
  },
};
