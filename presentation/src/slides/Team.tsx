import { Img, spring } from "remotion";
import { bump, prog, std } from "../anim";
import team from "../assets/team.webp";
import type { SlideDef, SlideProps } from "../deck/types";
import { C, elevation, font, radius } from "../theme";
import { Stage } from "../ui";

// Фото 4:3 в белой рамке-полароиде справа
const PW = 940;
const PH = (PW * 3) / 4;
const PAD = 20;
const CX = 860;
const CY = 150;
// голова надувного Валерчика на снимке, доли ширины и высоты кадра
const HEAD = { x: 0.4875 * PW, y: 0.19 * PH };

function TeamSlide({ f }: SlideProps) {
  const drop = spring({ frame: f - 2, fps: 30, config: { damping: 13, stiffness: 80 } });
  const rise = (d: number) => {
    const t = prog(f, d, d + 24);
    return { opacity: t, transform: `translateY(${(1 - t) * 36}px)` };
  };
  const tag = prog(f, 38, 52);
  return (
    <Stage>
      <div style={{ position: "absolute", left: 120, top: 240, width: 680 }}>
        <div style={{
          ...rise(6), display: "inline-flex", fontFamily: font.mono, fontSize: 24, letterSpacing: "0.14em",
          textTransform: "uppercase", background: C.primaryContainer, color: C.onPrimaryContainer,
          borderRadius: radius.full, padding: "6px 18px",
        }}>Встреча</div>
        <div style={{
          ...rise(12), fontSize: 132, fontWeight: 800, letterSpacing: "-0.04em", lineHeight: 0.95, marginTop: 30,
        }}>Наша<br />команда</div>
        <p style={{
          ...rise(20), fontSize: 36, lineHeight: 1.38, color: C.inkSoft, marginTop: 40, textWrap: "pretty",
        }}>
          Случайно встретили Валерчика прямо на улице. Теперь он наш кент.
        </p>
      </div>

      <div style={{
        position: "absolute", left: CX, top: CY, padding: PAD, background: "#fff", borderRadius: radius.s,
        boxShadow: elevation[5], opacity: prog(f, 0, 10),
        transform: `translateY(${(1 - drop) * -70}px) rotate(${-4.5 + 2.5 * drop}deg)`, transformOrigin: "50% 50%",
      }}>
        <div style={{ position: "relative", width: PW, height: PH, overflow: "hidden", borderRadius: radius.xs }}>
          <Img src={team} style={{ width: PW, height: PH, objectFit: "cover", display: "block" }} />
          <div style={{
            position: "absolute", left: HEAD.x, top: HEAD.y - 16, opacity: tag,
            transform: `translate(-50%, -100%) translateY(${(1 - tag) * 12}px) scale(${0.9 + 0.1 * tag + 0.04 * bump(f, 52, 6)})`,
            transformOrigin: "50% 100%", display: "flex", flexDirection: "column", alignItems: "center",
          }}>
            <span style={{
              background: C.primary, color: "#fff", fontWeight: 700, fontSize: 26, padding: "8px 20px",
              borderRadius: radius.full, boxShadow: elevation[3], whiteSpace: "nowrap",
            }}>Валерчик</span>
            <svg width="22" height="12" viewBox="0 0 22 12" style={{ marginTop: -1 }}>
              <path d="M0 0 L11 12 L22 0 Z" fill={C.primary} />
            </svg>
          </div>
        </div>
      </div>
    </Stage>
  );
}

export const teamSlide: SlideDef = {
  id: "team",
  title: "Наша команда",
  demo: 90,
  Component: TeamSlide,
  mascot: (f) => ({
    x: 400,
    y: 1004,
    size: 240,
    // машет двойнику на фото, потом просто радуется
    look: [0.9, -0.25],
    wave: std(f, 30, 42) * (1 - std(f, 70, 84)),
    happy: prog(f, 34, 50),
  }),
};
