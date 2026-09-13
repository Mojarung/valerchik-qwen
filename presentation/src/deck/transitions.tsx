import type { ReactNode } from "react";
import { bump, lerp, path, smooth, softplus, type Key } from "../anim";
import type { MascotState } from "../mascot/Valerchik";
import { C, elevation } from "../theme";
import type { TransitionDef } from "./types";

const Layer = ({ children, style }: { children: ReactNode; style?: React.CSSProperties }) => (
  <div style={{ position: "absolute", inset: 0, overflow: "hidden", ...style }}>{children}</div>
);

const Scrim = ({ o }: { o: number }) => (
  <div style={{ position: "absolute", inset: 0, background: `oklch(21% 0.01 260 / ${o})` }} />
);

/** эмоции прошлого слайда гаснут за первую пятую перехода, а не выключаются в кадре */
const fade = (p: number) => 1 - smooth(p, 0, 0.22);
function calm(s: MascotState, k: number): MascotState {
  return {
    ...s, think: (s.think ?? 0) * k, talk: (s.talk ?? 0) * k, point: (s.point ?? 0) * k,
    happy: (s.happy ?? 0) * k, wave: (s.wave ?? 0) * k, listen: (s.listen ?? 0) * k,
    stern: (s.stern ?? 0) * k, brush: (s.brush ?? 0) * k, rec: k > 0.5 && s.rec, poof: 0,
  };
}
const key = (t: number, s: { x: number; y: number; size: number }, stop?: boolean): Key => ({ t, x: s.x, y: s.y, size: s.size, stop });
const mix = (a: { x: number; y: number; size: number }, b: { x: number; y: number; size: number }, w: number) => ({
  x: lerp(a.x, b.x, w), y: lerp(a.y, b.y, w), size: lerp(a.size, b.size, w),
});

/** Команда → Чат: Валерчик убегает за правый край и возвращается, новый слайд проявляется за его спиной */
const edgeAt = (p: number) => lerp(2300, 0, smooth(p, 0.3, 0.95));
export const wipeBehind: TransitionDef = {
  frames: 84,
  render: (p, prev, next) => {
    const edge = edgeAt(p);
    return (
      <>
        <Layer>{prev}</Layer>
        <Layer style={{ clipPath: `inset(0 0 0 ${Math.max(0, edge)}px)` }}>{next}</Layer>
        {edge > 0 && edge < 1920 && (
          <div style={{ position: "absolute", top: 0, bottom: 0, left: edge - 2, width: 4, background: "oklch(54% 0.21 25)" }} />
        )}
      </>
    );
  },
  mascot: (p, from, to) => {
    if (p < 0.3) {
      const s = path([key(0, from), key(0.3, { x: 2330, y: 900, size: 300 })], p);
      return { ...calm(from, fade(p)), ...s, look: [0.8, 0] };
    }
    // бежит чуть впереди кромки и мягко тормозит на своём месте, кромка уходит дальше под ним
    const x = to.x + softplus(edgeAt(p) - 70 - to.x, 70);
    const t = smooth(p, 0.3, 0.95);
    return { ...calm(to, 1), x, y: lerp(900, to.y, t), size: lerp(300, to.size, t), look: [-0.9, 0] };
  },
};

