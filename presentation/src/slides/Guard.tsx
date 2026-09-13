import { bump, path, prog, type Key } from "../anim";
import type { SlideDef, SlideProps } from "../deck/types";
import { C, elevation, font, radius } from "../theme";
import { Chip, Stage, TextBlock } from "../ui";

const CARD = { x: 800, y: 96, w: 1000, h: 888 };
// пример из README: сообщение набрано в английской раскладке
const WRONG = "Rfr pfhtubcnhbhjdfnmcz?";
const RIGHT = "Как зарегистрироваться?";
// обфускации, которые ловит детектор (backend/app/services/guardrails.py)
const TRICKS = ["звёздочки внутри слова", "латиница вместо кириллицы", "цифры вместо букв", "пробелы между буквами", "растянутые буквы", "невидимые символы", "английская раскладка"];
const F = { ask: 8, run: 30, arrive: 62, clean: 64, fixed: 94, back: 118, home: 152, rude: 178, closed: 208, tricks: 236 };

const HOME = { x: 330, y: 1004, size: 230 };
const SPOT = { x: 1370, y: 400, size: 150 };
const PATH: Key[] = [
  { t: F.run, ...HOME },
  { t: (F.run + F.arrive) / 2, x: 900, y: 620, size: 190 },
  { t: F.arrive, ...SPOT, stop: true },
  { t: F.back, ...SPOT, stop: true },
  { t: (F.back + F.home) / 2, x: 900, y: 620, size: 190 },
  { t: F.home, ...HOME },
];

const bubbleUser = {
  position: "absolute", right: 36, background: C.primary, color: "#fff", borderRadius: 26,
  padding: "14px 24px", boxShadow: elevation[1],
} as const;

function GuardSlide({ f }: SlideProps) {
  const ask = prog(f, F.ask, F.ask + 12);
  const melt = prog(f, F.clean, F.fixed);
  const fixed = prog(f, F.fixed, F.fixed + 12);
  const flash = bump(f, F.fixed + 4, 10);
  const rude = prog(f, F.rude, F.rude + 12);
  const closed = prog(f, F.closed, F.closed + 14);

  return (
    <Stage>
      <TextBlock num="06" label="Защита"
        title="Опечатку чинит, хамство останавливает"
        body="Сообщение в английской раскладке Валерчик переводит сам, оригинал остаётся по клику. Мат ловится и под звёздочками, латиницей и растянутыми буквами, после него обращение закрывается. Инъекции в промпт отсекаются, персональные данные в выгрузках маскируются." />

      <div style={{
        position: "absolute", left: CARD.x, top: CARD.y, width: CARD.w, height: CARD.h, borderRadius: radius.xl,
        background: C.surfaceLow, boxShadow: elevation[3], overflow: "hidden", fontSize: 22,
      }}>
        <div style={{ position: "absolute", left: 36, top: 30, display: "flex", gap: 16, alignItems: "baseline" }}>
          <b style={{ fontSize: 25 }}>Валерчик на связи</b>
          <span style={{ fontSize: 19, color: C.inkSoft }}>отвечает мгновенно, 24/7</span>
        </div>
        <div style={{
          position: "absolute", left: 36, top: 84, width: 860, background: C.surface, borderRadius: 26,
          padding: "16px 24px", boxShadow: elevation[1], lineHeight: 1.4,
        }}>
          Здравствуйте! Я умный помощник Портала поставщиков. Задайте вопрос, отвечу по базе знаний,
          а если не смогу, переведу на оператора.
        </div>

        <div style={{
          ...bubbleUser, top: 214, opacity: ask, transform: `translateY(${(1 - ask) * 40}px)`,
          boxShadow: `${elevation[1]}, 0 0 0 ${flash * 12}px oklch(54% 0.21 25 / ${0.3 * flash})`,
          display: "grid",
        }}>
          <span style={{ gridArea: "1 / 1", opacity: 1 - melt, filter: `blur(${melt * 8}px)`, transform: `translateY(${-melt * 10}px)` }}>{WRONG}</span>
          <span style={{ gridArea: "1 / 1", opacity: fixed }}>{RIGHT}</span>
        </div>
        <span style={{
          position: "absolute", right: 36, top: 286, fontSize: 16, color: C.inkSoft, opacity: fixed,
          display: "inline-flex", gap: 6, alignItems: "center",
        }}>
          <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke={C.primary} strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M9.9 3.5 11 7l3.5 1.1L11 9.2 9.9 12.7 8.8 9.2 5.3 8.1 8.8 7z" /></svg>
          раскладка исправлена · оригинал по клику
        </span>

        {f >= F.rude && (
          <div style={{ ...bubbleUser, top: 340, opacity: rude, transform: `translateY(${(1 - rude) * 40}px)`, display: "flex", gap: 8, alignItems: "center" }}>
            Опять ничего не работает,
            <span style={{ width: 118, height: 26, borderRadius: 8, background: "oklch(100% 0 0 / 0.55)", filter: "blur(3px)" }} />
            !
          </div>
        )}

        {f >= F.closed && (
          <div style={{
            position: "absolute", left: 36, right: 36, top: 438, borderRadius: radius.l, padding: "18px 24px",
            background: "oklch(95.5% 0.03 25)", color: C.onPrimaryContainer, fontSize: 20, lineHeight: 1.45,
            opacity: closed, transform: `scale(${0.96 + 0.04 * closed})`,
          }}>
            <b>Обращение завершено:</b> в сообщении обнаружена нецензурная лексика, это нарушает правила общения.
            Вы можете создать новое обращение и задать вопрос корректно.
          </div>
        )}

        <div style={{ position: "absolute", left: 36, right: 36, top: 610, display: "flex", flexWrap: "wrap", gap: 10, alignItems: "center" }}>
          {f >= F.tricks && <span style={{ fontFamily: font.mono, fontSize: 16, color: C.inkSoft, opacity: prog(f, F.tricks, F.tricks + 8) }}>ловит:</span>}
          {TRICKS.map((trick, k) => {
            const t = prog(f, F.tricks + 4 + k * 5, F.tricks + 16 + k * 5);
            return <Chip key={trick} style={{ fontSize: 17, opacity: t, transform: `scale(${0.85 + 0.15 * t})` }}>{trick}</Chip>;
          })}
        </div>

        <div style={{
          position: "absolute", left: 36, right: 36, bottom: 30, height: 68, borderRadius: radius.full,
          background: C.surface, border: `1.5px solid ${C.outline}`, display: "flex", alignItems: "center",
          padding: "0 28px", color: C.inkSoft, opacity: 1 - 0.5 * closed,
        }}>Опишите ваш вопрос…</div>
      </div>
    </Stage>
  );
}

export const guard: SlideDef = {
  id: "guard",
  title: "Защита",
  demo: 320,
  Component: GuardSlide,
  mascot: (f) => ({
    ...path(PATH, f),
    brush: prog(f, F.arrive - 6, F.arrive + 2) * (1 - prog(f, F.fixed + 14, F.back)),
    look: f < F.home ? [0.9, -0.6] : [0.9, -0.2],
    happy: bump(f, F.fixed + 12, 12),
    stern: prog(f, F.closed, F.closed + 10),
  }),
};
