import { lerp, prog, std } from "../anim";
import type { SlideDef, SlideProps } from "../deck/types";
import { C, elevation, font, radius } from "../theme";
import { BookIcon, Chip, Cursor, Stage, TextBlock } from "../ui";

// пример очереди пробелов: вопросы без статьи и сколько раз их задали
const GAPS: [string, number][] = [
  ["Можно ли продлить срок подписания оферты?", 14],
  ["Где скачать акт сверки с заказчиком?", 9],
  ["Почему не приходит код подтверждения на почту?", 6],
];
const KB_TITLES = ["Как зарегистрироваться на Портале поставщиков?", "Как подписать контракт на портале?"];
const GAP = { x: 800, y: 140, w: 520 };
const KB = { x: 1360, y: 140, w: 460 };
const row = (i: number) => ({ x: GAP.x + 16, y: GAP.y + 76 + i * 112, w: GAP.w - 32, h: 96 });
const SLOT = { x: KB.x + 16, y: KB.y + 150, w: KB.w - 32, h: 96 };
// в прогоне 60 вопросов: по номерам — где ответ «не знаю», где уверенно мимо
const UNSURE = new Set([8, 29, 51]);
const WRONG = new Set([17, 44]);
const F = { cursor: 36, click: 62, fly: 70, landed: 104, swap: 118, live: 150, run: 186, runEnd: 272 };

