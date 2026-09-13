import { Img } from "remotion";
import { path, prog, type Key } from "../anim";
import dashboard from "../assets/admin-dashboard.webp";
import type { SlideDef, SlideProps } from "../deck/types";
import { C, elevation, radius } from "../theme";
import { BrowserFrame, Stage, TextBlock } from "../ui";

const BX = 760;
const BY = 150;
const BW = 1080;
const K = BW / 1440;
const BH = 900 * K;

// Экскурсия по живому дашборду: центр кадра в CSS-пикселях скриншота и увеличение
const TOUR: Key[] = [
  { t: 30, x: 720, y: 450, size: 1, stop: true },
  { t: 70, x: 838, y: 136, size: 1.45, stop: true },
  { t: 120, x: 838, y: 136, size: 1.45, stop: true },
  { t: 162, x: 580, y: 632, size: 1.75, stop: true },
  { t: 208, x: 580, y: 632, size: 1.75, stop: true },
  { t: 250, x: 1095, y: 632, size: 1.75, stop: true },
  { t: 296, x: 1095, y: 632, size: 1.75, stop: true },
  { t: 330, x: 720, y: 450, size: 1, stop: true },
];
const STOPS: [number, number, string][] = [
  [70, 150, "диалоги, доля автоответов ИИ, средняя оценка, нарушения"],
  [162, 238, "эскалации по линиям поддержки"],
  [250, 326, "выгрузки CSV и JSON, персональные данные маскируются"],
];

function AnalyticsSlide({ f }: SlideProps) {
  const cam = path(TOUR, f);
  const s = cam.size;
  const tx = Math.min(0, Math.max(BW - BW * s, BW / 2 - cam.x * K * s));
  const ty = Math.min(0, Math.max(BH - BH * s, BH / 2 - cam.y * K * s));

  return (
    <Stage>
      <TextBlock num="09" label="Аналитика"
        title="Руководитель видит, где болит"
        body="Дашборд считает диалоги, долю автоответов и эскалации по линиям. Вопросы группируются по смыслу: одна проблема в трёх формулировках остаётся одной темой, а рост к прошлой неделе виден сразу. Отзывы размечаются по тону, негатив собирается в кластеры, модель пишет заключение." />

      <BrowserFrame x={BX} y={BY} w={BW} h={BH} url="localhost:8090/admin">
        <Img src={dashboard} style={{
          position: "absolute", left: 0, top: 0, width: BW, height: BH, transformOrigin: "0 0",
          transform: `translate(${tx}px, ${ty}px) scale(${s})`,
        }} />
      </BrowserFrame>

      {STOPS.map(([a, b, label]) => {
        const t = prog(f, a - 6, a + 6) * (1 - prog(f, b - 8, b));
        if (t <= 0) return null;
        return (
          <div key={label} style={{
            position: "absolute", left: BX + 28, top: BY + 52 + BH - 84, padding: "12px 20px", borderRadius: radius.m,
            background: C.ink, color: C.surface, fontSize: 20, boxShadow: elevation[3],
            opacity: t, transform: `translateY(${(1 - t) * 16}px)`,
          }}>{label}</div>
        );
      })}
    </Stage>
  );
}

export const analytics: SlideDef = {
  id: "analytics",
  title: "Аналитика",
  demo: 340,
  Component: AnalyticsSlide,
  mascot: (f) => ({
    x: 330, y: 1004, size: 210, acc: "magnifier",
    look: [0.9, -0.3],
    think: 0,
    happy: prog(f, 326, 340),
  }),
};
