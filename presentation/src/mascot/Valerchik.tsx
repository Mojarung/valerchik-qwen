import { bump, cyc } from "../anim";
import { C } from "../theme";

export type Accessory = "none" | "box" | "bowler" | "beret" | "glasses" | "party" | "clipboard" | "mortarboard" | "magnifier";

/**
 * Всё, что делает Валерчик в конкретном кадре. Слайды и переходы описывают только
 * позицию, размер и костюм; бег, наклон и разворот колода досчитывает сама по
 * смещению между соседними кадрами.
 */
export interface MascotState {
  x: number; // центр по горизонтали, px кадра 1920×1080
  y: number; // линия «пола» под лапками
  size: number;
  rot?: number;
  squash?: number; // 1 — обычный, 0.8 — приплюснут при приземлении
  acc?: Accessory;
  poof?: number; // 0..1 облачко при смене костюма
  talk?: number;
  think?: number; // картотека в лапах
  happy?: number;
  point?: number; // правая лапка вверх-вправо
  rec?: boolean; // огонёк микрофона мигает как REC
  brush?: number; // щётка в лапе: вычищает латиницу из сообщения
  stern?: number; // строгие брови и дрожь, как при нарушении
  listen?: number; // лапка у уха, от уха расходятся волны
  wave?: number; // машет лапкой, здороваясь
  bow?: number; // кланяется: лёгкий кивок к зрителю, глаза открыты
  stick?: number; // указка в поднятой лапке
  look?: readonly [number, number];
  shadow?: number;
  // досчитывает колода
  run?: number;
  lean?: number;
  flip?: number;
}

const INK = "#2b303a";

function Hat({ acc }: { acc: Accessory }) {
  switch (acc) {
    case "bowler":
      return (
        <g>
          <path d="M41 28 C40 8 80 8 79 28 Z" fill={INK} stroke="#fff" strokeWidth="2" />
          <rect x="41" y="22" width="38" height="5" fill={C.primary} />
          <ellipse cx="60" cy="28.5" rx="27" ry="4.6" fill="#20242c" stroke="#fff" strokeWidth="1.4" />
        </g>
      );
    case "beret":
      return (
        <g transform="rotate(-11 58 22)">
          <ellipse cx="58" cy="21" rx="27" ry="10" fill="#7a1f2b" stroke="#fff" strokeWidth="2" />
          <path d="M40 19 Q58 13 76 18" stroke="#9a3040" strokeWidth="2" fill="none" opacity="0.7" />
          <rect x="56" y="7.5" width="4" height="6" rx="2" fill="#7a1f2b" />
        </g>
      );
    case "mortarboard":
      return (
        <g>
          <path d="M44 22 L44 30 Q60 36 76 30 L76 22 Z" fill="#3a404c" stroke="#fff" strokeWidth="1.6" />
          <path d="M31 19 L60 8 L89 19 L60 30 Z" fill={INK} stroke="#fff" strokeWidth="2" strokeLinejoin="round" />
          <path d="M60 19 L83 22 L83 33" fill="none" stroke={C.gold} strokeWidth="1.8" strokeLinecap="round" />
          <circle cx="83" cy="35" r="2.6" fill={C.gold} />
        </g>
      );
    case "party":
      return (
        <g>
          <path d="M47 27 L60 -6 L73 27 Z" fill={C.primary} stroke="#fff" strokeWidth="2" strokeLinejoin="round" />
          <path d="M52.5 13 L67.5 13 M50 20 L70 20 M55.5 5.5 L64.5 5.5" stroke={C.gold} strokeWidth="3" />
          <circle cx="60" cy="-7" r="5" fill={C.gold} stroke="#fff" strokeWidth="1.6" />
        </g>
      );
    default:
      return null;
  }
}

function Face({ acc }: { acc: Accessory }) {
  if (acc === "glasses") {
    return (
      <g fill="none" stroke={INK} strokeWidth="2.4">
        <circle cx="49" cy="56" r="12" fill="#fff" fillOpacity="0.14" />
        <circle cx="71" cy="56" r="12" fill="#fff" fillOpacity="0.14" />
        <path d="M58.5 54 Q60 51.5 61.5 54" />
        <path d="M37 53 L31 49 M83 53 L89 49" strokeLinecap="round" />
      </g>
    );
  }
  if (acc === "bowler") {
    return (
      <g>
        <path d="M56.5 79 L63.5 79 L62.4 84 L57.6 84 Z" fill={C.primaryDark} />
        <path d="M57.6 84 L62.4 84 L65.5 99 L60 104 L54.5 99 Z" fill={C.primary} />
      </g>
    );
  }
  return null;
}

