import { Img } from "remotion";
import { cyc, prog, typed } from "../anim";
import mobile from "../assets/main-mobile.webp";
import widget from "../assets/widget-embed.webp";
import type { SlideDef, SlideProps } from "../deck/types";
import { C, elevation, font, radius } from "../theme";
import { BrowserFrame, Chip, Highlight, Stage, TextBlock } from "../ui";

// строка подключения из docs/EMBED.md
const SNIPPET = '<script src="https://ВАШ-ХОСТ/embed.js" defer></script>';
const BX = 780;
const BY = 250;
const BW = 760;
const K = BW / 1440;
const BH = 900 * K;
const PHONE = { x: 1580, y: 250, w: 260, h: 520 };
const PK = PHONE.w / 390;
const F = { code: 6, ring: 66, listen: 128, heard: 200, speak: 240, speakEnd: 306 };

function VoiceSlide({ f, g }: SlideProps) {
  const listen = prog(f, F.listen, F.listen + 8) * (1 - prog(f, F.heard, F.heard + 8));
  const speak = prog(f, F.speak, F.speak + 10) * (1 - prog(f, F.speakEnd, F.speakEnd + 10));
  const heard = typed("как выгрузить УПД", f, F.heard, 30);
  const mic = { x: PHONE.x + 342 * PK, y: PHONE.y + 737 * PK };
  const c = cyc(g);

  return (
    <Stage>
      <TextBlock num="10" label="Голос и виджет"
        title="Слышит, говорит и встаёт на любой сайт"
        body="Вопрос можно надиктовать: распознаёт локальный faster-whisper, отвечает вслух Piper, разметку перед озвучкой он снимает. Виджет подключается одной строкой и передаёт раздел портала, с которого спрашивают. Номера заявок и ИНН из адреса не уходят." />

      <div style={{
        position: "absolute", left: BX, top: 140, width: 1060, height: 72, borderRadius: radius.m, background: C.ink,
        color: "oklch(90% 0.02 60)", fontFamily: font.mono, fontSize: 22, display: "flex", alignItems: "center", padding: "0 24px", boxSizing: "border-box",
      }}>
        {typed(SNIPPET, f, F.code, 44)}
        <span style={{ width: 11, height: 26, marginLeft: 2, background: C.primary, opacity: c * 4 % 1 < 0.5 ? 1 : 0 }} />
        <span style={{ marginLeft: "auto", fontFamily: font.sans, fontSize: 17, color: "oklch(75% 0.02 60)", opacity: prog(f, 58, 68) }}>одна строка</span>
      </div>

      <BrowserFrame x={BX} y={BY} w={BW} h={BH} url="localhost:8090/embed-test.html">
        <Img src={widget} style={{ position: "absolute", inset: 0, width: BW, height: BH }} />
      </BrowserFrame>
      <Highlight x={BX + 1036 * K} y={BY + 52 + 188 * K} w={384 * K} h={620 * K} g={g} on={prog(f, F.ring, F.ring + 10) * (1 - prog(f, 120, 130))} />

      <div style={{ position: "absolute", left: BX, top: BY + 52 + BH + 24, display: "flex", gap: 10 }}>
        <Chip tone="ok" style={{ fontSize: 16 }}>раздел страницы уходит в запрос</Chip>
        <Chip style={{ fontSize: 16 }}>query-строка не уходит никогда</Chip>
      </div>

      {/* телефон: голосовой ввод и ответ вслух */}
      <div style={{
        position: "absolute", left: PHONE.x, top: PHONE.y, width: PHONE.w, height: PHONE.h, borderRadius: 34, overflow: "hidden",
        boxShadow: `0 0 0 8px ${C.ink}, ${elevation[5]}`, background: "#fff",
      }}>
        <Img src={mobile} style={{ width: PHONE.w, height: PHONE.h }} />
        {listen > 0 && (
          <div style={{ position: "absolute", left: 22, top: 482, width: 170, height: 34, display: "flex", alignItems: "center", gap: 4, opacity: listen }}>
            {Array.from({ length: 16 }, (_, k) => (
              <span key={k} style={{ width: 5, borderRadius: 3, background: C.primary, height: 6 + 22 * Math.abs(Math.sin(2 * Math.PI * (c * 6 + k * 0.13))) }} />
            ))}
          </div>
        )}
        {f >= F.heard && (
          <div style={{ position: "absolute", left: 20, top: 488, width: 170, fontSize: 13.5, background: "#fff", color: C.ink }}>{heard}</div>
        )}
        {speak > 0 && (
          <div style={{
            position: "absolute", left: 14, right: 14, top: 380, borderRadius: 18, background: C.surface, boxShadow: elevation[2],
            padding: "12px 14px", display: "flex", alignItems: "center", gap: 10, opacity: speak,
          }}>
            <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke={C.primary} strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M11 5 6 9H2v6h4l5 4V5z" /></svg>
            {[0, 1, 2].map((k) => (
              <span key={k} style={{ width: 10 + k * 6, height: 10 + k * 6, borderRadius: "50%", border: `2px solid ${C.primary}`, borderLeftColor: "transparent", borderBottomColor: "transparent", transform: "rotate(45deg)", opacity: 0.3 + 0.7 * Math.abs(Math.sin(2 * Math.PI * (c * 3 - k * 0.2))) }} />
            ))}
            <span style={{ fontSize: 13, color: C.inkSoft }}>ответ звучит вслух</span>
          </div>
        )}
      </div>
      {listen > 0 && (
        <span style={{
          position: "absolute", left: mic.x - 26, top: mic.y - 26, width: 52, height: 52, borderRadius: 26, border: `3px solid ${C.primary}`,
          opacity: listen * (1 - (c * 3) % 1), transform: `scale(${0.8 + 0.6 * ((c * 3) % 1)})`,
        }} />
      )}
      <div style={{ position: "absolute", left: PHONE.x - 10, top: PHONE.y + PHONE.h + 30, width: PHONE.w + 20, display: "flex", flexDirection: "column", gap: 8, alignItems: "center" }}>
        <Chip style={{ fontSize: 15, fontFamily: font.mono }}>faster-whisper: речь → текст</Chip>
        <Chip style={{ fontSize: 15, fontFamily: font.mono }}>Piper: текст → речь</Chip>
      </div>
    </Stage>
  );
}

export const voice: SlideDef = {
  id: "voice",
  title: "Голос и виджет",
  demo: 320,
  Component: VoiceSlide,
  mascot: (f) => ({
    x: 330, y: 1004, size: 220,
    look: [0.9, -0.2],
    listen: prog(f, F.listen, F.listen + 10) * (1 - prog(f, F.heard + 4, F.heard + 14)),
    talk: prog(f, F.speak, F.speak + 8) - prog(f, F.speakEnd, F.speakEnd + 8),
  }),
};
