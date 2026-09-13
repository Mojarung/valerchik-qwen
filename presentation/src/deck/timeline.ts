import { HOLD } from "../anim";
import type { MascotState } from "../mascot/Valerchik";
import { analytics } from "../slides/Analytics";
import { arch } from "../slides/Arch";
import { bpmn } from "../slides/Bpmn";
import { chat } from "../slides/Chat";
import { extension } from "../slides/Extension";
import { finale } from "../slides/Finale";
import { guard } from "../slides/Guard";
import { learn } from "../slides/Learn";
import { operator } from "../slides/Operator";
import { rag } from "../slides/Rag";
import { recorder } from "../slides/Recorder";
import { role } from "../slides/Role";
import { teamSlide } from "../slides/Team";
import { title } from "../slides/Title";
import { voice } from "../slides/Voice";
import {
  badgeBloom, blindDown, cameraFlash, circleReveal, cornerPull, flipCard, jumpPan, pushAside, splitDoors, stripeWipe,
  swingIn, unrollDown, wipeBehind, zoomThrough,
} from "./transitions";
import type { SlideDef, TransitionDef } from "./types";

export const SLIDES: SlideDef[] = [
  title, teamSlide, chat, extension, role, recorder, rag, guard, operator, learn, analytics, voice, arch, bpmn, finale,
];
/** TRANSITIONS[i] — вход в слайд i; у титула входа нет, его въезд — часть демо */
export const TRANSITIONS: (TransitionDef | null)[] = [
  null, cameraFlash, wipeBehind, cornerPull, jumpPan, zoomThrough, blindDown, pushAside, flipCard, splitDoors, stripeWipe,
  swingIn, badgeBloom, unrollDown, circleReveal,
];

/**
 * Первые PAD кадров перехода повторяют покой прошлого слайда. Плеер ловит конец покоя
 * по событию и отматывает назад, событие приходит с опозданием в кадр-другой,
 * и эти кадры должны выглядеть так же, как покой.
 */
export const PAD = 6;

export interface Segment {
  start: number; // начало перехода входа
  demoStart: number;
  holdStart: number;
  end: number;
}

export const SEGMENTS: Segment[] = (() => {
  let at = 0;
  return SLIDES.map((slide, i) => {
    const enter = TRANSITIONS[i]?.frames ?? 0;
    const seg = { start: at, demoStart: at + enter, holdStart: at + enter + slide.demo, end: at + enter + slide.demo + HOLD };
    at = seg.end;
    return seg;
  });
})();

export const TOTAL = SEGMENTS[SEGMENTS.length - 1].end;

export type View =
  | { i: number; phase: "enter"; p: number }
  | { i: number; phase: "demo" | "hold"; f: number };

export function locate(frame: number): View {
  const i = Math.max(0, SEGMENTS.findIndex((s) => frame < s.end));
  const idx = frame >= TOTAL ? SEGMENTS.length - 1 : i;
  const seg = SEGMENTS[idx];
  const tr = TRANSITIONS[idx];
  if (tr && frame < seg.demoStart) {
    const p = Math.min(1, Math.max(0, (frame - seg.start - PAD) / (tr.frames - PAD)));
    return { i: idx, phase: "enter", p };
  }
  const f = Math.min(frame - seg.demoStart, SLIDES[idx].demo);
  return { i: idx, phase: frame >= seg.holdStart ? "hold" : "demo", f };
}

export function mascotAt(frame: number): MascotState {
  const view = locate(frame);
  const slide = SLIDES[view.i];
  if (view.phase === "enter") {
    const prev = SLIDES[view.i - 1];
    return TRANSITIONS[view.i]!.mascot(view.p, prev.mascot(prev.demo, frame), slide.mascot(0, frame));
  }
  return slide.mascot(view.f, frame);
}