function LearnSlide({ f }: SlideProps) {
  const fly = std(f, F.fly, F.landed);
  const shift = std(f, F.landed, F.landed + 16);
  const swap = std(f, F.swap, F.swap + 26);
  const live = prog(f, F.live, F.live + 12);
  const n = Math.floor(prog(f, F.run + 6, F.runEnd, (t) => t) * 60);
  const btn = { x: row(0).x + row(0).w - 70, y: row(0).y + 48 };
  const toRun = { x: 1700, y: 690 };
  const cur = std(f, F.cursor, F.click - 4);
  const cur2 = std(f, F.live + 6, F.run - 4);
  const cx = f < F.live + 6 ? lerp(1180, btn.x, cur) : lerp(btn.x, toRun.x, cur2);
  const cy = f < F.live + 6 ? lerp(760, btn.y, cur) : lerp(btn.y, toRun.y, cur2);
  const press = f >= F.run ? prog(f, F.run, F.run + 14) : f >= F.click ? prog(f, F.click, F.click + 14) : 0;

  return (
    <Stage>
      <TextBlock num="08" label="Самообучение"
        title="Вопрос без ответа учит базу знаний"
        body="Вопросы, на которые не нашлось статьи, копятся как пробелы с числом повторов. Один клик, и пробел становится статьёй: индекс переключается blue/green, поиск не прерывается. Золотой набор из 60 перефразированных вопросов показывает, не стало ли хуже." />

      <div style={{ position: "absolute", left: GAP.x, top: GAP.y, width: GAP.w, height: 470, borderRadius: radius.xl, background: C.surfaceLow, boxShadow: elevation[2] }}>
        <div style={{ padding: "22px 22px 0", display: "flex", alignItems: "center", gap: 10 }}>
          <b style={{ fontSize: 20 }}>Пробелы БЗ</b>
          <span style={{ fontSize: 15, color: C.inkSoft }}>вопросы без ответа</span>
        </div>
      </div>
      {GAPS.map(([q, count], i) => {
        const r = row(i);
        const isFirst = i === 0;
        const y = isFirst ? lerp(r.y, SLOT.y, fly) : r.y - shift * 112;
        const x = isFirst ? lerp(r.x, SLOT.x, fly) : r.x;
        const w = isFirst ? lerp(r.w, SLOT.w, fly) : r.w;
        return (
          <div key={q} style={{
            position: "absolute", left: x, top: y, width: w, height: r.h, borderRadius: radius.l, boxSizing: "border-box",
            background: C.surface, border: `1.5px solid ${isFirst && fly > 0.5 ? C.ok : C.outline}`, padding: "14px 16px",
            display: "flex", alignItems: "center", gap: 12, boxShadow: isFirst && fly > 0 && fly < 1 ? elevation[5] : "none",
            zIndex: 2, // статья перелетает в карточку базы знаний и должна лечь поверх неё
          }}>
            {isFirst && fly > 0.5 ? <BookIcon color={C.ok} /> : (
              <span style={{ fontFamily: font.mono, fontSize: 17, color: C.primary, fontWeight: 500, minWidth: 44 }}>×{count}</span>
            )}
            <span style={{ fontSize: 17, lineHeight: 1.35, flex: 1 }}>{q}</span>
            {isFirst && fly < 0.3 && (
              <span style={{ fontSize: 14, padding: "7px 12px", borderRadius: radius.full, background: C.primary, color: "#fff", fontWeight: 600, whiteSpace: "nowrap", opacity: 1 - fly * 3 }}>В статью</span>
            )}
          </div>
        );
      })}

      <div style={{ position: "absolute", left: KB.x, top: KB.y, width: KB.w, height: 470, borderRadius: radius.xl, background: C.surfaceLow, boxShadow: elevation[2] }}>
        <div style={{ padding: "22px 22px 0", display: "flex", alignItems: "center", gap: 10 }}>
          <b style={{ fontSize: 20 }}>База знаний</b>
          <span style={{ marginLeft: "auto", position: "relative", display: "flex", borderRadius: radius.full, border: `1.5px solid ${C.outline}`, overflow: "hidden", fontFamily: font.mono, fontSize: 14 }}>
            <span style={{ position: "absolute", top: 0, bottom: 0, width: "50%", left: `${swap * 50}%`, background: C.primaryContainer }} />
            <span style={{ position: "relative", padding: "5px 12px" }}>blue</span>
            <span style={{ position: "relative", padding: "5px 12px" }}>green</span>
          </span>
        </div>
        <div style={{ padding: "8px 22px 0", fontSize: 14, color: C.inkSoft, height: 40 }}>
          {f < F.swap ? "поиск идёт по индексу blue" : f < F.swap + 26 ? "собираем green, поиск не прерывается…" : "алиас переключён на green"}
        </div>
        {KB_TITLES.map((t, k) => (
          <div key={t} style={{
            position: "absolute", left: 16, right: 16, top: 262 + k * 96, height: 84, borderRadius: radius.l, background: C.surface,
            border: `1.5px solid ${C.outline}`, padding: "0 16px", display: "flex", alignItems: "center", gap: 12, fontSize: 16, color: C.inkSoft,
          }}><BookIcon />{t}</div>
        ))}
      </div>
      {live > 0 && (
        <Chip tone="ok" style={{ position: "absolute", left: SLOT.x + SLOT.w - 150, top: SLOT.y - 18, fontSize: 14, padding: "4px 12px", opacity: live }}>уже в поиске</Chip>
      )}

      <div style={{ position: "absolute", left: 800, top: 640, width: 1020, height: 340, borderRadius: radius.xl, background: C.surfaceLow, boxShadow: elevation[2], padding: "24px 30px", boxSizing: "border-box" }}>
        <div style={{ display: "flex", alignItems: "center", gap: 14 }}>
          <b style={{ fontSize: 20 }}>Регресс качества</b>
          <span style={{ fontFamily: font.mono, fontSize: 16, color: C.inkSoft }}>{n} / 60</span>
          <span style={{ marginLeft: "auto", fontSize: 15, padding: "8px 16px", borderRadius: radius.full, border: `1.5px solid ${C.outline}`, background: C.surface }}>Прогнать набор</span>
        </div>
        <div style={{ marginTop: 26, display: "grid", gridTemplateColumns: "repeat(20, 22px)", gap: 10 }}>
          {Array.from({ length: 60 }, (_, i) => {
            const done = i < n;
            const color = !done ? C.surfaceHigh : WRONG.has(i) ? C.primary : UNSURE.has(i) ? "oklch(72% 0.01 260)" : C.ok;
            return <span key={i} style={{ width: 22, height: 22, borderRadius: 11, background: color, transform: `scale(${done && i === n - 1 ? 1.25 : 1})` }} />;
          })}
        </div>
        <div style={{ marginTop: 26, display: "flex", gap: 22, fontSize: 16, color: C.inkSoft, alignItems: "center", flexWrap: "wrap" }}>
          {[[C.ok, "нашёл верную статью"], ["oklch(72% 0.01 260)", "честно сказал «не знаю»"], [C.primary, "уверенно и мимо"]].map(([color, label]) => (
            <span key={label} style={{ display: "inline-flex", gap: 8, alignItems: "center" }}>
              <span style={{ width: 14, height: 14, borderRadius: 7, background: color }} />{label}
            </span>
          ))}
          <span style={{ marginLeft: "auto", fontFamily: font.mono, fontSize: 14 }}>пример прогона</span>
        </div>
        <div style={{ marginTop: 12, fontSize: 16, color: C.ink }}>Уверенные ошибки считаются отдельно: ответить уверенно и мимо опаснее, чем «не знаю».</div>
      </div>

      <Cursor x={cx} y={cy} press={press} opacity={prog(f, F.cursor - 8, F.cursor)} />
    </Stage>
  );
}

export const learn: SlideDef = {
  id: "learn",
  title: "Самообучение",
  demo: 320,
  Component: LearnSlide,
  mascot: (f) => ({
    x: 330, y: 1004, size: 210, acc: "mortarboard",
    look: [0.9, f < F.run ? -0.5 : 0.2],
    happy: prog(f, F.live, F.live + 14) * (1 - prog(f, F.run, F.run + 10)) + prog(f, F.runEnd, F.runEnd + 14),
    think: prog(f, F.run + 4, F.run + 14) - prog(f, F.runEnd - 6, F.runEnd + 4),
  }),
};
