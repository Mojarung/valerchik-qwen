import { Easing, interpolate } from "remotion";

export const W = 1920;
export const H = 1080;
/** Все тайминги слайдов записаны в кадрах по 30 в секунду, а играет колода в 60:
 *  на экране 60 Гц полноэкранный сдвиг в 30 кадрах читается как дрожь. Кадр плеера
 *  делится на SUB и дальше живёт дробным числом. */
export const FPS = 30;
export const RENDER_FPS = 60;
export const SUB = RENDER_FPS / FPS;

/** 0→1 на отрезке [a, b] с зажимом по краям и мягкой M3-кривой (emphasized decelerate) */
export function prog(frame: number, a: number, b: number, easing = Easing.bezier(0.05, 0.7, 0.1, 1)) {
  return interpolate(frame, [a, b], [0, 1], {
    easing, extrapolateLeft: "clamp", extrapolateRight: "clamp",
  });
}

/** то же на standard-кривой, для движения, которое и начинается, и заканчивается в кадре */
export function std(frame: number, a: number, b: number) {
  return prog(frame, a, b, Easing.bezier(0.2, 0, 0, 1));
}

/** симметричная кривая для того, что едет через весь кадр: шторы, панорамы, круг */
export function smooth(frame: number, a: number, b: number) {
  return prog(frame, a, b, Easing.bezier(0.65, 0, 0.35, 1));
}

export const lerp = (a: number, b: number, t: number) => a + (b - a) * t;

/** мягкий «пол»: для z ≫ k это z, для z ≪ k это 0, без излома между ними */
export const softplus = (z: number, k: number) => k * Math.log1p(Math.exp(z / k));

export interface Key {
  t: number;
  x: number;
  y: number;
  size: number;
  stop?: boolean; // в этой точке Валерчик останавливается
}

/**
 * Путь через ключевые точки кубическим Эрмитом. Касательные считаются с учётом
 * времени между ключами, поэтому скорость непрерывна и на стыках: нет рывка,
 * который даёт склейка отдельных lerp. На концах пути он трогается и тормозит плавно.
 */
export function path(keys: readonly Key[], t: number): { x: number; y: number; size: number } {
  const n = keys.length;
  if (t <= keys[0].t) return keys[0];
  if (t >= keys[n - 1].t) return keys[n - 1];
  let i = 0;
  while (t > keys[i + 1].t) i++;
  const tan = (j: number, f: "x" | "y" | "size") => {
    if (j === 0 || j === n - 1 || keys[j].stop) return 0;
    return (keys[j + 1][f] - keys[j - 1][f]) / (keys[j + 1].t - keys[j - 1].t);
  };
  const a = keys[i];
  const b = keys[i + 1];
  const h = b.t - a.t;
  const u = (t - a.t) / h;
  const u2 = u * u;
  const u3 = u2 * u;
  const at = (f: "x" | "y" | "size") =>
    (2 * u3 - 3 * u2 + 1) * a[f] + (u3 - 2 * u2 + u) * h * tan(i, f)
    + (-2 * u3 + 3 * u2) * b[f] + (u3 - u2) * h * tan(i + 1, f);
  return { x: at("x"), y: at("y"), size: at("size") };
}

/** парабола прыжка: 0 на краях, h в середине */
export const hop = (t: number, h: number) => 4 * h * t * (1 - t);

/** колокол вокруг c шириной w — вспышки, «пуф» при смене костюма */
export function bump(t: number, c: number, w: number) {
  const d = Math.abs(t - c) / w;
  return d >= 1 ? 0 : 0.5 + 0.5 * Math.cos(Math.PI * d);
}

/** сколько символов строки показать: печать со скоростью cps символов в секунду */
export function typed(text: string, frame: number, start: number, cps = 38) {
  const n = Math.max(0, Math.floor(((frame - start) / FPS) * cps));
  return text.slice(0, n);
}

/**
 * Период «живых» деталей в покое. Удержание слайда длится ровно HOLD кадров и
 * зацикливается прыжком назад на HOLD, поэтому всё, что зависит от (кадр mod HOLD),
 * склеивается без шва: моргание, хвост, пульс подсветки.
 */
export const HOLD = 90;
export const cyc = (g: number) => (((g % HOLD) + HOLD) % HOLD) / HOLD;