/** Чат → Расширение: Валерчик хватает угол нового листа и вытягивает его из правого нижнего угла */
const CORNER: Key[] = [
  { t: 0.05, x: 1920, y: 1080, size: 0 },
  { t: 0.24, x: 1650, y: 890, size: 0 },
  { t: 0.8, x: 0, y: 0, size: 0 },
];
const grabAt = (p: number) => {
  const c = path(CORNER, p);
  return { x: 110 + softplus(c.x - 160, 60), y: 230 + softplus(c.y - 190, 60), size: 190 };
};
export const cornerPull: TransitionDef = {
  frames: 96,
  render: (p, prev, next) => {
    const c = path(CORNER, p);
    const pull = 1 - c.x / 1920;
    return (
      <>
        <Layer>{prev}<Scrim o={0.12 * pull} /></Layer>
        <Layer style={{ transform: `translate(${c.x}px, ${c.y}px)`, borderRadius: 36 * (1 - pull), boxShadow: elevation[5] }}>{next}</Layer>
      </>
    );
  },
  mascot: (p, from, to) => {
    if (p < 0.8) {
      const jump = path([key(0, from), key(0.12, { x: 1050, y: 760, size: 220 }), key(0.24, { x: 1600, y: 930, size: 190 })], p);
      const w = smooth(p, 0.18, 0.3);
      return {
        ...calm(from, fade(p)), ...mix(jump, grabAt(p), w),
        rot: -8 * w, look: [lerp(0.6, -0.9, w), lerp(0.3, -0.8, w)],
      };
    }
    const land = path([key(0.8, grabAt(0.8)), key(0.9, { x: 470, y: 330, size: 150 }), key(1, to)], p);
    const t = smooth(p, 0.8, 0.95);
    return { ...calm(to, 1), ...land, rot: -8 * (1 - t), look: [lerp(-0.9, 0.4, t), lerp(-0.8, 0.6, t)] };
  },
};

/** Расширение → Роль: прыжок вверх, камера уезжает вниз, приземление на новом слайде */
export const jumpPan: TransitionDef = {
  frames: 80,
  render: (p, prev, next) => {
    const q = smooth(p, 0.2, 0.82);
    return (
      <>
        <Layer style={{ transform: `translateY(${-1080 * q}px)` }}>{prev}</Layer>
        <Layer style={{ transform: `translateY(${1080 * (1 - q)}px)` }}>{next}</Layer>
      </>
    );
  },
  mascot: (p, from, to) => {
    const s = path([
      key(0, from),
      key(0.22, { x: 1520, y: 560, size: 170 }),
      key(0.45, { x: 1160, y: 300, size: 240 }),
      key(0.7, { x: 1480, y: 660, size: 240 }),
      key(0.9, to, true),
      key(1, to),
    ], p);
    return {
      ...calm(p < 0.45 ? from : to, p < 0.45 ? fade(p) : 1), ...s,
      squash: 1 - 0.18 * bump(p, 0.92, 0.06) + 0.06 * bump(p, 0.06, 0.06),
      look: [0, lerp(-0.8, 0.6, smooth(p, 0.35, 0.6))], rot: bump(p, 0.45, 0.3) * 10,
    };
  },
};

/** Роль → Сценарии: общий отзум по оси Z, Валерчик делает сальто и переодевается в воздухе */
export const zoomThrough: TransitionDef = {
  frames: 76,
  render: (p, prev, next) => {
    const a = smooth(p, 0, 0.55);
    const b = smooth(p, 0.35, 1);
    return (
      <>
        <Layer style={{ transform: `scale(${1 + 0.14 * a})`, opacity: 1 - a }}>{prev}</Layer>
        <Layer style={{ transform: `scale(${0.86 + 0.14 * b})`, opacity: b }}>{next}</Layer>
      </>
    );
  },
  mascot: (p, from, to) => {
    const s = path([key(0, from), key(0.5, { x: 1030, y: 620, size: 270 }), key(0.94, to, true), key(1, to)], p);
    const late = p >= 0.5;
    return {
      ...calm(late ? to : from, late ? 1 : fade(p)), ...s,
      rot: -360 * smooth(p, 0.12, 0.88), acc: late ? to.acc : from.acc, poof: bump(p, 0.5, 0.12),
      look: [-0.6, -0.4], squash: 1 - 0.14 * bump(p, 0.96, 0.04),
    };
  },
};

