import type { CSSProperties, ReactNode } from "react";
import { bump, cyc } from "./anim";
import { C, elevation, font, radius } from "./theme";

export function Stage({ children, bg = C.surface }: { children: ReactNode; bg?: string }) {
  return (
    <div style={{
      position: "absolute", inset: 0, overflow: "hidden", background: bg,
      fontFamily: font.sans, color: C.ink, fontOpticalSizing: "auto",
    }}>
      {children}
    </div>
  );
}

/** Левая колонка каждого слайда: номер, заголовок и два-три предложения по делу */
export function TextBlock({ num, label, title, body, width = 620, children }: {
  num: string; label: string; title: string; body: string; width?: number; children?: ReactNode;
}) {
  return (
    <div style={{ position: "absolute", left: 120, top: 140, width }}>
      <div style={{
        fontFamily: font.mono, fontSize: 22, letterSpacing: "0.12em", color: C.primary,
        textTransform: "uppercase", display: "flex", gap: 14, alignItems: "center",
      }}>
        <span style={{
          background: C.primaryContainer, color: C.onPrimaryContainer, borderRadius: radius.full,
          padding: "4px 14px",
        }}>{num}</span>
        {label}
      </div>
      <h1 style={{
        margin: "30px 0 0", fontSize: 70, lineHeight: 1.04, fontWeight: 700,
        letterSpacing: "-0.028em", textWrap: "balance",
      }}>{title}</h1>
      <p style={{
        margin: "28px 0 0", fontSize: 27, lineHeight: 1.45, color: C.inkSoft, textWrap: "pretty",
      }}>{body}</p>
      {children}
    </div>
  );
}

type Tone = "neutral" | "primary" | "ok" | "warn";
const TONES: Record<Tone, CSSProperties> = {
  neutral: { background: C.surface, color: C.ink, border: `1.5px solid ${C.outline}` },
  primary: { background: C.primaryContainer, color: C.onPrimaryContainer, border: "1.5px solid transparent" },
  ok: { background: C.okContainer, color: "oklch(38% 0.1 150)", border: "1.5px solid transparent" },
  warn: { background: "oklch(95% 0.04 60)", color: "oklch(45% 0.12 60)", border: "1.5px solid transparent" },
};

export function Chip({ children, tone = "neutral", style }: { children: ReactNode; tone?: Tone; style?: CSSProperties }) {
  return (
    <span style={{
      display: "inline-flex", alignItems: "center", gap: 8, borderRadius: radius.s,
      padding: "7px 14px", fontSize: 19, fontWeight: 500, whiteSpace: "nowrap", ...TONES[tone], ...style,
    }}>{children}</span>
  );
}

export function Card({ children, style }: { children: ReactNode; style?: CSSProperties }) {
  return (
    <div style={{
      position: "absolute", background: C.surface, borderRadius: radius.xl, boxShadow: elevation[2], ...style,
    }}>{children}</div>
  );
}

/** Окно браузера в духе M3: верхняя плашка с адресом, контент обрезается по радиусу */
export function BrowserFrame({ x, y, w, h, url, children, style }: {
  x: number; y: number; w: number; h: number; url: string; children: ReactNode; style?: CSSProperties;
}) {
  return (
    <div style={{
      position: "absolute", left: x, top: y, width: w, height: h + 52, borderRadius: radius.l,
      background: C.surfaceHigh, boxShadow: elevation[3], overflow: "hidden", ...style,
    }}>
      <div style={{ height: 52, display: "flex", alignItems: "center", gap: 10, padding: "0 18px" }}>
        {["#e0625b", "#e6b44c", "#62b36b"].map((c) => (
          <span key={c} style={{ width: 13, height: 13, borderRadius: 7, background: c }} />
        ))}
        <span style={{
          marginLeft: 14, flex: 1, height: 32, borderRadius: radius.full, background: C.surface,
          display: "flex", alignItems: "center", padding: "0 16px", fontSize: 17, color: C.inkSoft,
          fontFamily: font.mono,
        }}>{url}</span>
      </div>
      <div style={{ position: "relative", width: w, height: h, overflow: "hidden", background: "#fff" }}>
        {children}
      </div>
    </div>
  );
}

