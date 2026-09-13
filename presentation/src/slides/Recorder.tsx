import { Img } from "remotion";
import { bump, lerp, prog, std, typed } from "../anim";
import portalHome from "../assets/portal-home.webp";
import portalRegister from "../assets/portal-register.webp";
import type { SlideDef, SlideProps } from "../deck/types";
import { C, elevation, font, radius } from "../theme";
import { BrowserFrame, Cursor, Stage, TextBlock } from "../ui";

const BX = 760;
const BY = 150;
const BW = 700;
const K = BW / 1440;
const BH = 900 * K;
const at = (x: number, y: number) => ({ x: BX + x * K, y: BY + 52 + y * K });
const T1 = { ...at(1160, 12), w: 198 * K, h: 48 * K };
const T2 = { ...at(1002, 832), w: 284 * K, h: 48 * K };
const center = (t: { x: number; y: number; w: number; h: number }) => ({ x: t.x + t.w / 2, y: t.y + t.h / 2 });

const PANEL = { x: 1500, y: 150, w: 320, h: 490 };
const DONE = { x: PANEL.x + PANEL.w - 74, y: PANEL.y + PANEL.h - 44 };

const F = {
  c1: 16, click1: 50, nav: 70, c2: 96, click2: 128, c3: 150, done: 174,
  swap: 184, conjure: 214, title: 236, questions: 256, step1: 268, step2: 288, save: 318,
};

// формулировки и заголовки шагов из сценария register-supplier
const QUESTIONS = ["как зарегистрироваться на портале", "как стать поставщиком на портале", "хочу зарегистрироваться"];
const ROWS = [
  { at: F.click1 + 6, text: "«Зарегистрироваться»", where: "кнопка в шапке" },
  { at: F.click2 + 6, text: "«Выбрать электронную подпись»", where: "форма регистрации" },
];

function Label({ children }: { children: string }) {
  return <div style={{ fontSize: 15, letterSpacing: "0.08em", color: C.inkSoft, textTransform: "uppercase", marginBottom: 8 }}>{children}</div>;
}

function Field({ value, busy, mono, height = 52 }: { value: string; busy: number; mono?: boolean; height?: number }) {
  return (
    <div style={{
      position: "relative", minHeight: height, borderRadius: radius.m, border: `1.5px solid ${C.outline}`,
      background: C.surface, padding: "12px 18px", boxSizing: "border-box", fontSize: 21, overflow: "hidden",
      fontFamily: mono ? font.mono : undefined, whiteSpace: "pre-line", lineHeight: 1.4,
    }}>
      {value}
      {busy > 0 && (
        <div style={{
          position: "absolute", inset: 0, opacity: busy,
          background: `linear-gradient(100deg, transparent 20%, oklch(93% 0.035 25 / 0.9) 50%, transparent 80%)`,
          backgroundSize: "200% 100%", backgroundPositionX: `${(1 - (busy * 3) % 1) * 200}%`,
        }} />
      )}
    </div>
  );
}

function LockChip() {
  return (
    <span style={{
      display: "inline-flex", gap: 8, alignItems: "center", fontSize: 17, color: C.inkSoft,
      fontFamily: font.mono, padding: "6px 12px", borderRadius: radius.s, background: C.surfaceHigh,
    }}>
      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round"><rect x="4" y="11" width="16" height="10" rx="2" /><path d="M8 11V7a4 4 0 0 1 8 0v4" /></svg>
      селектор из записи
    </span>
  );
}