/** Сценарии → База знаний: Валерчик тянет новый слайд сверху, как штору */
const blindAt = (p: number) => smooth(p, 0.26, 0.88);
export const blindDown: TransitionDef = {
  frames: 90,
  render: (p, prev, next) => {
    const e = blindAt(p);
    return (
      <>
        <Layer>{prev}<Scrim o={0.1 * e} /></Layer>
        <Layer style={{ transform: `translateY(${-1080 * (1 - e)}px)`, boxShadow: elevation[5] }}>{next}</Layer>
      </>
    );
  },
  mascot: (p, from, to) => {
    const jump = path([key(0, from), key(0.13, { x: 700, y: 430, size: 210 }), key(0.26, { x: 960, y: 250, size: 200 })], p);
    // висит под нижней кромкой шторы, пока та не опустит его на пол
    const hang = 250 + softplus(1080 * blindAt(p) + 190 - 250, 50);
    const t = smooth(p, 0.3, 0.92);
    const ride = { x: lerp(960, to.x, t), y: to.y - softplus(to.y - hang, 50), size: lerp(200, to.size, t) };
    const late = p >= 0.34;
    return {
      ...calm(late ? to : from, late ? 1 : fade(p)), ...mix(jump, ride, smooth(p, 0.2, 0.32)),
      acc: late ? to.acc : from.acc, poof: bump(p, 0.34, 0.07), look: [-0.2, -0.9], rot: 6 * (1 - t),
    };
  },
};

/** База знаний → Защита: Валерчик выталкивает старый слайд влево, новый едет за ним */
const seamAt = (p: number) => 1920 * (1 - smooth(p, 0.28, 0.9));
export const pushAside: TransitionDef = {
  frames: 110,
  render: (p, prev, next) => {
    const seam = seamAt(p);
    return (
      <>
        <Layer style={{ transform: `translateX(${seam - 1920}px)` }}>{prev}</Layer>
        <Layer style={{ transform: `translateX(${seam}px)`, boxShadow: elevation[5] }}>{next}</Layer>
      </>
    );
  },
  mascot: (p, from, to) => {
    if (p < 0.27) {
      const s = path([key(0, from), key(0.27, { x: 2100, y: 1004, size: 230 })], p);
      return { ...calm(from, fade(p)), ...s, look: [0.9, 0] };
    }
    // упирается в кромку старого слайда и толкает его, пока тот не уедет
    const x = to.x + softplus(seamAt(p) + 140 - to.x, 60);
    return { ...calm(to, 1), x, y: to.y, size: lerp(230, to.size, smooth(p, 0.3, 0.9)), look: [-0.9, 0] };
  },
};

/** Защита → Оператор: слайд переворачивается как карточка, Валерчик перепрыгивает через ребро */
export const flipCard: TransitionDef = {
  frames: 90,
  render: (p, prev, next) => {
    const a = smooth(p, 0.12, 0.5);
    const b = smooth(p, 0.5, 0.88);
    return (
      <>
        <div style={{ position: "absolute", inset: 0, background: C.surfaceHigh }} />
        {p < 0.5
          ? <Layer style={{ transform: `perspective(2600px) rotateY(${-90 * a}deg)` }}>{prev}</Layer>
          : <Layer style={{ transform: `perspective(2600px) rotateY(${90 * (1 - b)}deg)` }}>{next}</Layer>}
      </>
    );
  },
  mascot: (p, from, to) => {
    const s = path([key(0, from), key(0.2, { x: 560, y: 820, size: 240 }), key(0.5, { x: 960, y: 330, size: 270 }), key(0.86, to, true), key(1, to)], p);
    const late = p >= 0.5;
    return {
      ...calm(late ? to : from, late ? 1 : fade(p)), ...s, acc: late ? to.acc : from.acc, poof: bump(p, 0.5, 0.1),
      squash: 1 + 0.1 * bump(p, 0.3, 0.12) - 0.16 * bump(p, 0.9, 0.05), look: [0, -0.7],
    };
  },
};

