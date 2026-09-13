/**
 * Маленькие настройки посетителя: диалог, роль, тема.
 *
 * Основное хранилище — кука: её видят все вкладки, она переживает закрытие
 * вкладки и читается инлайновым скриптом до первого кадра (тема). Но внутри
 * расширения браузера интерфейс живёт на origin вида chrome-extension://…,
 * где document.cookie не работает вовсе — там записи уходят в localStorage.
 * Выбор делается один раз пробной записью, а не по признакам окружения:
 * поведение куки на чужом origin зависит от браузера и его настроек.
 */

const PROBE = "valerchik-probe";

const cookiesWork: boolean = (() => {
  try {
    document.cookie = `${PROBE}=1; path=/; max-age=60; samesite=lax`;
    const ok = document.cookie.includes(`${PROBE}=1`);
    document.cookie = `${PROBE}=; path=/; max-age=0; samesite=lax`;
    return ok;
  } catch {
    return false;
  }
})();

export function readValue(name: string): string {
  if (cookiesWork) {
    const match = document.cookie.match(new RegExp(`(?:^|;\\s*)${name}=([^;]*)`));
    return match?.[1] ? decodeURIComponent(match[1]) : "";
  }
  try {
    return localStorage.getItem(name) ?? "";
  } catch {
    return "";
  }
}

/** `attributes` — хвост куки после значения (path, max-age, samesite). */
export function writeValue(name: string, value: string, attributes: string): void {
  if (cookiesWork) {
    document.cookie = `${name}=${value}; ${attributes}`;
    return;
  }
  try {
    if (value) localStorage.setItem(name, value);
    else localStorage.removeItem(name);
  } catch {
    /* приватный режим без хранилища — настройка проживёт до перезагрузки */
  }
}
