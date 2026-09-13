/**
 * Тема оформления: светлая, тёмная или «как в системе».
 *
 * Выбор хранится в куке, а не в localStorage: куку видно всем вкладкам и
 * поддоменам стенда и, в отличие от localStorage, её можно прочитать до первого
 * кадра из инлайнового скрипта в index.html — иначе страница успевала мигнуть
 * светлым перед переключением в тёмное.
 *
 * «Системная» — это отсутствие куки, а не снимок текущего состояния системы:
 * иначе выбравший «как в системе» днём остался бы в светлой теме вечером.
 */

import { readValue, writeValue } from "@/lib/storage";

export type ThemeChoice = "system" | "light" | "dark";
export type Theme = "light" | "dark";

export const THEME_COOKIE = "valerchik-theme";
const COOKIE_MAX_AGE = 60 * 60 * 24 * 365;
const DARK_QUERY = "(prefers-color-scheme: dark)";

/** Цвет системной строки браузера: должен совпадать с фоном страницы. */
const THEME_COLOR: Record<Theme, string> = { light: "#f4f4f6", dark: "#15171c" };

export function readThemeChoice(): ThemeChoice {
  const value = readValue(THEME_COOKIE);
  return value === "light" || value === "dark" ? value : "system";
}

export function saveThemeChoice(choice: ThemeChoice): void {
  if (choice === "system") writeValue(THEME_COOKIE, "", "path=/; max-age=0; samesite=lax");
  else writeValue(THEME_COOKIE, choice, `path=/; max-age=${COOKIE_MAX_AGE}; samesite=lax`);
}

export function systemTheme(): Theme {
  return window.matchMedia(DARK_QUERY).matches ? "dark" : "light";
}

export function resolveTheme(choice: ThemeChoice): Theme {
  return choice === "system" ? systemTheme() : choice;
}

export function applyTheme(choice: ThemeChoice): Theme {
  const theme = resolveTheme(choice);
  document.documentElement.dataset.theme = theme;
  const meta = document.querySelector('meta[name="theme-color"]');
  if (meta) meta.setAttribute("content", THEME_COLOR[theme]);
  return theme;
}


/**
 * Смена темы волной из левого верхнего угла в правый нижний.
 *
 * Через View Transitions: браузер сам делает снимок старого кадра и накладывает
 * поверх новый, а сама волна описана в CSS (keyframes theme-sweep в global.css).
 * Плавно перекрасить страницу обычными transition нельзя — цвета живут в
 * переменных, и по ним анимируется каждый элемент отдельно, из-за чего фон,
 * текст и тени приходят к новому состоянию вразнобой.
 *
 * Где API нет (или человек просил меньше движения) — тема просто применяется.
 */
export function applyThemeAnimated(choice: ThemeChoice): Theme {
  const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  if (typeof document.startViewTransition !== "function" || reduced) return applyTheme(choice);

  let theme: Theme = resolveTheme(choice);
  document.startViewTransition(() => {
    theme = applyTheme(choice);
  });
  return theme;
}

/** Подписка на смену системной темы — пока выбрана «системная». */
export function onSystemThemeChange(handler: (theme: Theme) => void): () => void {
  const media = window.matchMedia(DARK_QUERY);
  const listener = (e: MediaQueryListEvent) => handler(e.matches ? "dark" : "light");
  media.addEventListener("change", listener);
  return () => media.removeEventListener("change", listener);
}