/** Оператор → Самообучение: слайд расходится створками, Валерчик раздвигает их лапами */
export const splitDoors: TransitionDef = {
  frames: 100,
  render: (p, prev, next) => {
    const q = smooth(p, 0.28, 0.86);
    return (
      <>
        <Layer style={{ transform: `scale(${0.96 + 0.04 * q})` }}>{next}</Layer>
        <Layer style={{ clipPath: "inset(0 0 50% 0)", transform: `translateY(${-560 * q}px)`, boxShadow: elevation[3] }}>{prev}</Layer>
        <Layer style={{ clipPath: "inset(50% 0 0 0)", transform: `translateY(${560 * q}px)` }}>{prev}</Layer>
      </>
    );
  },
  mascot: (p, from, to) => {
    const mid = { x: 960, y: 660, size: 240 };
    const s = path([key(0, from), key(0.24, mid, true), key(0.7, mid, true), key(0.95, to, true), key(1, to)], p);
    const late = p >= 0.5;
    return {
      ...calm(late ? to : from, late ? 1 : fade(p)), ...s, acc: late ? to.acc : from.acc, poof: bump(p, 0.5, 0.08),
      point: smooth(p, 0.2, 0.3) * (1 - smooth(p, 0.66, 0.74)), look: [0, -0.8],
    };
  },
};

/** Самообучение → Аналитика: Валерчик бежит по верхней кромке, за ним опускаются полосы нового слайда */
const STRIPES = 8;
const runAt = (p: number) => lerp(1800, 140, smooth(p, 0.24, 0.8));
export const stripeWipe: TransitionDef = {
  frames: 110,
  render: (p, prev, next) => {
    const w = 1920 / STRIPES;
    const h = Array.from({ length: STRIPES }, (_, i) => {
      const t0 = 0.22 + ((1800 - (w * i + w / 2)) / 1660) * 0.56;
      return 1080 * smooth(p, t0 - 0.04, t0 + 0.2);
    });
    let pts = "0px 0px, 1920px 0px";
    for (let i = STRIPES - 1; i >= 0; i--) pts += `, ${w * (i + 1)}px ${h[i]}px, ${w * i}px ${h[i]}px`;
    return (
      <>
        <Layer>{prev}</Layer>
        <Layer style={{ clipPath: `polygon(${pts})` }}>{next}</Layer>
      </>
    );
  },
  mascot: (p, from, to) => {
    if (p < 0.24) {
      const s = path([key(0, from), key(0.12, { x: 1100, y: 440, size: 200 }), key(0.24, { x: 1800, y: 170, size: 170 })], p);
      return { ...calm(from, fade(p)), ...s, look: [0.8, -0.8] };
    }
    if (p < 0.8) return { ...calm(from, 0), x: runAt(p), y: 170, size: 170, look: [-0.9, 0.6] };
    const s = path([key(0.8, { x: 140, y: 170, size: 170 }), key(0.9, { x: 250, y: 620, size: 190 }), key(1, to)], p);
    const late = p >= 0.86;
    return { ...calm(late ? to : from, late ? 1 : 0), ...s, acc: late ? to.acc : from.acc, poof: bump(p, 0.86, 0.06), look: [0.4, 0.6] };
  },
};

