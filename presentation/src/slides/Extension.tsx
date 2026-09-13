import { Img } from "remotion";
import { bump, lerp, path, prog, std, type Key } from "../anim";
import portalHome from "../assets/portal-home.webp";
import portalRegister from "../assets/portal-register.webp";
import type { SlideDef, SlideProps } from "../deck/types";
import { BrowserFrame, Cursor, GuideCard, Highlight, Stage, TextBlock } from "../ui";

// Окно браузера и перевод координат из CSS-пикселей скриншота (1440×900) в кадр
const BX = 740;
const BY = 170;
const BW = 1100;
const K = BW / 1440;
const BH = 900 * K;
const at = (x: number, y: number) => ({ x: BX + x * K, y: BY + 52 + y * K });

// Цели сняты со скриншотов портала: кнопка в шапке и кнопка внизу формы регистрации
const T1 = { ...at(1160, 12), w: 198 * K, h: 48 * K };
const T2 = { ...at(1002, 832), w: 284 * K, h: 48 * K };

// тексты шагов из backend/app/guides/portal.json, сценарий register-supplier
const STEP1 = {
  title: "Нажмите «Зарегистрироваться»",
  text: "Красная кнопка в правом верхнем углу. Она откроет форму регистрации: пользователь, тип организации, регистрация организации.",
};
const STEP3 = {
  title: "Выберите способ регистрации",
  text: "С электронной подписью: «Выбрать электронную подпись». Нужны КриптоПро CSP и плагин КриптоПро ЭЦП Browser plug-in.",
};

const F = { run1: 10, ring1: 58, card1: 66, cursor: 96, click: 140, nav: 158, run2: 178, ring2: 236, card2: 246 };

export const EXT_START = { x: 880, y: BY + 52 + BH - 6, size: 96 };
const P1 = { x: T1.x + T1.w / 2 + 6, y: T1.y + T1.h + 136, size: EXT_START.size };
// у второй цели Валерчик встаёт справа от кнопки: слева её соседи, их нельзя закрывать
const P2 = { x: T2.x + T2.w + 58, y: EXT_START.y, size: EXT_START.size };

// перебежки дугой: вершина прыжка посередине, скорость на стыках непрерывна
const RUN1: Key[] = [
  { t: F.run1, ...EXT_START },
  { t: (F.run1 + F.ring1 - 4) / 2, x: (EXT_START.x + P1.x) / 2, y: P1.y - 80, size: EXT_START.size },
  { t: F.ring1 - 4, ...P1 },
];
const RUN2: Key[] = [
  { t: F.run2, ...P1 },
  { t: F.run2 + 20, x: (P1.x + P2.x) / 2, y: P1.y - 70, size: EXT_START.size },
  { t: F.ring2 - 2, ...P2 },
];

function ExtensionSlide({ f, g }: SlideProps) {
  const nav = std(f, F.nav, F.nav + 22);
  const card1 = prog(f, F.card1, F.card1 + 14) * (1 - prog(f, F.nav - 6, F.nav + 2));
  const ring1 = prog(f, F.ring1, F.ring1 + 10) * (1 - prog(f, F.nav - 6, F.nav + 2));
  const cur = std(f, F.cursor, F.click - 4);
  const cursorOn = prog(f, F.cursor - 6, F.cursor + 4) * (1 - prog(f, F.nav + 4, F.nav + 14));
  const press = f >= F.click ? prog(f, F.click, F.click + 16) : 0;
  const tx = T1.x + T1.w * 0.55;
  const ty = T1.y + T1.h * 0.5;

  return (
    <Stage>
      <TextBlock num="02" label="Расширение"
        title="Показывает на живом портале, куда нажать"
        body="Расширение для Chrome и Edge. Валерчик подсвечивает кнопку, ставит рядом карточку шага и сам перебегает дальше, даже когда портал открыл другую страницу. Селекторы сняты с живого DOM, зрительная модель не нужна." />

      <BrowserFrame x={BX} y={BY} w={BW} h={BH} url="zakupki.mos.ru">
        <Img src={portalHome} style={{ position: "absolute", inset: 0, width: BW, height: BH }} />
        <Img src={portalRegister} style={{
          position: "absolute", inset: 0, width: BW, height: BH, opacity: nav,
          transform: `translateY(${(1 - nav) * 30}px)`,
        }} />
      </BrowserFrame>

      <Highlight {...T1} g={g} on={ring1} />
      <GuideCard x={T1.x + T1.w - 380 - 132} y={T1.y + T1.h + 34} label="Регистрация поставщика"
        step={1} total={5} {...STEP1} on={card1} />

      <Highlight {...T2} g={g} on={prog(f, F.ring2, F.ring2 + 10)} />
      <GuideCard x={T2.x - 40} y={T2.y - 292} label="Регистрация поставщика"
        step={3} total={5} {...STEP3} on={prog(f, F.card2, F.card2 + 14)} />

      <Cursor x={lerp(1260, tx, cur)} y={lerp(760, ty, cur) + bump(cur, 0.5, 0.5) * -40}
        press={press} opacity={cursorOn} />
    </Stage>
  );
}

export const extension: SlideDef = {
  id: "extension",
  title: "Расширение",
  demo: 330,
  Component: ExtensionSlide,
  mascot: (f) => {
    const { x, y } = f < F.run2 ? path(RUN1, f) : path(RUN2, f);
    const land = bump(f, F.ring1 - 2, 6) + bump(f, F.ring2, 6);
    const pointing = prog(f, F.ring1, F.ring1 + 10) * (1 - prog(f, F.nav, F.nav + 8));
    return {
      x, y, size: EXT_START.size, acc: "none",
      squash: 1 - land * 0.16,
      point: pointing,
      happy: bump(f, F.click + 12, 16),
      look: f < F.run2 ? [0.6, -0.8] : [-0.9, -0.1],
    };
  },
};