const Paw = ({ x, y }: { x: number; y: number }) => (
  <ellipse cx={x} cy={y} rx="5.4" ry="7" fill="#aab0bd" stroke="#8f96a5" strokeWidth="0.8" />
);

/** То, что он держит в лапах: роль и занятие видны без шапки */
function Held({ acc }: { acc: Accessory }) {
  if (acc === "box") {
    // поставщик с коробкой товара
    return (
      <g>
        <rect x="37" y="80" width="42" height="28" rx="3" fill="#c99a62" stroke="#fff" strokeWidth="2" />
        <path d="M37 88.5 H79" stroke="#b1834d" strokeWidth="1.4" />
        <rect x="54" y="80" width="8" height="28" fill="#e6c38e" />
        <rect x="41" y="93" width="10" height="7" rx="1.2" fill="#fff" opacity="0.85" />
        <Paw x={38} y={95} /><Paw x={78} y={95} />
      </g>
    );
  }
  if (acc === "clipboard") {
    // оператор с планшетом обращений
    return (
      <g>
        <rect x="40" y="77" width="36" height="31" rx="3" fill="#b88a55" stroke="#fff" strokeWidth="2" />
        <rect x="43.5" y="82" width="29" height="23" rx="1.5" fill="#fff" />
        <rect x="53" y="74.5" width="10" height="6" rx="2" fill="#8f96a5" />
        <path d="M47 88 H69 M47 93 H65 M47 98 H67" stroke="#c9ccd4" strokeWidth="1.6" strokeLinecap="round" />
        <path d="M47 88 l1.6 1.6 l3 -3" stroke={C.ok} strokeWidth="1.6" fill="none" strokeLinecap="round" />
        <Paw x={41} y={96} /><Paw x={75} y={96} />
      </g>
    );
  }
  if (acc === "magnifier") {
    // аналитик с лупой
    return (
      <g>
        <path d="M84 96 L94 84" stroke="#8a5a2b" strokeWidth="4.2" strokeLinecap="round" />
        <circle cx="100" cy="77" r="10.5" fill="#fff" fillOpacity="0.4" stroke={INK} strokeWidth="3.2" />
        <path d="M95 72 Q97 69 100.5 69" stroke="#fff" strokeWidth="1.8" fill="none" strokeLinecap="round" />
        <Paw x={83} y={96} />
      </g>
    );
  }
  return null;
}

