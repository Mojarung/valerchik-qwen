import { useEffect, useState } from "react";
import { onSystemThemeChange } from "@/lib/theme";

/**
 * Текущие значения CSS-переменных темы — для библиотек, которые красят SVG
 * атрибутами и CSS не видят (recharts). Значения перечитываются при смене
 * темы: и явной (атрибут data-theme на <html>), и системной.
 *
 * `names` — константа модуля: новый массив на каждый рендер переподписывал бы
 * наблюдатель зря.
 */
export function useCssVars<T extends string>(names: readonly T[]): Record<T, string> {
  const [values, setValues] = useState(() => readVars(names));
  useEffect(() => {
    const refresh = () => setValues(readVars(names));
    const observer = new MutationObserver(refresh);
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
    const stop = onSystemThemeChange(refresh);
    return () => {
      observer.disconnect();
      stop();
    };
  }, [names]);
  return values;
}

function readVars<T extends string>(names: readonly T[]): Record<T, string> {
  const style = getComputedStyle(document.documentElement);
  return Object.fromEntries(names.map((name) => [name, style.getPropertyValue(name).trim()])) as Record<T, string>;
}
