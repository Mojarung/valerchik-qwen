import { loadFont as loadInter } from "@remotion/google-fonts/Inter";
import { loadFont as loadMono } from "@remotion/google-fonts/RobotoMono";

/**
 * Material 3 поверх фирменного красного Валерчика. Значения бренда взяты из
 * frontend/src/styles/global.css: тот же oklch(54% 0.21 25), та же холодная «чернильная» гамма.
 * Поверхности чуть тёплые, как тональные surface-контейнеры M3 от красного seed-цвета.
 */
export const C = {
  primary: "oklch(54% 0.21 25)",
  primaryDark: "oklch(46% 0.19 25)",
  primaryContainer: "oklch(93.5% 0.035 25)",
  onPrimaryContainer: "oklch(36% 0.13 25)",
  surface: "oklch(98.7% 0.004 40)",
  surfaceLow: "oklch(96.8% 0.006 40)",
  surfaceContainer: "oklch(95% 0.008 40)",
  surfaceHigh: "oklch(92.6% 0.01 40)",
  ink: "oklch(21% 0.01 260)",
  inkSoft: "oklch(46% 0.012 260)",
  outline: "oklch(87% 0.009 40)",
  ok: "oklch(58% 0.15 150)",
  okContainer: "oklch(94% 0.04 150)",
  warn: "oklch(62% 0.16 60)",
  gold: "#f5a623",
} as const;

/** уровни тени M3: 1 — карточки, 3 — плавающие панели, 5 — то, что тянут руками */
export const elevation = {
  1: "0 1px 2px oklch(30% 0.02 260 / 0.12), 0 1px 3px 1px oklch(30% 0.02 260 / 0.07)",
  2: "0 1px 2px oklch(30% 0.02 260 / 0.14), 0 2px 6px 2px oklch(30% 0.02 260 / 0.08)",
  3: "0 4px 8px 3px oklch(30% 0.02 260 / 0.09), 0 1px 3px oklch(30% 0.02 260 / 0.15)",
  5: "0 8px 12px 6px oklch(30% 0.02 260 / 0.1), 0 4px 4px oklch(30% 0.02 260 / 0.15)",
} as const;

export const radius = { xs: 8, s: 12, m: 16, l: 20, xl: 28, full: 999 } as const;

// Inter — шрифт самого интерфейса Валерчика (frontend/src/styles/global.css), слайды говорят тем же голосом
const inter = loadInter("normal", { weights: ["400", "500", "600", "700", "800"], subsets: ["cyrillic", "latin"] });
const mono = loadMono("normal", { weights: ["400", "500"], subsets: ["cyrillic", "latin"] });

export const font = {
  sans: `${inter.fontFamily}, "Segoe UI", system-ui, sans-serif`,
  mono: `${mono.fontFamily}, ui-monospace, Consolas, monospace`,
};
