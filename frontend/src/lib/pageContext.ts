/**
 * Раздел портала, на котором открыт виджет.
 *
 * Короткие вопросы («как отменить», «где посмотреть статус») без него
 * неразрешимы: отменить можно и оферту, и котировочную сессию, и заявку.
 * Контекст присылает embed.js из родительского окна и обновляет его при
 * навигации — виджет открывают один раз, а по сайту ходят дальше.
 *
 * Держим в модуле, а не в состоянии React: смена страницы не должна
 * перерисовывать чат, значение нужно только в момент отправки сообщения.
 */

export interface PageContext {
  path: string;
  title: string;
}

/** Что умеет страница-хозяин: расширение ведёт по порталу, обычный сайт — нет. */
export interface HostCapabilities {
  name: string;
  guides: boolean;
}

let current: PageContext | null = null;
let host: HostCapabilities | null = null;

export function pageContext(): PageContext | null {
  return current;
}

/** Хозяин умеет показывать пошаговые сценарии прямо на портале. */
export function hostSupportsGuides(): boolean {
  return Boolean(host?.guides);
}

/**
 * Отдать сценарий хозяину — расширение подсветит кнопки и проведёт по шагам.
 * Origin не сужаем по той же причине, что и при приёме: адрес хозяина
 * заранее неизвестен, а в сценарии нет ничего, кроме публичных селекторов.
 */
export function sendGuideToHost(guide: unknown): void {
  if (window.parent === window) return;
  window.parent.postMessage({ source: "valerchik", type: "guide", guide }, "*");
}

/**
 * Сообщить хозяину выбранную тему: кнопка чата, подсказки и маскот на портале
 * рисуются контент-скриптом расширения, а не нами, и должны перекрашиваться
 * вместе с чатом. Хозяину без подсказок (embed.js на чужом сайте) сообщение
 * просто ни на что не влияет.
 */
export function sendThemeToHost(theme: "light" | "dark"): void {
  if (window.parent === window) return;
  window.parent.postMessage({ source: "valerchik", type: "theme", theme }, "*");
}

/**
 * Подписка на сообщения от embed.js. Возвращает функцию отписки.
 *
 * Origin отправителя не проверяем: встраивающий сайт — это и есть родительское
 * окно, его адрес заранее неизвестен и законно меняется от установки к
 * установке. Подделать контекст может только тот, кто и так встроил виджет к
 * себе, а влияет он лишь на порядок выдачи; сервер всё равно чистит и обрезает
 * присланное заново.
 */
export function listenForPageContext(): () => void {
  const onMessage = (event: MessageEvent) => {
    const data = event.data as { source?: string; type?: string; page?: unknown } | null;
    if (!data || data.source !== "valerchik" || data.type !== "page") return;
    const page = data.page as { path?: unknown; title?: unknown } | null;
    if (!page || typeof page !== "object") return;
    current = {
      path: String(page.path ?? "").slice(0, 200),
      title: String(page.title ?? "").slice(0, 120),
    };
    // Хозяин представляется вместе с контекстом: расширение — с флагом guides,
    // embed.js на стороннем сайте — без него
    const who = (data as { host?: { name?: unknown; guides?: unknown } }).host;
    host = who && typeof who === "object"
      ? { name: String(who.name ?? ""), guides: Boolean(who.guides) }
      : null;
  };
  window.addEventListener("message", onMessage);
  // Сообщаем родителю, что слушатель встал. Без этого контекст терялся всегда:
  // embed.js отправляет его один раз по событию load фрейма, а мы к этому
  // моменту ещё не смонтированы — страница чата приезжает отдельным ленивым
  // чанком. Сообщение без адресата просто пропадает, и виджет всю сессию
  // спрашивал «как отменить» без раздела портала. Ответ придёт тем же
  // сообщением `page`, так что путь обработки один.
  if (window.parent !== window) {
    window.parent.postMessage({ source: "valerchik", type: "ready" }, "*");
  }
  return () => window.removeEventListener("message", onMessage);
}