/** Валерчик кадр за кадром: рисунок тот же, что в frontend/src/components/Mascot.tsx */
export function Valerchik({ s, g }: { s: MascotState; g: number }) {
  const c = cyc(g);
  const acc = s.acc ?? "none";
  const run = s.run ?? 0;
  const stride = g * 0.95;
  const bob = Math.abs(Math.sin(stride)) * s.size * 0.045 * run;
  const squash = s.squash ?? 1;
  const poof = s.poof ?? 0;
  const scale = 1 + poof * 0.08;
  const blink = 1 - bump(c, 0.64, 0.028) * 0.94;
  const tail = 9 * (0.5 - 0.5 * Math.cos(2 * Math.PI * c * (run > 0.2 ? 3 : 1)));
  const talk = s.talk ?? 0;
  const mouth = 1 + talk * 0.75 * Math.abs(Math.sin(2 * Math.PI * 9 * c));
  const think = s.think ?? 0;
  const happy = s.happy ?? 0;
  const point = s.point ?? 0;
  const stern = s.stern ?? 0;
  const listen = s.listen ?? 0;
  const brush = s.brush ?? 0;
  const shake = stern * Math.sin(2 * Math.PI * 18 * c) * s.size * 0.008;
  // правая лапка: к уху, когда слушает, иначе указывает вверх-вправо
  const wave = s.wave ?? 0;
  const bow = s.bow ?? 0;
  const swing = Math.sin(2 * Math.PI * 4 * c);
  // машет отдельной ладошкой (см. ниже), поэтому обычная лапка в этот момент не поднимается
  const paw = listen >= point
      ? { tx: 20 * listen, ty: -44 * listen, r: -18 * listen, k: 1 + 0.55 * listen }
      : { tx: 18 * point, ty: -34 * point, r: -40 * point, k: 1 + 0.35 * point };
  const lifted = point > 0 || listen > 0;
  const [lx, ly] = s.look ?? [0, 0];
  const earTilt = happy * 5;
  const footL = -Math.max(0, Math.sin(stride)) * 7 * run;
  const footR = -Math.max(0, -Math.sin(stride)) * 7 * run;
  const recOn = s.rec ? (c < 0.5 ? 1 : 0.25) : 0;

  return (
    <div
      style={{
        position: "absolute",
        left: s.x - s.size / 2,
        top: s.y - s.size,
        width: s.size,
        height: s.size,
        transformOrigin: "50% 100%",
        transform: `translate(${shake}px, ${-bob}px) perspective(${s.size * 2.4}px) rotateX(${-bow * 9}deg) translateY(${bow * s.size * 0.015}px) rotate(${(s.rot ?? 0) + (s.lean ?? 0)}deg) scale(${(s.flip ?? 1) * scale * (2 - squash)}, ${scale * squash})`,
        pointerEvents: "none",
      }}
    >
      <svg viewBox="0 0 120 120" width="100%" height="100%" style={{ overflow: "visible", display: "block" }}>
        <defs>
          <linearGradient id="v-fur" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="#a2a8b6" />
            <stop offset="100%" stopColor="#868d9d" />
          </linearGradient>
          <linearGradient id="v-ear" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="#f9c8ce" />
            <stop offset="100%" stopColor="#f3aeb9" />
          </linearGradient>
          <linearGradient id="v-belly" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="#fbf6f3" />
            <stop offset="100%" stopColor="#f2ebe6" />
          </linearGradient>
        </defs>

        <ellipse cx="60" cy="116" rx="25" ry="3.6" fill="#8b7a80" opacity={0.18 * (s.shadow ?? 1)} />

        {/* хвост */}
        <g transform={`rotate(${tail} 84 99)`}>
          <path d="M84 99 C99 103 110 95 108 83 C107 74 98 71 95 77" fill="none" stroke="#fff" strokeWidth="8.6" strokeLinecap="round" />
          <path d="M84 99 C99 103 110 95 108 83 C107 74 98 71 95 77" fill="none" stroke="#f0a6b3" strokeWidth="4.6" strokeLinecap="round" />
        </g>

        {/* лапки-ножки видны только на бегу */}
        {run > 0.02 && (
          <g opacity={Math.min(1, run * 2)}>
            <ellipse cx="47" cy={112 + footL} rx="8" ry="4.6" fill="#8f96a5" stroke="#fff" strokeWidth="1.6" />
            <ellipse cx="73" cy={112 + footR} rx="8" ry="4.6" fill="#8f96a5" stroke="#fff" strokeWidth="1.6" />
          </g>
        )}

        {/* стикерная обводка */}
        <circle cx="30" cy="31" r="19" fill="#fff" />
        <circle cx="90" cy="31" r="19" fill="#fff" />
        <path d="M60 20 C81 20 94 34 94 53 C94 62 91 69 88 74 C93 81 95 89 95 96 C95 108 81 114 60 114 C39 114 25 108 25 96 C25 89 27 81 32 74 C29 69 26 62 26 53 C26 34 39 20 60 20 Z" fill="#fff" />

        <g transform={`rotate(${-earTilt} 30 44)`}>
          <circle cx="30" cy="31" r="15" fill="url(#v-fur)" />
          <circle cx="30.5" cy="31.5" r="10.8" fill="url(#v-ear)" />
        </g>
        <g transform={`rotate(${earTilt - 13 * listen} 90 44)`}>
          <circle cx="90" cy="31" r="15" fill="url(#v-fur)" />
          <circle cx="89.5" cy="31.5" r="10.8" fill="url(#v-ear)" />
        </g>

        <path d="M60 24 C79 24 90 37 90 54 C90 62 87 68 84 73 C89 80 91 87 91 94 C91 105 79 110 60 110 C41 110 29 105 29 94 C29 87 31 80 36 73 C33 68 30 62 30 54 C30 37 41 24 60 24 Z" fill="url(#v-fur)" />
        <ellipse cx="60" cy="94" rx="17.5" ry="14.5" fill="url(#v-belly)" />

        <ellipse cx="43" cy="92" rx="5.4" ry="7" fill="#aab0bd" stroke="#8f96a5" strokeWidth="0.8"
          transform={think > 0 ? `translate(${-14 * think} ${-4 * think}) rotate(${-24 * think + 8 * think * Math.sin(2 * Math.PI * 6 * c)} 43 92)` : undefined} />
        <ellipse cx="77" cy="92" rx="5.4" ry="7" fill="#aab0bd" stroke="#8f96a5" strokeWidth="0.8" opacity={1 - wave}
          transform={lifted ? `translate(${paw.tx} ${paw.ty}) rotate(${paw.r} 77 92) translate(77 92) scale(${paw.k}) translate(-77 -92)` : undefined} />

        <Held acc={acc} />

        {/* Приветствие: белая мультяшная ладошка без руки, как перчатка у персонажей
            из мультфильмов, — четыре пальца и большой. Висит отдельно справа от головы
            и качается от основания. */}
        {wave > 0.01 && (
          <g opacity={Math.min(1, wave * 1.5)} transform={`translate(9 ${(1 - wave) * 18 - 2})`}>
            {/* на 30% меньше прежней и отражена по горизонтали: большой палец смотрит к голове,
                как у поднятой левой ладони персонажа, повёрнутого к залу */}
            <g transform={`rotate(${-10 + 24 * swing} 102 62) translate(102 52) scale(-0.7 0.7) translate(-102 -52)`}>
              <g fill="#fff" stroke="#c9ced8" strokeWidth="1.5" strokeLinejoin="round">
                {/* мизинец, безымянный, средний, указательный — слева направо, по росту */}
                <rect x="92.6" y="40.5" width="4.4" height="12" rx="2.2" />
                <rect x="97.2" y="36.5" width="4.6" height="15" rx="2.3" />
                <rect x="102" y="34.5" width="4.6" height="16.5" rx="2.3" />
                <rect x="106.8" y="37" width="4.6" height="15" rx="2.3" />
                {/* большой палец снаружи ладони, отставлен вверх и в сторону */}
                <rect x="108" y="51" width="11" height="4.8" rx="2.4" transform="rotate(-38 109 53.4)" />
              </g>
              {/* ладонь поверх оснований пальцев, чтобы их швы не просвечивали */}
              <ellipse cx="102" cy="54" rx="9.2" ry="8.4" fill="#fff" stroke="#c9ced8" strokeWidth="1.5" />
              <path d="M95.2 48.6 Q102 46 109 48.8" fill="none" stroke="#fff" strokeWidth="3" />
              <path d="M97.5 56 Q102 59 106.5 56" fill="none" stroke="#dfe2e8" strokeWidth="1.3" strokeLinecap="round" />
            </g>
          </g>
        )}

        {/* указка: держит поднятой лапкой и чуть водит кончиком по экрану */}
        {(s.stick ?? 0) > 0.01 && (
          <g opacity={s.stick} transform={`rotate(${4 * Math.sin(2 * Math.PI * 2 * c)} 95 60)`}>
            <path d="M93 63 L186 22" stroke="#fff" strokeWidth="6" strokeLinecap="round" />
            <path d="M93 63 L186 22" stroke="#8a5a2b" strokeWidth="3.4" strokeLinecap="round" />
            <circle cx="187" cy="21.5" r="3.6" fill={C.primary} stroke="#fff" strokeWidth="1.4" />
          </g>
        )}

        {/* гарнитура */}
        <path d="M31 47 C32 30 44 25 60 25 C76 25 88 30 89 47" fill="none" stroke={INK} strokeWidth="3.6" strokeLinecap="round" />
        <circle cx="31" cy="49" r="6.2" fill={INK} />
        <circle cx="89" cy="49" r="6.2" fill={INK} />
        <path d="M89 55 C90 64 84 69 78 70" fill="none" stroke={INK} strokeWidth="2" strokeLinecap="round" />
        {recOn > 0 && <circle cx="77" cy="70" r={6.5} fill="#ff3b4a" opacity={0.35 * recOn} />}
        <circle cx="77" cy="70" r="2.9" fill={s.rec ? "#ff2f3f" : "#d92b3a"} opacity={s.rec ? 0.4 + 0.6 * recOn : 1} />

        <path d="M44 42.5 Q49 39.5 54 42.5" fill="none" stroke="#7d8494" strokeWidth="1.5" strokeLinecap="round" opacity="0.7" />
        <path d="M76 42.5 Q71 39.5 66 42.5" fill="none" stroke="#7d8494" strokeWidth="1.5" strokeLinecap="round" opacity="0.7" />
        {/* злость: густые брови домиком к переносице и красная «венка» у макушки */}
        {stern > 0.01 && (
          <g opacity={stern}>
            <g stroke="#fff" strokeWidth="6.4" strokeLinecap="round">
              <path d="M40 39.5 L55.5 46.5" /><path d="M80 39.5 L64.5 46.5" />
            </g>
            <g stroke="#2b303a" strokeWidth="3.8" strokeLinecap="round">
              <path d="M40 39.5 L55.5 46.5" /><path d="M80 39.5 L64.5 46.5" />
            </g>
            <g stroke={C.primary} strokeWidth="2.4" strokeLinecap="round" fill="none"
              transform={`translate(96 22) scale(${0.9 + 0.2 * Math.abs(Math.sin(2 * Math.PI * 3 * c))}) translate(-96 -22)`}>
              <path d="M90 18 Q94 19 94 15" /><path d="M98 15 Q98 19 102 18" />
              <path d="M90 26 Q94 25 94 29" /><path d="M98 29 Q98 25 102 26" />
            </g>
          </g>
        )}

        <ellipse cx="39" cy="64" rx="6.2" ry="4.4" fill="#f3a3b2" opacity="0.62" />
        <ellipse cx="81" cy="64" rx="6.2" ry="4.4" fill="#f3a3b2" opacity="0.62" />

        <g transform={`translate(0 ${56 * (1 - blink)}) scale(1 ${blink})`}>
          <ellipse cx="49" cy="56" rx="9.6" ry="10.6" fill="#fff" />
          <ellipse cx="71" cy="56" rx="9.6" ry="10.6" fill="#fff" />
          <g transform={`translate(${lx * 2.6} ${ly * 2.2 + think * 2.5})`}>
            <ellipse cx="49" cy="56.6" rx="6.9" ry="8" fill="#20242c" />
            <ellipse cx="71" cy="56.6" rx="6.9" ry="8" fill="#20242c" />
            <circle cx="46.7" cy="53.4" r="2.4" fill="#fff" />
            <circle cx="68.7" cy="53.4" r="2.4" fill="#fff" />
            <circle cx="51.2" cy="59.6" r="1.15" fill="#fff" opacity="0.9" />
            <circle cx="73.2" cy="59.6" r="1.15" fill="#fff" opacity="0.9" />
          </g>
        </g>

        <ellipse cx="60" cy="69" rx="13.5" ry="10.5" fill="#faf5f2" />
        <ellipse cx="60" cy="64.5" rx="4.2" ry="3.2" fill="#ef94a4" />
        <path d={stern > 0.5 ? "M55 74 Q60 69.5 65 74" : happy > 0.5 ? "M54 70 Q60 78 66 70" : "M55 70.5 Q60 75.5 65 70.5"} fill="none" stroke="#3c424e" strokeWidth="1.9"
          strokeLinecap="round" transform={`translate(0 ${72 * (1 - mouth)}) scale(1 ${mouth})`} />

        <g stroke="#c6ccd6" strokeWidth="1.4" strokeLinecap="round">
          <path d="M47 64 L34 61.5" /><path d="M47 67.5 L33 68" /><path d="M47 71 L34.5 74" />
          <path d="M73 64 L86 61.5" /><path d="M73 67.5 L87 68" /><path d="M73 71 L85.5 74" />
        </g>

        <Face acc={acc} />
        <Hat acc={acc} />

        {/* картотека: крыска перебирает карточки базы знаний */}
        {think > 0.01 && (
          <g opacity={think} transform={`translate(0 ${(1 - think) * 10})`}>
            {[0, 1, 2, 3].map((k) => (
              <rect key={k} x={7 + k * 8} y="84" width="8" height="13" rx="2"
                fill={k % 2 ? "#eef0f3" : "#f4f5f7"} stroke="#c9ccd4" strokeWidth="0.8"
                transform={`rotate(${-16 * bump((c * 4 + k * 0.1) % 1, 0.25, 0.25)} ${11 + k * 8} 97)`} />
            ))}
            <rect x="3" y="96" width="40" height="17" rx="4" fill="#5b626f" />
            <rect x="3" y="96" width="40" height="4" rx="2" fill="#6d7481" />
            <rect x="18" y="103" width="10" height="4" rx="2" fill="#9aa0ae" />
          </g>
        )}

        {happy > 0.01 && (
          <g stroke={C.gold} strokeWidth="2.2" strokeLinecap="round" opacity={happy * (0.6 + 0.3 * Math.sin(2 * Math.PI * c))}>
            <path d="M10 40 l0 8 M6 44 l8 0" />
            <path d="M110 62 l0 8 M106 66 l8 0" />
            <path d="M16 100 l0 6 M13 103 l6 0" />
          </g>
        )}

        {/* щётка: крыска вычищает латиницу из сообщения, из-под ворса летит пыль */}
        {brush > 0.01 && (
          <g opacity={brush}>
            <g transform={`rotate(${-13 + 25 * (0.5 - 0.5 * Math.cos(2 * Math.PI * 6 * c))} 91 100)`}>
              <g transform="translate(82 111) rotate(39)">
                <rect x="-4.7" y="-27" width="9.4" height="29" rx="4.7" fill="#fff" />
                <rect x="-3.5" y="-26" width="7" height="27" rx="3.5" fill="#c98f4e" />
                <rect x="-7.4" y="-35.4" width="14.8" height="10.8" rx="2.6" fill="#fff" />
                <rect x="-6.3" y="-34.4" width="12.6" height="8.8" rx="2" fill="#8f96a5" />
                <path d="M-6.3 -34 L6.3 -34 L8.4 -44.6 L-8.4 -44.6 Z" fill="#79808f" stroke="#fff" strokeWidth="1.6" strokeLinejoin="round" />
                <g stroke="#aeb5c2" strokeWidth="1.1" strokeLinecap="round">
                  <path d="M-4.2 -34.6 L-5.9 -43.9" /><path d="M-1.4 -34.6 L-2 -44.2" />
                  <path d="M1.4 -34.6 L2 -44.2" /><path d="M4.2 -34.6 L5.9 -43.9" />
                </g>
              </g>
            </g>
            {[0, 1, 2, 3, 4].map((k) => {
              const ph = (c * 3 + k * 0.2) % 1;
              return (
                <circle key={k} cx={107 + 11 * ph} cy={72 - 15 * ph} r={1.4 + 2.6 * ph}
                  fill="#c4cad4" opacity={0.6 * Math.sin(Math.PI * ph)} />
              );
            })}
          </g>
        )}

        {/* волны от уха: он слушает */}
        {listen > 0.01 && (
          <g fill="none" stroke={C.primary} strokeWidth="3" strokeLinecap="round">
            {["M104 26 A 8 8 0 0 1 104 42", "M108 21 A 13.5 13.5 0 0 1 108 47", "M112 16 A 19 19 0 0 1 112 52"].map((d, k) => {
              const ph = (c * 3 + k * 0.17) % 1;
              return <path key={k} d={d} opacity={listen * (1 - ph)} transform={`translate(${100 * (1 - (0.7 + 0.45 * ph))} ${34 * (1 - (0.7 + 0.45 * ph))}) scale(${0.7 + 0.45 * ph})`} />;
            })}
          </g>
        )}

        {poof > 0.01 && (
          <g fill="#fff" stroke="#dfe2e8" strokeWidth="1.2" opacity={poof}>
            {Array.from({ length: 8 }, (_, k) => {
              const a = (k / 8) * Math.PI * 2;
              const r = 30 + 18 * (1 - poof);
              return <circle key={k} cx={60 + Math.cos(a) * r} cy={34 + Math.sin(a) * r * 0.7} r={5 + 5 * poof} />;
            })}
          </g>
        )}
      </svg>
    </div>
  );
}
