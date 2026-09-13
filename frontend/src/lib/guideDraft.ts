/**
 * Черновик сценария, записанный расширением прямо на портале.
 *
 * Расширение открывает админку с якорем `#draft=<base64 JSON>`: якорь не
 * уходит на сервер и не попадает в его логи, а сохранит сценарий сам
 * администратор — своим токеном, из уже открытой панели.
 *
 * Забираем якорь при старте приложения, а не на странице сценариев: путь до
 * неё может лежать через экран входа, и к моменту монтирования страницы адрес
 * уже переписан роутером. Черновик ждёт в sessionStorage — он привязан к
 * вкладке и умирает вместе с ней, что для одноразовой передачи ровно то, что нужно.
 */

const KEY = "valerchik-guide-draft";

export function captureGuideDraft(): void {
  const match = /[#&]draft=([^&]+)/.exec(window.location.hash);
  if (!match) return;
  try {
    const bytes = Uint8Array.from(atob(decodeURIComponent(match[1] ?? "")), (c) => c.charCodeAt(0));
    sessionStorage.setItem(KEY, new TextDecoder().decode(bytes));
  } catch {
    /* испорченный якорь — просто нет черновика */
  }
  history.replaceState(null, "", window.location.pathname + window.location.search);
}

/** Забрать черновик (одноразово) — или null, если его нет. */
export function takeGuideDraft<T>(): T | null {
  try {
    const raw = sessionStorage.getItem(KEY);
    if (!raw) return null;
    sessionStorage.removeItem(KEY);
    return JSON.parse(raw) as T;
  } catch {
    return null;
  }
}