/** Аналитика → Голос: новый слайд качается внутрь вокруг левого нижнего угла, Валерчик едет на нём */
const swingAt = (p: number) => -90 * (1 - smooth(p, 0.24, 0.84));
const ride = (p: number) => {
  const a = (swingAt(p) * Math.PI) / 180;
  // точка на слайде в 700 px от левого края: она выходит из-за кадра одновременно с самим слайдом
  return { x: 700 * Math.cos(a) + 470 * Math.sin(a), y: 1080 + 700 * Math.sin(a) - 470 * Math.cos(a) };
};
export const swingIn: TransitionDef = {
  frames: 110,
  render: (p, prev, next) => {
    const angle = swingAt(p);
    return (
      <>
        <Layer>{prev}<Scrim o={0.12 * (1 + angle / 90)} /></Layer>
        <Layer style={{ transform: `rotate(${angle}deg)`, transformOrigin: "0 100%", boxShadow: elevation[5] }}>{next}</Layer>
      </>
    );
  },
  mascot: (p, from, to) => {
    if (p < 0.22) {
      const s = path([key(0, from), key(0.22, { x: -300, y: 1004, size: 210 })], p);
      return { ...calm(from, fade(p)), ...s, look: [-0.9, 0] };
    }
    const late = p >= 0.5;
    const base = { ...calm(late ? to : from, late ? 1 : 0), acc: late ? to.acc : from.acc, poof: bump(p, 0.5, 0.08) };
    if (p < 0.84) {
      const r = ride(p);
      return { ...base, x: r.x, y: r.y, size: 205, rot: swingAt(p), look: [0.8, -0.4] };
    }
    const s = path([key(0.84, { ...ride(0.84), size: 205 }), key(0.92, { x: 250, y: 820, size: 215 }), key(1, to)], p);
    return { ...base, ...s, look: [0.6, 0.4] };
  },
};

/** Голос → Архитектура: красный бейдж Валерчика разрастается на весь кадр, из него проступает схема */
const BLOOM = { x: 374, y: 969 };
export const badgeBloom: TransitionDef = {
  frames: 100,
  render: (p, prev, next) => {
    const r1 = smooth(p, 0.18, 0.52) * 2100;
    const r2 = smooth(p, 0.5, 0.9) * 2100;
    return (
      <>
        <Layer>{prev}</Layer>
        <div style={{ position: "absolute", inset: 0, background: C.primary, clipPath: `circle(${r1}px at ${BLOOM.x}px ${BLOOM.y}px)` }} />
        <Layer style={{ clipPath: `circle(${r2}px at ${BLOOM.x}px ${BLOOM.y}px)` }}>{next}</Layer>
      </>
    );
  },
  mascot: (p, from, to) => {
    const s = path([key(0, from), key(0.42, from, true), key(0.72, { x: 760, y: 380, size: 150 }), key(0.95, to, true), key(1, to)], p);
    return {
      ...calm(from, fade(p)), ...s, happy: bump(p, 0.3, 0.15),
      squash: 1 - 0.12 * bump(p, 0.16, 0.06) + 0.08 * bump(p, 0.24, 0.06), look: [0, 0.2],
    };
  },
};

/**
 * Архитектура → BPMN: новый слайд разворачивается сверху вниз, как чертёж с рулона.
 * Сам лист не едет, его проявляет кромка; рулон лежит на кромке, Валерчик висит под ним и тянет.
 */
