import { path, prog, typed, type Key } from "../anim";
import type { SlideDef, SlideProps } from "../deck/types";
import { C, elevation, font, radius } from "../theme";
import { BookIcon, Chip, Cursor, Stage, TextBlock } from "../ui";

const TOP = 110;
const H = 880;
const Q = { x: 780, w: 250 };
const M = { x: 1046, w: 470 };
const R = { x: 1532, w: 308 };

// пример обращения; линия и её подпись из routing.lines, статьи из демо-базы знаний
const QUESTION = "Не могу подписать контракт: КриптоПро пишет, что плагин не установлен. Позовите оператора.";
const DRAFT = "Здравствуйте! Проверьте, что КриптоПро ЭЦП Browser plug-in установлен и включён в настройках браузера. После установки перезапустите браузер и снова нажмите «Подписать».";
const HINTS = ["Какая электронная подпись нужна для работы на портале?", "Как подписать контракт на портале?"];
const LINE = "Линия 2 · Техническая поддержка";
const F = { ticket: 18, routed: 40, open: 62, take: 90, draft: 102, tab: 178, send: 198, kb: 238, save: 274 };

const CURSOR: Key[] = [
  { t: F.open - 26, x: 1300, y: 820, size: 0 },
  { t: F.open, x: 905, y: 240, size: 0, stop: true },
  { t: F.take, x: 1432, y: TOP + 42, size: 0, stop: true },
  { t: F.send, x: 1488, y: TOP + H - 50, size: 0, stop: true },
  { t: F.kb, x: 1300, y: TOP + 42, size: 0, stop: true },
  { t: F.save, x: 1402, y: 598, size: 0, stop: true },
];

const column = (x: number, w: number) => ({
  position: "absolute", left: x, top: TOP, width: w, height: H, borderRadius: radius.xl,
  background: C.surfaceLow, boxShadow: elevation[2], overflow: "hidden",
} as const);

const pill = (primary: boolean) => ({
  fontSize: 15, padding: "7px 14px", borderRadius: radius.full, whiteSpace: "nowrap",
  ...(primary ? { background: C.primary, color: "#fff", fontWeight: 600 } : { border: `1.5px solid ${C.outline}`, color: C.ink, background: C.surface }),
} as const);

