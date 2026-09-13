import { bump, lerp, prog, std, typed } from "../anim";
import type { SlideDef, SlideProps } from "../deck/types";
import { C, elevation, font, radius } from "../theme";
import { Chip, Stage, TextBlock } from "../ui";

const DOC = { x: 780, y: 140, w: 360, h: 520 };
// разделы на странице: [верх, высота] в координатах документа
const BLOCKS: [number, number][] = [[70, 118], [200, 204], [416, 88]];
const FIG = { x: 22, y: 250, w: 150, h: 92 };
const CARD = (k: number) => ({ x: 1200, y: 140 + k * 160, w: 620, h: 140 });
const LIST_Y = 700;
const QUESTIONS = [
  "Как подать заявку на участие в котировочной сессии?",
  "Как подписать контракт электронной подписью?",
  "Как выгрузить УПД?",
];
const F = { scan: 16, fly: 62, figure: 128, list: 196, fresh: 218, evict: 246, tie: 286 };

function Bars({ widths, top }: { widths: number[]; top: number }) {
  return (
    <>
      {widths.map((w, k) => (
        <div key={k} style={{ position: "absolute", left: 22, top: top + k * 17, width: w, height: 8, borderRadius: 4, background: C.surfaceHigh }} />
      ))}
    </>
  );
}

/** мини-скриншот внутри «рисунка»: кнопка подписи, как её рисуют методички */
function Figure({ w, h }: { w: number; h: number }) {
  return (
    <div style={{ width: w, height: h, borderRadius: 6, background: "#eef2f8", border: `1px solid ${C.outline}`, position: "relative", overflow: "hidden" }}>
      <div style={{ position: "absolute", left: "8%", top: "12%", width: "55%", height: "10%", borderRadius: 3, background: "#c9d3e3" }} />
      <div style={{ position: "absolute", left: "8%", top: "30%", width: "80%", height: "8%", borderRadius: 3, background: "#dbe2ee" }} />
      <div style={{ position: "absolute", left: "8%", top: "44%", width: "70%", height: "8%", borderRadius: 3, background: "#dbe2ee" }} />
      <div style={{ position: "absolute", right: "8%", bottom: "12%", width: "38%", height: "20%", borderRadius: 4, background: "#d52b2b" }} />
    </div>
  );
}

