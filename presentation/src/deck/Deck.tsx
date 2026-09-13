import { AbsoluteFill, useCurrentFrame } from "remotion";
import { SUB } from "../anim";
import { Valerchik } from "../mascot/Valerchik";
import { C } from "../theme";
import { SLIDES, TRANSITIONS, locate, mascotAt } from "./timeline";

/** куда он смотрит: знак последнего заметного шага за ~0.8 с; стоит давно — лицом вправо */
function facing(frame: number) {
  for (let k = 0; k < 24; k++) {
    const dx = mascotAt(frame - k).x - mascotAt(frame - k - 1).x;
    if (Math.abs(dx) > 1.2) return Math.sign(dx);
  }
  return 1;
}

/**
 * Вся презентация — одна композиция. Слайды лежат слоями, Валерчик поверх всех:
 * он не принадлежит ни одному слайду, поэтому переходы между ними для него
 * просто продолжение пути.
 */
export function Deck() {
  const frame = useCurrentFrame() / SUB;
  const view = locate(frame);

  let content;
  if (view.phase === "enter") {
    const Prev = SLIDES[view.i - 1];
    const Next = SLIDES[view.i];
    content = TRANSITIONS[view.i]!.render(
      view.p,
      <Prev.Component f={Prev.demo} g={frame} />,
      <Next.Component f={0} g={frame} />,
    );
  } else {
    const Slide = SLIDES[view.i].Component;
    content = <Slide f={view.f} g={frame} />;
  }

  // Бег и наклон — из скорости по окну в ±2 кадра, разворот — среднее направления
  // за последние ~4 кадра: он поворачивается за мгновение, а не щёлкает зеркалом.
  const now = mascotAt(frame);
  const ahead = mascotAt(frame + 2);
  const back = mascotAt(frame - 2);
  const vx = (ahead.x - back.x) / 4;
  const vy = (ahead.y - back.y) / 4;
  let flip = 0;
  for (let j = 0; j < 6; j++) flip += facing(frame - j * 0.75);
  const mascot = {
    ...now,
    run: Math.min(1, Math.hypot(vx, vy) / 6),
    lean: Math.max(-12, Math.min(12, vx * 0.4)),
    // слайд может развернуть его сам (поклоны в финале), иначе — по направлению бега
    flip: now.flip ?? flip / 6,
  };

  return (
    <AbsoluteFill style={{ background: C.surface, overflow: "hidden" }}>
      {content}
      <Valerchik s={mascot} g={frame} />
    </AbsoluteFill>
  );
}
