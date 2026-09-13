import { FPS, bump, prog } from "../anim";
import type { SlideDef, SlideProps } from "../deck/types";
import { C, elevation, font, radius } from "../theme";
import { BookIcon, Chip, Rich, Stage, TextBlock, plainLength } from "../ui";

// ответы по смыслу статей backend/app/seed/data.py для поставщика и для заказчика
const ROLES = [
  {
    key: "Поставщик",
    chip: "статьи для поставщика",
    answer: "Нажмите **«Регистрация»** на главной странице, выберите тип участника (юрлицо, ИП или самозанятый) и авторизуйтесь через **ЕСИА**. Кабинет активируют после проверки данных, обычно в течение рабочего дня.",
    source: "Как зарегистрироваться на Портале поставщиков?",
  },
  {
    key: "Заказчик",
    chip: "статьи для заказчика",
    answer: "Регистрацию заказчиков ведёт **уполномоченный орган** вашего региона. Самостоятельно: «Зарегистрироваться», сертификат подписи руководителя, затем заявка с типом **«Заказчик»**. Московские заказчики подключаются через ЕАИСТ.",
    source: "Как зарегистрироваться на Портале поставщиков в качестве заказчика?",
  },
] as const;

const F = { ask: 4, pick1: 30, type1: 44, pick2: 168, swap: 176, type2: 188 };
const CPS = 72;
const SEG_W = 250;

function RoleSlide({ f }: SlideProps) {
  const role = f < F.pick2 ? 0 : 1;
  const picked = prog(f, F.pick1, F.pick1 + 10);
  const slide = prog(f, F.pick2, F.pick2 + 12);
  const out = prog(f, F.pick2, F.swap);
  const inn = prog(f, F.swap, F.swap + 14);
  const r = ROLES[role];
  const start = role === 0 ? F.type1 : F.type2;
  const n = Math.max(0, Math.floor(((f - start) / FPS) * CPS));
  const ask = prog(f, F.ask, F.ask + 14);
  const contentShift = role === 0 ? -out * 40 : (1 - inn) * 40;
  const contentOpacity = role === 0 ? (f < F.type1 - 6 ? prog(f, F.pick1 + 4, F.type1) : 1 - out) : inn;

  return (
    <Stage>
      <TextBlock num="03" label="Роль собеседника"
        title="Поставщик и заказчик получают разные ответы"
        body="У портала две стороны сделки, и регистрация у них разная. Роль задаётся переключателем или понимается из слов «я заказчик». Поиск отдаёт статьи только этой роли, а если роль не ясна, бот переспрашивает." />

      <div style={{
        position: "absolute", right: 120, top: 140, background: C.primary, color: "#fff",
        borderRadius: 26, padding: "16px 26px", fontSize: 24, boxShadow: elevation[1],
        opacity: ask, transform: `translateY(${(1 - ask) * 30}px)`,
      }}>Как зарегистрироваться на портале?</div>

      {/* сегментированная кнопка M3 */}
      <div style={{
        position: "absolute", left: 800, top: 250, width: SEG_W * 2, height: 64, borderRadius: radius.full,
        border: `1.5px solid ${C.outline}`, background: C.surface, overflow: "hidden", display: "flex",
      }}>
        <div style={{
          position: "absolute", top: 0, bottom: 0, width: SEG_W, left: SEG_W * slide,
          background: C.primaryContainer, opacity: picked,
        }} />
        {ROLES.map((item, k) => (
          <div key={item.key} style={{
            position: "relative", width: SEG_W, display: "flex", alignItems: "center", justifyContent: "center",
            gap: 10, fontSize: 22, fontWeight: 500,
            color: (k === role && picked > 0.5) ? C.onPrimaryContainer : C.inkSoft,
            borderLeft: k ? `1.5px solid ${C.outline}` : undefined,
          }}>
            {k === role && picked > 0.5 && (
              <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.6" strokeLinecap="round" strokeLinejoin="round"><path d="M20 6 9 17l-5-5" /></svg>
            )}
            {item.key}
          </div>
        ))}
      </div>

      <div style={{
        position: "absolute", left: 800, top: 350, width: 760, height: 470, borderRadius: radius.xl,
        background: C.surfaceLow, boxShadow: elevation[2], padding: "34px 38px", boxSizing: "border-box",
        overflow: "hidden",
      }}>
        <div style={{
          opacity: contentOpacity, transform: `translateY(${contentShift}px)`,
          display: "flex", flexDirection: "column", gap: 22,
        }}>
          <Chip tone="primary" style={{ alignSelf: "flex-start", fontFamily: font.mono, fontSize: 17 }}>{r.chip}</Chip>
          <div style={{ fontSize: 25, lineHeight: 1.5, minHeight: 190 }}>
            <Rich text={r.answer} n={n} />
          </div>
          <span style={{
            alignSelf: "flex-start", display: "inline-flex", gap: 10, alignItems: "center",
            border: `1.5px solid ${C.outline}`, borderRadius: radius.full, padding: "8px 18px", fontSize: 18,
            background: C.surface, opacity: n >= plainLength(r.answer) ? 1 : 0,
          }}><BookIcon />{r.source}</span>
        </div>
      </div>
    </Stage>
  );
}

export const role: SlideDef = {
  id: "role",
  title: "Роль",
  demo: 300,
  Component: RoleSlide,
  mascot: (f) => ({
    x: 1700,
    y: 1000,
    size: 240,
    acc: f < F.pick1 + 4 ? "none" : f < F.pick2 + 4 ? "box" : "bowler",
    poof: bump(f, F.pick1 + 4, 8) + bump(f, F.pick2 + 4, 8),
    look: [-0.8, -0.1],
    talk: (prog(f, F.type1, F.type1 + 6) - prog(f, 136, 142)) + (prog(f, F.type2, F.type2 + 6) - prog(f, 282, 288)),
    happy: prog(f, 288, 300),
  }),
};