function OperatorSlide({ f }: SlideProps) {
  const ticket = prog(f, F.ticket, F.ticket + 12);
  const routed = f >= F.routed;
  const open = f >= F.open;
  const mine = f >= F.take;
  const draftOn = prog(f, F.draft, F.draft + 10) * (1 - prog(f, F.tab, F.tab + 8));
  const inInput = f >= F.tab && f < F.send;
  const sent = prog(f, F.send, F.send + 12);
  const modal = prog(f, F.kb + 4, F.kb + 14) * (1 - prog(f, F.save + 6, F.save + 14));
  const saved = prog(f, F.save + 10, F.save + 20);
  const clicks = [F.open, F.take, F.send, F.kb, F.save];
  const last = clicks.filter((k) => f >= k).pop();
  const c = path(CURSOR, f);

  return (
    <Stage>
      <TextBlock num="07" label="Оператор"
        title="Когда нужен человек, у него всё готово"
        body="Классификатор на эмбеддингах сам выбирает линию. Диалог уходит оператору по WebSocket, копайлот пишет черновик по всей переписке. Удачный ответ одной кнопкой становится статьёй, а брошенное обращение возвращается в очередь." />

      {/* очередь */}
      <div style={column(Q.x, Q.w)}>
        <div style={{ padding: "20px 20px 0", display: "flex", alignItems: "center", gap: 8 }}>
          <b style={{ fontSize: 19 }}>Обращения</b>
          <span style={{ ...pill(true), marginLeft: "auto", padding: "4px 10px", fontSize: 13 }}>Очередь</span>
        </div>
        {f < F.ticket && <div style={{ textAlign: "center", marginTop: 40, fontSize: 16, color: C.inkSoft }}>Очередь пуста</div>}
        <div style={{
          margin: "18px 14px", padding: 14, borderRadius: radius.l, background: C.surface,
          border: `1.5px solid ${open ? C.primary : C.outline}`, opacity: ticket, transform: `translateY(${(1 - ticket) * 20}px)`,
          display: "flex", flexDirection: "column", gap: 10,
        }}>
          <div style={{ fontSize: 15, lineHeight: 1.4, display: "-webkit-box", WebkitLineClamp: 3, WebkitBoxOrient: "vertical", overflow: "hidden" }}>{QUESTION}</div>
          <Chip tone={routed ? "primary" : "neutral"} style={{ fontSize: 13, alignSelf: "flex-start", padding: "4px 10px", whiteSpace: "normal" }}>
            {routed ? LINE : "определяем линию…"}
          </Chip>
        </div>
      </div>

      {/* переписка */}
      <div style={column(M.x, M.w)}>
        {open ? (
          <>
            <div style={{ height: 84, padding: "0 16px", display: "flex", alignItems: "center", gap: 8, borderBottom: `1.5px solid ${C.outline}` }}>
              <span style={{ display: "flex", flexDirection: "column", gap: 2 }}>
                <b style={{ fontSize: 17 }}>Линия 2</b>
                <span style={{ fontSize: 14, color: C.inkSoft }}>Техническая поддержка</span>
              </span>
              <span style={{ marginLeft: "auto", display: "flex", gap: 6 }}>
                {mine ? (
                  <>
                    <span style={pill(false)}>В базу знаний</span>
                    <span style={pill(false)}>Завершить</span>
                  </>
                ) : <span style={pill(true)}>Взять в работу</span>}
              </span>
            </div>
            <div style={{ padding: 16, display: "flex", flexDirection: "column", gap: 12 }}>
              <div style={{ alignSelf: "flex-start", maxWidth: 360, background: C.surface, borderRadius: 18, padding: "10px 14px", fontSize: 16, lineHeight: 1.4, boxShadow: elevation[1] }}>{QUESTION}</div>
              {sent > 0 && (
                <div style={{
                  alignSelf: "flex-end", maxWidth: 380, background: C.primary, color: "#fff", borderRadius: 18,
                  padding: "10px 14px", fontSize: 15.5, lineHeight: 1.4, opacity: sent, transform: `translateY(${(1 - sent) * 30}px)`,
                }}>
                  <div style={{ fontSize: 12, opacity: 0.75, marginBottom: 2 }}>Оператор</div>
                  {DRAFT}
                </div>
              )}
            </div>
            {mine && (
              <div style={{
                position: "absolute", left: 14, right: 14, bottom: 96, borderRadius: radius.m, background: C.surface,
                border: `1.5px solid ${C.outline}`, padding: 12, opacity: draftOn,
              }}>
                <div style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 14, fontWeight: 600, color: C.inkSoft }}>
                  <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke={C.primary} strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M9.9 3.5 11 7l3.5 1.1L11 9.2 9.9 12.7 8.8 9.2 5.3 8.1 8.8 7z" /><path d="M17 12l.7 2.3L20 15l-2.3.7L17 18l-.7-2.3L14 15l2.3-.7z" /></svg>
                  Черновик ответа
                  <span style={{ marginLeft: "auto", color: C.primary, fontWeight: 500 }}>Ещё вариант</span>
                </div>
                <div style={{ marginTop: 8, fontSize: 15, lineHeight: 1.45, minHeight: 88 }}>{typed(DRAFT, f, F.draft + 6, 60)}</div>
                <div style={{ fontSize: 13, color: C.inkSoft, fontFamily: font.mono }}>Tab подставит в поле</div>
              </div>
            )}
            <div style={{
              position: "absolute", left: 14, right: 14, bottom: 22, height: 58, borderRadius: radius.full, background: C.surface,
              border: `1.5px solid ${inInput ? C.primary : C.outline}`, display: "flex", alignItems: "center", padding: "0 20px", gap: 10,
              fontSize: 15, color: inInput ? C.ink : C.inkSoft,
            }}>
              <span style={{ flex: 1, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{inInput ? DRAFT : "Ответ пользователю…"}</span>
              <span style={{ width: 36, height: 36, borderRadius: 18, background: C.primary, display: "grid", placeItems: "center" }}>
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="#fff" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round"><path d="M5 12h14M13 6l6 6-6 6" /></svg>
              </span>
            </div>
          </>
        ) : (
          <div style={{ height: "100%", display: "grid", placeItems: "center", fontSize: 17, color: C.inkSoft }}>Выберите обращение из очереди</div>
        )}
      </div>

      {/* подсказки */}
      <div style={column(R.x, R.w)}>
        <div style={{ padding: "22px 20px 8px", fontSize: 17, fontWeight: 700 }}>Подсказки из базы знаний</div>
        {HINTS.map((hint, k) => {
          const t = prog(f, F.open + 6 + k * 8, F.open + 18 + k * 8);
          return (
            <div key={hint} style={{
              margin: "10px 14px", padding: "12px 14px", borderRadius: radius.m, background: C.surface,
              border: `1.5px solid ${C.outline}`, fontSize: 15, lineHeight: 1.4, display: "flex", gap: 10,
              opacity: t, transform: `translateX(${(1 - t) * 20}px)`,
            }}><BookIcon />{hint}</div>
          );
        })}
        {saved > 0 && (
          <Chip tone="ok" style={{ margin: "10px 14px", fontSize: 15, opacity: saved, whiteSpace: "normal" }}>черновик статьи добавлен в базу знаний</Chip>
        )}
      </div>

      {/* модалка «в базу знаний» */}
      {modal > 0 && (
        <div style={{
          position: "absolute", left: 1066, top: 400, width: 430, borderRadius: radius.xl, background: C.surface,
          boxShadow: elevation[5], padding: 22, opacity: modal, transform: `scale(${0.94 + 0.06 * modal})`,
        }}>
          <b style={{ fontSize: 17 }}>Добавить в базу знаний (черновик)</b>
          <div style={{ marginTop: 12, fontSize: 13, color: C.inkSoft }}>ВОПРОС</div>
          <div style={{ fontSize: 14, lineHeight: 1.4 }}>Не могу подписать контракт: КриптоПро пишет, что плагин не установлен</div>
          <div style={{ marginTop: 10, fontSize: 13, color: C.inkSoft }}>ОТВЕТ</div>
          <div style={{ fontSize: 14, lineHeight: 1.4, display: "-webkit-box", WebkitLineClamp: 2, WebkitBoxOrient: "vertical", overflow: "hidden" }}>{DRAFT}</div>
          <div style={{ marginTop: 14, display: "flex", justifyContent: "flex-end", gap: 8 }}>
            <span style={pill(false)}>Отмена</span>
            <span style={pill(true)}>Сохранить черновик</span>
          </div>
        </div>
      )}

      {f >= F.tab - 4 && f < F.tab + 14 && (
        <span style={{
          position: "absolute", left: 1240, top: TOP + H - 150, fontFamily: font.mono, fontSize: 18, padding: "6px 14px",
          borderRadius: radius.s, background: C.ink, color: C.surface, opacity: prog(f, F.tab - 4, F.tab) * (1 - prog(f, F.tab + 8, F.tab + 14)),
        }}>Tab</span>
      )}

      <Cursor x={c.x} y={c.y} press={last === undefined ? 0 : prog(f, last, last + 14)}
        opacity={prog(f, F.open - 30, F.open - 20)} />
    </Stage>
  );
}

export const operator: SlideDef = {
  id: "operator",
  title: "Оператор",
  demo: 320,
  Component: OperatorSlide,
  mascot: (f) => ({
    x: 330, y: 1004, size: 220, acc: "clipboard",
    look: [0.9, -0.2],
    happy: prog(f, F.save + 12, F.save + 28),
  }),
};