const unrollAt = (p: number) => smooth(p, 0.26, 0.86);
const ROLL = 28;
/** нижний край рулона: уходит за низ кадра целиком, чтобы не пропадать в кадре */
const rollEdge = (p: number) => (1080 + 2 * ROLL + 24) * unrollAt(p);
export const unrollDown: TransitionDef = {
  frames: 90,
  render: (p, prev, next) => {
    const e = unrollAt(p);
    const edge = rollEdge(p);
    return (
      <>
        <Layer>{prev}<Scrim o={0.1 * e} /></Layer>
        <Layer style={{ clipPath: `inset(0 0 ${Math.max(0, 1080 - edge)}px 0)` }}>{next}</Layer>
        {e > 0 && (
          <>
            {/* лист темнеет, загибаясь в рулон */}
            <div style={{
              position: "absolute", left: 0, right: 0, top: edge - ROLL - 56, height: 56,
              background: "linear-gradient(to bottom, transparent, oklch(21% 0.01 260 / 0.16))",
            }} />
            <div style={{
              position: "absolute", left: 0, right: 0, top: edge - ROLL, height: ROLL, borderRadius: ROLL / 2,
              background: `linear-gradient(to bottom, #fff, ${C.surfaceHigh} 55%, oklch(78% 0.012 40))`, boxShadow: elevation[5],
            }} />
          </>
        )}
      </>
    );
  },
  mascot: (p, from, to) => {
    if (p >= 0.86) {
      // приземлился левее своего места и допрыгивает вправо: так он встаёт лицом к схеме
      const s = path([key(0.86, { x: to.x - 90, y: to.y, size: to.size }), key(0.93, { x: to.x - 40, y: to.y - 70, size: to.size }), key(1, to)], p);
      const u = smooth(p, 0.86, 0.97);
      return { ...calm(to, 1), ...s, look: [lerp(-0.3, to.look?.[0] ?? 0.9, u), lerp(-0.9, to.look?.[1] ?? 0, u)], squash: 1 - 0.1 * bump(p, 0.99, 0.04) };
    }
    const jump = path([key(0, from), key(0.13, { x: 1140, y: 60, size: 140 }), key(0.26, { x: 960, y: 250, size: 200 })], p);
    // висит под рулоном, пока тот не опустит его на пол; дальше рулон уходит вниз без него
    const hang = 250 + softplus(rollEdge(p) + 190 - 250, 50);
    const t = smooth(p, 0.3, 0.86);
    const ride = { x: lerp(960, to.x - 90, t), y: to.y - softplus(to.y - hang, 50), size: lerp(200, to.size, t) };
    const late = p >= 0.34;
    return {
      ...calm(late ? to : from, late ? 1 : fade(p)), ...mix(jump, ride, smooth(p, 0.2, 0.32)),
      look: [-0.3, -0.9], rot: 6 * (1 - t),
    };
  },
};

/** BPMN → Финал: круг раскрывается из-под Валерчика в центре кадра */
export const circleReveal: TransitionDef = {
  frames: 88,
  render: (p, prev, next) => {
    const r = smooth(p, 0.32, 0.86) * 2300;
    return (
      <>
        <Layer>{prev}</Layer>
        <Layer style={{ clipPath: `circle(${r}px at 960px 640px)` }}>{next}</Layer>
      </>
    );
  },
  mascot: (p, from, to) => {
    const center = { x: 960, y: 760, size: 300 };
    const s = path([
      key(0, from), key(0.15, { x: 650, y: 690, size: 260 }), key(0.3, center, true),
      key(0.78, center, true), key(0.89, { x: 1320, y: 780, size: 310 }), key(1, to),
    ], p);
    const late = p >= 0.42;
    return {
      ...calm(late ? to : from, late ? 1 : fade(p)), ...s,
      acc: late ? to.acc : from.acc, poof: bump(p, 0.42, 0.08),
      happy: smooth(p, 0.45, 0.6), squash: 1 - 0.12 * bump(p, 0.31, 0.05), look: [0, 0],
    };
  },
};

/** Титул → Команда: вспышка фотоаппарата, пока Валерчик в прыжке, после неё уже снимок команды */
export const cameraFlash: TransitionDef = {
  frames: 64,
  render: (p, prev, next) => {
    const flash = bump(p, 0.5, 0.26);
    const q = smooth(p, 0.5, 1);
    return (
      <>
        {p < 0.5
          ? <Layer>{prev}</Layer>
          : <Layer style={{ transform: `scale(${1.04 - 0.04 * q})` }}>{next}</Layer>}
        <div style={{ position: "absolute", inset: 0, background: "#fff", opacity: flash }} />
      </>
    );
  },
  mascot: (p, from, to) => {
    const s = path([key(0, from), key(0.45, { x: 900, y: 560, size: 330 }), key(0.9, to, true), key(1, to)], p);
    const late = p >= 0.5;
    return {
      ...calm(late ? to : from, late ? 1 : fade(p)), ...s,
      happy: 1, squash: 1 - 0.14 * bump(p, 0.93, 0.05) + 0.05 * bump(p, 0.06, 0.05),
      look: [lerp(0, -0.6, smooth(p, 0.1, 0.5)), lerp(0.08, -0.4, smooth(p, 0.1, 0.5))],
    };
  },
};