function RagSlide({ f }: SlideProps) {
  const scan = std(f, F.scan, F.fly - 4);
  const figFly = std(f, F.figure, F.figure + 22);
  const listOn = prog(f, F.list, F.list + 14);
  const fresh = std(f, F.fresh, F.fresh + 20);
  const evict = std(f, F.evict, F.evict + 24);
  const tie = prog(f, F.tie, F.tie + 16);
  const card1 = CARD(1);

  return (
    <Stage>
      <TextBlock num="05" label="База знаний"
        title="Методичка становится статьями за один импорт"
        body="PDF, docx и xlsx режутся по разделам, к каждому куску модель формулирует вопрос. Картинка уходит в ту статью, где на неё ссылается текст. Свежий регламент вытесняет старую статью с тем же вопросом.">
        <Chip tone="primary" style={{ marginTop: 30, fontSize: 21 }}>инструкция на 300 страниц даёт около 600 статей</Chip>
      </TextBlock>

      {/* страница документа */}
      <div style={{
        position: "absolute", left: DOC.x, top: DOC.y, width: DOC.w, height: DOC.h, borderRadius: radius.s,
        background: "#fff", boxShadow: elevation[2], overflow: "hidden",
      }}>
        <div style={{ position: "absolute", left: 22, top: 22, fontFamily: font.mono, fontSize: 15, color: C.inkSoft }}>инструкция-поставщика.pdf</div>
        {BLOCKS.map(([top], k) => (
          <div key={k}>
            <div style={{ position: "absolute", left: 22, top, width: [210, 240, 150][k], height: 13, borderRadius: 4, background: C.ink, opacity: 0.8 }} />
            <Bars top={top + 26} widths={k === 1 ? [300] : k === 0 ? [310, 290, 316, 200] : [300, 240]} />
          </div>
        ))}
        <div style={{ position: "absolute", left: FIG.x, top: FIG.y, opacity: 1 - figFly * 0.6 }}>
          <Figure w={FIG.w} h={FIG.h} />
          <div style={{ fontSize: 12, color: C.inkSoft, marginTop: 4 }}>Рисунок 4</div>
        </div>
        <div style={{ position: "absolute", left: 190, top: 262, width: 150, fontSize: 13, lineHeight: 1.4, color: C.ink }}>
          Нажмите «Подписать» <b style={{ color: C.primary }}>(Рисунок 4)</b> и выберите сертификат.
        </div>
        {scan > 0 && scan < 1 && (
          <div style={{ position: "absolute", left: 0, right: 0, top: scan * DOC.h - 30, height: 60, background: "linear-gradient(transparent, oklch(54% 0.21 25 / 0.16), transparent)" }} />
        )}
        {BLOCKS.map(([top, h], k) => (
          <div key={k} style={{
            position: "absolute", left: 10, top: top - 10, width: DOC.w - 20, height: h + 4, borderRadius: 8,
            border: `2px dashed ${C.primary}`, opacity: prog(f, F.scan + 10 + k * 10, F.scan + 20 + k * 10) * (1 - prog(f, F.fly + k * 22, F.fly + k * 22 + 10)),
          }} />
        ))}
      </div>

      {/* куски превращаются в статьи */}
      {BLOCKS.map(([top, h], k) => {
        const t = std(f, F.fly + k * 22, F.fly + k * 22 + 28);
        if (t <= 0) return null;
        const card = CARD(k);
        const isFresh = k === 1;
        const x = lerp(DOC.x + 10, card.x, t);
        const y = lerp(DOC.y + top - 10, card.y, t) + (isFresh ? fresh * (LIST_Y - card.y) : 0);
        const w = lerp(DOC.w - 20, card.w, t);
        const hh = lerp(h + 4, card.h, t);
        const q = typed(QUESTIONS[k], f, F.fly + k * 22 + 24, 46);
        return (
          <div key={k} style={{
            position: "absolute", left: x, top: y, width: w, height: hh, borderRadius: radius.l,
            background: C.surface, boxShadow: elevation[t > 0.95 ? 2 : 5], padding: "18px 22px", boxSizing: "border-box",
            opacity: isFresh ? 1 : 1 - listOn * 0.55, overflow: "hidden",
          }}>
            <div style={{ fontFamily: font.mono, fontSize: 14, color: C.primary, letterSpacing: "0.06em", textTransform: "uppercase", opacity: t }}>
              {isFresh && fresh > 0.5 ? "свежая статья · 2026" : "вопрос от модели"}
            </div>
            <div style={{ marginTop: 8, fontSize: 22, fontWeight: 600, lineHeight: 1.3, width: isFresh ? 440 : undefined }}>{q}</div>
            <div style={{ marginTop: 10, width: isFresh ? 400 : 520, height: 8, borderRadius: 4, background: C.surfaceHigh }} />
          </div>
        );
      })}

      {/* картинка летит за своей ссылкой */}
      {figFly > 0 && (
        <div style={{
          position: "absolute",
          left: lerp(DOC.x + FIG.x, card1.x + 460, figFly),
          top: lerp(DOC.y + FIG.y, card1.y + 30, figFly) + fresh * (LIST_Y - card1.y),
          transform: `scale(${lerp(1, 0.9, figFly)}) rotate(${bump(figFly, 0.5, 0.5) * -8}deg)`, transformOrigin: "0 0",
        }}>
          <Figure w={FIG.w} h={FIG.h} />
          <Chip tone="primary" style={{ marginTop: 6, fontSize: 14, padding: "3px 10px", fontFamily: font.mono }}>рис. 4</Chip>
        </div>
      )}

      {/* поиск: свежая статья с тем же вопросом вытесняет старую */}
      <div style={{ position: "absolute", left: 1200, top: LIST_Y - 72, width: 620, opacity: listOn }}>
        <div style={{ display: "flex", alignItems: "center", gap: 12, fontSize: 18, color: C.inkSoft }}>
          <span style={{ fontFamily: font.mono }}>поиск:</span>
          <span style={{ padding: "6px 16px", borderRadius: radius.full, border: `1.5px solid ${C.outline}`, color: C.ink, background: C.surface }}>как подписать контракт с ЭП?</span>
        </div>
      </div>
      <div style={{
        position: "absolute", left: 1200, top: LIST_Y + fresh * 160, width: 620, height: 140, borderRadius: radius.l,
        background: C.surfaceLow, border: `1.5px solid ${C.outline}`, padding: "18px 22px", boxSizing: "border-box",
        opacity: listOn * (1 - evict), transform: `translateX(${evict * 120}px)`,
      }}>
        <div style={{ fontFamily: font.mono, fontSize: 14, color: evict > 0.05 ? C.warn : C.inkSoft, letterSpacing: "0.06em", textTransform: "uppercase" }}>
          {f >= F.evict ? "тот же вопрос · вытеснена" : "старая статья · 2025"}
        </div>
        <div style={{ marginTop: 8, fontSize: 22, fontWeight: 600, color: C.inkSoft, textDecoration: evict > 0.05 ? "line-through" : "none" }}>
          Как подписать контракт электронной подписью?
        </div>
      </div>
      <div style={{
        position: "absolute", left: 1200, top: LIST_Y + 172, width: 620, fontSize: 18, lineHeight: 1.45, color: C.inkSoft,
        opacity: tie, transform: `translateY(${(1 - tie) * 12}px)`,
      }}>
        Вопросы разные, а оценки ближе <b style={{ fontFamily: font.mono, color: C.ink }}>0.02</b>? Выше встанет свежая статья.
      </div>
    </Stage>
  );
}

export const rag: SlideDef = {
  id: "rag",
  title: "База знаний",
  demo: 330,
  Component: RagSlide,
  mascot: (f) => ({
    x: 330,
    y: 1004,
    size: 200,
    acc: "glasses",
    look: [0.9, f < F.list ? -0.4 : 0.1],
    think: prog(f, F.fly - 8, F.fly + 4) - prog(f, F.figure + 20, F.figure + 32),
    happy: bump(f, F.evict + 30, 24),
  }),
};
