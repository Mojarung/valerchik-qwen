import { Img } from "remotion";
import { prog, std } from "../anim";
// Копии docs/bpmn/*.svg. И .bpmn, и .svg собирает scripts/bpmn.py из одного описания,
// а CI проверяет, что docs/bpmn совпадает с генератором. Менять схему нужно в генераторе:
//   uv run --project backend python scripts/bpmn.py
// и потом обновить копии здесь. В сборке (vite-plugin-singlefile) они уходят в data-URI.
import knowledge from "../assets/bpmn-knowledge.svg";
import quality from "../assets/bpmn-quality.svg";
import support from "../assets/bpmn-support.svg";
import type { SlideDef, SlideProps } from "../deck/types";
import { C, elevation, font, radius } from "../theme";
import { Stage, TextBlock } from "../ui";

// Размеры схем как в SVG (viewBox), отсюда высота при известной ширине
const RATIO = { support: 696 / 3598, knowledge: 354 / 2338, quality: 244 / 1918 };
const PAD = 14;
const CAP = 30;
const GAP = 8;

// Главная схема внизу, во всю ширину правее Валерчика; две поменьше столбиком справа от текста
const MAIN = { x: 370, y: 628, w: 1490 };
const KNOW = { x: 800, y: 140, w: 1060 };
const QUAL = { x: 800, y: 384, w: 1060 };
const F = { main: 2, know: 30, qual: 44 };

function Scheme({ x, y, w, n, name, src, ratio, on, draw }: {
  x: number; y: number; w: number; n: string; name: string; src: string; ratio: number; on: number; draw: number;
}) {
  const iw = w - 2 * PAD;
  const ih = iw * ratio;
  return (
    <div style={{
      position: "absolute", left: x, top: y, width: w, padding: PAD, boxSizing: "border-box",
      background: "#fff", borderRadius: radius.l, border: `1.5px solid ${C.outline}`, boxShadow: elevation[2],
      opacity: on, transform: `translateY(${(1 - on) * 24}px)`,
    }}>
      <div style={{ height: CAP, display: "flex", alignItems: "center", gap: 12 }}>
        <span style={{
          fontFamily: font.mono, fontSize: 15, letterSpacing: "0.1em", textTransform: "uppercase",
          color: C.onPrimaryContainer, background: C.primaryContainer, borderRadius: radius.full, padding: "3px 10px",
        }}>{n}</span>
        <span style={{ fontSize: 20, fontWeight: 700 }}>{name}</span>
      </div>
      {/* схема прорисовывается слева направо, по ходу процесса, за красной кромкой */}
      <div style={{ position: "relative", marginTop: GAP, width: iw, height: ih, clipPath: `inset(0 ${(1 - draw) * 100}% 0 0)` }}>
        <Img src={src} style={{ width: iw, height: ih, display: "block" }} />
      </div>
      {draw > 0 && draw < 1 && (
        <div style={{ position: "absolute", left: PAD + iw * draw - 1.5, top: PAD + CAP + GAP, width: 3, height: ih, background: C.primary }} />
      )}
    </div>
  );
}

function BpmnSlide({ f }: SlideProps) {
  return (
    <Stage>
      <TextBlock num="12" label="BPMN"
        title="Три процесса в нотации BPMN"
        body="Обращение проходит фильтр, поиск и порог уверенности и заканчивается ответом или оператором. Ещё два процесса: методички раскладываются в базу знаний, а отзывы — в оценку качества специалистов." />

      <Scheme {...MAIN} n="1" name="Обработка обращения в поддержку" src={support} ratio={RATIO.support}
        on={prog(f, F.main, F.main + 14)} draw={std(f, F.main + 4, F.main + 44)} />
      <Scheme {...KNOW} n="2" name="Структурирование базы знаний" src={knowledge} ratio={RATIO.knowledge}
        on={prog(f, F.know, F.know + 14)} draw={std(f, F.know + 4, F.know + 28)} />
      <Scheme {...QUAL} n="3" name="Оценка качества работы специалистов" src={quality} ratio={RATIO.quality}
        on={prog(f, F.qual, F.qual + 14)} draw={std(f, F.qual + 4, F.qual + 28)} />
    </Stage>
  );
}

export const bpmn: SlideDef = {
  id: "bpmn",
  title: "BPMN",
  demo: 80,
  Component: BpmnSlide,
  // стоит слева от главной схемы и, пока она прорисовывается, поднимает указку на неё
  mascot: (f) => ({
    x: 240, y: 1004, size: 220,
    look: [0.9, -0.15],
    stick: prog(f, 8, 20),
    point: prog(f, 8, 20),
  }),
};