function RecorderSlide({ f, g }: SlideProps) {
  const nav = std(f, F.nav, F.nav + 18);
  const swap = std(f, F.swap, F.swap + 24);
  const recBlink = (g % 30) < 18 ? 1 : 0.3;

  // курсор: шапка → кнопка формы → «Готово»; во второй фазе → «Сочинить тексты» → «Сохранить»
  const p1 = center(T1);
  const p2 = center(T2);
  const a = std(f, F.c1, F.click1 - 4);
  const b = std(f, F.c2, F.click2 - 4);
  const c = std(f, F.c3, F.done - 4);
  let cx = lerp(1180, p1.x, a);
  let cy = lerp(560, p1.y, a);
  if (f >= F.c2) { cx = lerp(p1.x, p2.x, b); cy = lerp(p1.y, p2.y, b); }
  if (f >= F.c3) { cx = lerp(p2.x, DONE.x, c); cy = lerp(p2.y, DONE.y, c); }
  const conjureBtn = { x: 900, y: 924 };
  const saveBtn = { x: 1728, y: 924 };
  const d = std(f, F.swap + 10, F.conjure - 4);
  const e = std(f, F.step2 + 4, F.save - 4);
  if (f >= F.swap + 10) { cx = lerp(1300, conjureBtn.x, d); cy = lerp(700, conjureBtn.y, d); }
  if (f >= F.step2 + 4) { cx = lerp(conjureBtn.x, saveBtn.x, e); cy = lerp(conjureBtn.y, saveBtn.y, e); }
  const clicks = [F.click1, F.click2, F.done, F.conjure, F.save];
  const last = clicks.filter((k) => f >= k).pop();
  const press = last === undefined ? 0 : prog(f, last, last + 14);
  const cursorOn = prog(f, F.c1 - 6, F.c1 + 4) * (1 - bump(f, F.swap + 4, 8));

  const busy = f >= F.conjure && f < F.title ? prog(f, F.conjure, F.conjure + 6) : 0;
  const drafting = f >= F.conjure && f < F.step2 + 20;
  const badge = (t: { x: number; y: number; w: number }, n: number, from: number) => {
    const s = prog(f, from, from + 10);
    return (
      <div style={{
        position: "absolute", left: t.x + t.w - 12, top: t.y - 18, width: 30, height: 30, borderRadius: 15,
        background: C.primary, color: "#fff", fontSize: 17, fontWeight: 700, display: "grid", placeItems: "center",
        transform: `scale(${s})`, boxShadow: elevation[2],
      }}>{n}</div>
    );
  };

  return (
    <Stage>
      <TextBlock num="04" label="Сценарии"
        title="Новый сценарий записывается кликами"
        body="Админ включает запись и сам проходит путь на портале. Каждый клик проверяет движок подсказок: неоднозначный селектор в сценарий не попадёт. Заголовки и пояснения сочинит модель, селекторы останутся вашими." />

      <div style={{ position: "absolute", inset: 0, opacity: 1 - swap, transform: `scale(${1 - 0.05 * swap})`, transformOrigin: "1290px 400px" }}>
        <BrowserFrame x={BX} y={BY} w={BW} h={BH} url="zakupki.mos.ru">
          <Img src={portalHome} style={{ position: "absolute", inset: 0, width: BW, height: BH }} />
          <Img src={portalRegister} style={{ position: "absolute", inset: 0, width: BW, height: BH, opacity: nav }} />
        </BrowserFrame>
        {f < F.nav + 10 && <div style={{ opacity: 1 - nav }}>{badge(T1, 1, F.click1 + 2)}</div>}
        {badge(T2, 2, F.click2 + 2)}

        <div style={{
          position: "absolute", left: PANEL.x, top: PANEL.y, width: PANEL.w, height: PANEL.h, borderRadius: radius.l,
          background: "#fff", boxShadow: elevation[5], padding: 20, boxSizing: "border-box",
          display: "flex", flexDirection: "column", gap: 12,
        }}>
          <div style={{ display: "flex", alignItems: "center", gap: 10, fontSize: 19, fontWeight: 700 }}>
            <span style={{ width: 12, height: 12, borderRadius: 6, background: "#ff2f3f", opacity: recBlink }} />
            Запись сценария
          </div>
          <div style={{ fontSize: 15, color: C.inkSoft, lineHeight: 1.4 }}>Нажимайте на портале то, что нажимал бы человек.</div>
          {ROWS.map((row, k) => {
            const t = prog(f, row.at, row.at + 12);
            return (
              <div key={row.text} style={{
                borderRadius: radius.m, background: C.surfaceLow, padding: "10px 14px", opacity: t,
                transform: `translateY(${(1 - t) * 16}px)`, display: "flex", flexDirection: "column", gap: 4,
              }}>
                <div style={{ fontSize: 16, fontWeight: 600 }}>{k + 1}. {row.text}</div>
                <div style={{ fontSize: 13.5, color: C.inkSoft }}>{row.where}</div>
                <div style={{ fontSize: 13.5, color: C.ok, fontFamily: font.mono }}>✓ ровно одно совпадение</div>
              </div>
            );
          })}
          <div style={{ marginTop: "auto", display: "flex", justifyContent: "flex-end", gap: 8, fontSize: 16 }}>
            <span style={{ padding: "8px 16px", borderRadius: radius.full, border: `1.5px solid ${C.outline}`, color: C.inkSoft }}>Отмена</span>
            <span style={{ padding: "8px 18px", borderRadius: radius.full, background: C.primary, color: "#fff", fontWeight: 600 }}>Готово</span>
          </div>
        </div>
      </div>

      {/* редактор в админке: черновик пришёл из записи, тексты сочиняет модель */}
      <div style={{
        position: "absolute", left: 760, top: 130, width: 1060, height: 860, borderRadius: radius.xl,
        background: C.surfaceLow, boxShadow: elevation[3], padding: "30px 36px", boxSizing: "border-box",
        opacity: swap, transform: `translateY(${(1 - swap) * 90}px)`, display: "flex", flexDirection: "column", gap: 20,
      }}>
        <div style={{ display: "flex", alignItems: "center", gap: 14 }}>
          <b style={{ fontSize: 28 }}>Новый сценарий</b>
          <span style={{ fontSize: 17, color: C.inkSoft, fontFamily: font.mono }}>черновик из записи · 2 шага</span>
          {f >= F.save + 8 && (
            <span style={{ marginLeft: "auto", fontSize: 18, padding: "6px 14px", borderRadius: radius.s, background: C.okContainer, color: "oklch(38% 0.1 150)", opacity: prog(f, F.save + 8, F.save + 18) }}>Сохранено</span>
          )}
        </div>
        <div>
          <Label>Название</Label>
          <Field value={typed("Регистрация поставщика", f, F.title, 40)} busy={busy} />
        </div>
        <div>
          <Label>Формулировки вопроса, по одной в строке</Label>
          <Field height={126} busy={busy}
            value={QUESTIONS.filter((_, k) => f >= F.questions + k * 7).join("\n")} />
        </div>
        {[
          { n: 1, title: "Нажмите «Зарегистрироваться»", at: F.step1 },
          { n: 2, title: "Выберите способ регистрации", at: F.step2 },
        ].map((step) => (
          <div key={step.n} style={{ borderRadius: radius.l, border: `1.5px solid ${C.outline}`, padding: 16, display: "flex", flexDirection: "column", gap: 12, background: C.surface }}>
            <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
              <span style={{ fontSize: 16, fontWeight: 600, color: C.onPrimaryContainer, background: C.primaryContainer, borderRadius: radius.s, padding: "4px 12px" }}>Шаг {step.n}</span>
              <LockChip />
            </div>
            <Field value={typed(step.title, f, step.at, 44)} busy={busy} />
          </div>
        ))}
        <div style={{ marginTop: "auto", display: "flex", alignItems: "center", gap: 12, fontSize: 19 }}>
          <span style={{ display: "inline-flex", gap: 8, alignItems: "center", padding: "10px 18px", borderRadius: radius.full, color: C.primary, background: drafting ? C.primaryContainer : "transparent" }}>
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M9.9 3.5 11 7l3.5 1.1L11 9.2 9.9 12.7 8.8 9.2 5.3 8.1 8.8 7z" /><path d="M17 12l.7 2.3L20 15l-2.3.7L17 18l-.7-2.3L14 15l2.3-.7z" /></svg>
            {drafting ? "Сочиняю…" : "Сочинить тексты"}
          </span>
          <span style={{ marginLeft: "auto", padding: "10px 18px", borderRadius: radius.full, border: `1.5px solid ${C.outline}`, color: C.inkSoft }}>Отмена</span>
          <span style={{ padding: "10px 22px", borderRadius: radius.full, background: C.primary, color: "#fff", fontWeight: 600 }}>Сохранить</span>
        </div>
      </div>

      <Cursor x={cx} y={cy} press={press} opacity={cursorOn} />
    </Stage>
  );
}

export const recorder: SlideDef = {
  id: "recorder",
  title: "Сценарии",
  demo: 350,
  Component: RecorderSlide,
  mascot: (f) => ({
    x: 360,
    y: 1004,
    size: 220,
    acc: "beret",
    rec: f < F.done,
    look: [0.9, -0.3],
    happy: prog(f, F.save + 6, F.save + 24),
  }),
};
