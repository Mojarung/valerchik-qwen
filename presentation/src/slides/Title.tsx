import { Easing, interpolate, spring } from "remotion";
import { bump, cyc, prog } from "../anim";
import type { SlideDef, SlideProps } from "../deck/types";
import { C, font } from "../theme";
import { Stage } from "../ui";

/** «печенька» M3 Expressive: круг с восемью мягкими волнами по краю */
function cookie(cx: number, cy: number, r: number, lobes = 8, amp = 0.07) {
  const pts: string[] = [];
  for (let i = 0; i <= 180; i++) {
    const a = (i / 180) * Math.PI * 2;
    const rr = r * (1 + amp * Math.cos(lobes * a));
    pts.push(`${(cx + Math.cos(a) * rr).toFixed(1)},${(cy + Math.sin(a) * rr).toFixed(1)}`);
  }
  return `M${pts.join("L")}Z`;
}

const SHAPE = cookie(0, 0, 330);

function TitleSlide({ f, g }: SlideProps) {
  const grow = spring({ frame: f - 4, fps: 30, config: { damping: 15, stiffness: 90 } });
  // восемь волн — симметрия 45°: поворот на 45° за цикл покоя выглядит как непрерывное вращение
  const turn = cyc(g) * 45;
  const rise = (d: number) => {
    const t = prog(f, d, d + 24);
    return { opacity: t, transform: `translateY(${(1 - t) * 36}px)` };
  };
  return (
    <Stage>
      <svg width="1920" height="1080" style={{ position: "absolute", inset: 0 }}>
        <g transform={`translate(1430 600) rotate(${turn}) scale(${grow})`}>
          <path d={SHAPE} fill={C.primaryContainer} />
        </g>
        <circle cx="1430" cy="600" r={250 * grow} fill="none" stroke={C.primary} strokeOpacity="0.12" strokeWidth="2" strokeDasharray="4 10" />
      </svg>
      <div style={{ position: "absolute", left: 120, top: 250, width: 900 }}>
        <div style={{
          ...rise(6), fontFamily: font.mono, fontSize: 24, letterSpacing: "0.14em", color: C.primary,
          textTransform: "uppercase",
        }}>Портал поставщиков · zakupki.mos.ru</div>
        <div style={{
          ...rise(12), fontSize: 220, fontWeight: 800, letterSpacing: "-0.045em", lineHeight: 0.95,
          marginTop: 28,
        }}>Валерчик</div>
        <p style={{
          ...rise(20), fontSize: 38, lineHeight: 1.36, color: C.inkSoft, marginTop: 44, width: 760,
          textWrap: "pretty",
        }}>
          ИИ-поддержка, которая отвечает по базе знаний со ссылками на источники и прямо на портале
          показывает, куда нажать.
        </p>
      </div>
    </Stage>
  );
}

export const title: SlideDef = {
  id: "title",
  title: "Валерчик",
  demo: 75,
  Component: TitleSlide,
  mascot: (f) => {
    const fall = interpolate(f, [0, 24], [-260, 860], {
      easing: Easing.in(Easing.quad), extrapolateLeft: "clamp", extrapolateRight: "clamp",
    });
    return {
      x: 1430,
      y: fall,
      size: 440,
      squash: 1 - 0.2 * bump(f, 28, 7) + 0.05 * bump(f, 37, 5),
      // на титуле смотрит прямо в зал
      look: [0, 0.08],
      happy: prog(f, 42, 60),
      wave: prog(f, 36, 48),
      shadow: prog(f, 16, 26),
    };
  },
};