/** Курсор мыши; press — 0…1 ход волны клика */
export function Cursor({ x, y, press = 0, opacity = 1 }: { x: number; y: number; press?: number; opacity?: number }) {
  return (
    <div style={{ position: "absolute", left: x, top: y, opacity, pointerEvents: "none" }}>
      {press > 0 && press < 1 && (
        <span style={{
          position: "absolute", left: -34, top: -34, width: 68, height: 68, borderRadius: 34,
          background: C.primary, opacity: 0.35 * (1 - press), transform: `scale(${0.3 + press * 1.3})`,
        }} />
      )}
      <svg width="34" height="40" viewBox="0 0 17 20" style={{
        position: "absolute", left: -3, top: -2, transform: `scale(${1 - bump(press, 0.15, 0.15) * 0.15})`,
        filter: "drop-shadow(0 2px 3px rgb(0 0 0 / 0.25))",
      }}>
        <path d="M1 1 L1 16 L5 12.5 L8 19 L10.6 17.8 L7.7 11.6 L13 11.3 Z" fill="#1d2027" stroke="#fff" strokeWidth="1.3" strokeLinejoin="round" />
      </svg>
    </div>
  );
}

/** Подсветка цели, как у расширения: кольцо фирменного цвета пульсирует в такт покоя */
export function Highlight({ x, y, w, h, g, on }: { x: number; y: number; w: number; h: number; g: number; on: number }) {
  const pulse = 0.5 - 0.5 * Math.cos(2 * Math.PI * cyc(g) * 2);
  return (
    <div style={{
      position: "absolute", left: x - 6, top: y - 6, width: w + 12, height: h + 12, borderRadius: 10,
      border: `3.5px solid ${C.primary}`, opacity: on,
      boxShadow: `0 0 0 ${4 + pulse * 10}px oklch(54% 0.21 25 / ${0.28 - pulse * 0.2})`,
      transform: `scale(${0.9 + 0.1 * on})`,
    }} />
  );
}

/** Карточка шага подсказки: тот же состав, что рисует extension/content/guide.js */
export function GuideCard({ x, y, w = 380, label, step, total, title, text, on }: {
  x: number; y: number; w?: number; label: string; step: number; total: number; title: string; text: string; on: number;
}) {
  return (
    <div style={{
      position: "absolute", left: x, top: y, width: w, padding: "18px 20px 16px", borderRadius: radius.l,
      background: "#fff", boxShadow: elevation[5], opacity: on,
      transform: `translateY(${(1 - on) * 14}px) scale(${0.94 + 0.06 * on})`, transformOrigin: "50% 0",
    }}>
      <div style={{ display: "flex", justifyContent: "space-between", fontSize: 13, letterSpacing: "0.06em" }}>
        <span style={{ color: C.primary, fontWeight: 700, textTransform: "uppercase" }}>{label}</span>
        <span style={{ color: C.inkSoft }}>{step} из {total}</span>
      </div>
      <div style={{ marginTop: 10, fontSize: 20, fontWeight: 700, lineHeight: 1.25 }}>{title}</div>
      <div style={{ marginTop: 8, fontSize: 16, lineHeight: 1.45, color: C.inkSoft }}>{text}</div>
      <div style={{ marginTop: 14, display: "flex", alignItems: "center", gap: 6 }}>
        {Array.from({ length: total }, (_, k) => (
          <span key={k} style={{
            width: k === step - 1 ? 18 : 7, height: 7, borderRadius: 4,
            background: k === step - 1 ? C.primary : C.outline,
          }} />
        ))}
        <span style={{ marginLeft: "auto", fontSize: 15, padding: "7px 14px", borderRadius: radius.full, border: `1.5px solid ${C.outline}`, color: C.inkSoft }}>Назад</span>
        <span style={{ fontSize: 15, padding: "7px 16px", borderRadius: radius.full, background: C.primary, color: "#fff", fontWeight: 600 }}>Далее</span>
      </div>
    </div>
  );
}

/**
 * Печать текста с **жирными** кусками: n — сколько видимых символов показать.
 * Маркеры не считаются символами, поэтому скорость печати ровная.
 */
export function Rich({ text, n }: { text: string; n: number }) {
  const parts = text.split("**");
  let left = n;
  return (
    <>
      {parts.map((part, k) => {
        if (left <= 0) return null;
        const shown = part.slice(0, left);
        left -= part.length;
        return k % 2 ? <b key={k} style={{ fontWeight: 700 }}>{shown}</b> : <span key={k}>{shown}</span>;
      })}
    </>
  );
}

export const plainLength = (text: string) => text.replaceAll("**", "").length;

export function BookIcon({ size = 18, color = C.inkSoft }: { size?: number; color?: string }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke={color} strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M12 7v14" />
      <path d="M3 18a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1h5a4 4 0 0 1 4 4 4 4 0 0 1 4-4h5a1 1 0 0 1 1 1v13a1 1 0 0 1-1 1h-6a3 3 0 0 0-3 3 3 3 0 0 0-3-3z" />
    </svg>
  );
}
