import { interpolate } from "remotion";
import { FPS, prog, typed } from "../anim";
import type { SlideDef, SlideProps } from "../deck/types";
import { C, elevation, font, radius } from "../theme";
import { BookIcon, Rich, Stage, TextBlock, plainLength } from "../ui";

// ответ дословно из docs/screenshots/dialog-answer.png: так Валерчик отвечает на этот вопрос в демо-базе
const QUESTION = "Как зарегистрироваться на портале?";
const ANSWER_TITLE = "Регистрация на Портале поставщиков";
const STEPS = [
  "На главной странице портала нажмите кнопку **«Регистрация»**.",
  "Выберите тип участника (юридическое лицо, ИП или самозанятый).",
  "Авторизуйтесь через **ЕСИА (Госуслуги)**.",
  "Заполните профиль организации и подтвердите данные.",
  "После проверки придёт уведомление об активации личного кабинета, обычно в течение **одного рабочего дня**.",
];
const SOURCES: [string, number][] = [
  ["Как зарегистрироваться на Портале поставщиков?", 71],
  ["Нужно ли платить за регистрацию и работу на портале?", 50],
];

const T = { type: 12, send: 62, search: 70, answer: 132, meta: 262, gauge: 270 };
const CPS = 62;

function ChatSlide({ f }: SlideProps) {
  const q = typed(QUESTION, f, T.type, 26);
  const sent = f >= T.send;
  const userPop = prog(f, T.send, T.send + 12);
  const searching = f >= T.search && f < T.answer;
  const shown = Math.max(0, Math.floor(((f - T.answer) / FPS) * CPS));
  const titleN = Math.min(shown, ANSWER_TITLE.length);
  let left = shown - ANSWER_TITLE.length;
  const answerOn = prog(f, T.answer - 4, T.answer + 8);
  const meta = prog(f, T.meta, T.meta + 14);
  const conf = prog(f, T.gauge, T.gauge + 36);
  const status = searching ? "ищет в базе знаний…" : "отвечает мгновенно, 24/7";

  return (
    <Stage>
      <TextBlock num="01" label="Чат"
        title="Отвечает по базе знаний, а не по памяти"
        body="Гибридный поиск (векторы BERTA, BM25 и слияние рангов) находит статьи, агент собирает ответ и показывает источники. Если уверенность ниже порога, Валерчик честно отказывается и зовёт оператора.">
        <div style={{ marginTop: 40, width: 560 }}>
          <div style={{ display: "flex", justifyContent: "space-between", fontSize: 20, color: C.inkSoft }}>
            <span>уверенность ответа <span style={{ fontFamily: font.mono, fontSize: 17 }}>· порог 0.40</span></span>
            <span style={{ fontFamily: font.mono, color: C.ink, fontWeight: 500 }}>
              {conf > 0 ? (0.71 * conf).toFixed(2) : "–"}
            </span>
          </div>
          <div style={{ position: "relative", marginTop: 12, height: 14, borderRadius: 7, background: C.surfaceHigh }}>
            <div style={{ position: "absolute", inset: 0, width: `${71 * conf}%`, borderRadius: 7, background: C.ok }} />
            <div style={{ position: "absolute", left: "40%", top: -8, bottom: -8, width: 3, borderRadius: 2, background: C.ink }} />
          </div>
        </div>
      </TextBlock>

      <div style={{
        position: "absolute", left: 800, top: 96, width: 1000, height: 888, borderRadius: radius.xl,
        background: C.surfaceLow, boxShadow: elevation[3], padding: "30px 36px", boxSizing: "border-box",
        display: "flex", flexDirection: "column", gap: 18, fontSize: 22, overflow: "hidden",
      }}>
        <div style={{ display: "flex", alignItems: "baseline", gap: 16 }}>
          <b style={{ fontSize: 25 }}>Валерчик на связи</b>
          <span style={{ fontSize: 19, color: searching ? C.primary : C.inkSoft }}>{status}</span>
        </div>

        <div style={bubble}>
          Здравствуйте! Я умный помощник Портала поставщиков. Задайте вопрос, отвечу по базе знаний,
          а если не смогу, переведу на оператора.
        </div>

        {sent && (
          <div style={{
            alignSelf: "flex-end", background: C.primary, color: "#fff", borderRadius: 26, padding: "14px 24px",
            opacity: userPop, transform: `translateY(${(1 - userPop) * 60}px) scale(${0.9 + 0.1 * userPop})`,
            transformOrigin: "100% 100%", boxShadow: elevation[1],
          }}>{QUESTION}</div>
        )}

        {searching && (
          <div style={{ ...bubble, alignSelf: "flex-start", display: "flex", gap: 8, padding: "20px 24px" }}>
            {[0, 1, 2].map((k) => (
              <span key={k} style={{
                width: 11, height: 11, borderRadius: 6, background: C.inkSoft,
                opacity: 0.35 + 0.65 * Math.abs(Math.sin((f - T.search) * 0.18 - k * 0.7)),
              }} />
            ))}
          </div>
        )}

        {f >= T.answer - 4 && (
          <div style={{ ...bubble, opacity: answerOn, lineHeight: 1.45 }}>
            <b style={{ fontSize: 23 }}>{ANSWER_TITLE.slice(0, titleN)}</b>
            <ol style={{ margin: "10px 0 0", paddingLeft: 30, display: "flex", flexDirection: "column", gap: 6 }}>
              {STEPS.map((step, k) => {
                if (left <= 0) return null;
                const n = left;
                left -= plainLength(step);
                return <li key={k}><Rich text={step} n={n} /></li>;
              })}
            </ol>
          </div>
        )}

        {meta > 0 && (
          <div style={{ opacity: meta, display: "flex", flexDirection: "column", gap: 10 }}>
            <div style={{ fontSize: 18, color: C.inkSoft }}>
              Ответ из базы знаний, уверенность <b style={{ color: C.ok }}>71%</b> · источники:
            </div>
            {SOURCES.map(([name, pct], k) => {
              const t = prog(f, T.meta + 6 + k * 7, T.meta + 20 + k * 7);
              return (
                <span key={name} style={{
                  alignSelf: "flex-start", display: "inline-flex", gap: 10, alignItems: "center",
                  border: `1.5px solid ${C.outline}`, borderRadius: radius.full, padding: "8px 18px",
                  fontSize: 18, background: C.surface, opacity: t, transform: `translateX(${(1 - t) * -24}px)`,
                }}>
                  <BookIcon />{name}<b style={{ color: C.primary }}>{pct}%</b>
                </span>
              );
            })}
          </div>
        )}

        <div style={{
          marginTop: "auto", height: 68, borderRadius: radius.full, background: C.surface,
          border: `1.5px solid ${C.outline}`, display: "flex", alignItems: "center", padding: "0 28px",
          color: q && !sent ? C.ink : C.inkSoft,
        }}>
          {!sent && q ? q : "Опишите ваш вопрос…"}
          {!sent && q && <span style={{
            width: 2.5, height: 28, marginLeft: 3, background: C.primary,
            opacity: interpolate(Math.sin(f * 0.4), [-1, 1], [0, 1]),
          }} />}
        </div>
      </div>
    </Stage>
  );
}

const bubble = {
  background: C.surface, borderRadius: 26, padding: "16px 24px", boxShadow: elevation[1],
  alignSelf: "flex-start", maxWidth: 860, lineHeight: 1.4,
} as const;

export const chat: SlideDef = {
  id: "chat",
  title: "Чат",
  demo: 330,
  Component: ChatSlide,
  mascot: (f) => ({
    x: 330,
    y: 1004,
    size: 250,
    // пока печатается вопрос, показывает указкой на чат; к поиску откладывает её
    stick: 1 - prog(f, T.send - 4, T.search),
    point: 1 - prog(f, T.send - 4, T.search),
    look: [0.85, f > T.answer ? 0.2 : -0.1],
    think: prog(f, T.search, T.search + 12) - prog(f, T.answer, T.answer + 12),
    talk: prog(f, T.answer + 4, T.answer + 12) - prog(f, 248, 258),
    happy: prog(f, T.meta + 4, T.meta + 22),
  }),
};
