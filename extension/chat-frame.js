/**
 * Прослойка между порталом и чатом.
 *
 * Сам чат — тот же React-код, что и на сайте Валерчика, только собранный
 * внутрь расширения (widget/, `npm run build:extension`). Грузить /widget с
 * сервера прямо в страницу портала нельзя: Chrome с 2026 года блокирует
 * обращения публичных https-страниц к локальной сети
 * (ERR_BLOCKED_BY_LOCAL_NETWORK_ACCESS_CHECKS), а страницам расширения этот
 * запрет не писан. Заодно так обходится CSP `frame-src 'self'` на страницах
 * входа. Адрес сервера чат получает параметром ?api=, обращается к нему сам —
 * и за ответами, и вебсокетом.
 *
 * Сообщения ходят транзитом в обе стороны: от виджета (ready, guide, theme) —
 * в страницу портала, где их ждёт контент-скрипт; от контент-скрипта (page) —
 * в виджет.
 */
const api = globalThis.browser ?? globalThis.chrome;
/** Общие проверки; content/lib.js подключён в chat.html перед этим файлом. */
const VK = globalThis.VK;
const DEFAULTS = { serverUrl: "https://201-24-127-126.sslip.io" };
/** Сколько ждём /api/health, прежде чем объяснить, что сервер не поднят. */
const PROBE_MS = 6000;
const widget = document.getElementById("widget");
const err = document.getElementById("err");
const addr = document.getElementById("addr");
const why = document.getElementById("why");

/** storage.get в Chrome отдаёт результат в колбэк, в Firefox — промисом; ждём любой. */
function readSettings() {
  return new Promise((resolve) => {
    try {
      const maybe = api.storage.sync.get(DEFAULTS, (items) => resolve(items || {}));
      if (maybe && typeof maybe.then === "function") maybe.then((items) => resolve(items || {}));
    } catch {
      resolve({});
    }
  });
}

function clean(url) {
  const trimmed = String(url || "").trim().replace(/\/+$/, "");
  try {
    new URL(trimmed);
    return trimmed;
  } catch {
    return DEFAULTS.serverUrl;
  }
}

/**
 * Адреса, по которым стоит поискать сервер.
 *
 * Имя localhost Firefox разрешает сначала в IPv6 (::1), а uvicorn по умолчанию
 * слушает только 127.0.0.1 — и чат, работающий в Chrome, в Firefox молча не
 * находил сервер. Chrome в этом случае сам откатывается на IPv4, Firefox не
 * обязан. Поэтому пробуем и числовой адрес тоже, не спрашивая человека.
 */
function candidates(url) {
  const list = [url];
  try {
    const parsed = new URL(url);
    if (parsed.hostname === "localhost") {
      parsed.hostname = "127.0.0.1";
      list.push(parsed.href.replace(/\/+$/, ""));
    }
  } catch { /* адрес уже проверен в clean() */ }
  return list;
}

/** Пусто — сервер отвечает; иначе понятная причина отказа. */
async function reach(url) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), PROBE_MS);
  try {
    const resp = await fetch(`${url}/api/health`, { signal: controller.signal, cache: "no-store" });
    return resp.ok ? "" : `ответил ${resp.status} ${resp.statusText}`;
  } catch (error) {
    if (error && error.name === "AbortError") return `молчит дольше ${PROBE_MS / 1000} секунд`;
    // Текст браузера здесь важнее нашего: он различает отказ соединения,
    // подмену схемы на https и блокировку запроса политикой безопасности
    return `${(error && error.name) || "Ошибка"}: ${(error && error.message) || error}`;
  } finally {
    clearTimeout(timer);
  }
}

let base = DEFAULTS.serverUrl;

/** Показать чат, обращённый к найденному адресу. */
function show(url) {
  base = url;
  addr.textContent = url;
  err.style.display = "none";
  widget.style.display = "";
  const src = `widget/index.html?widget=1&api=${encodeURIComponent(url)}`;
  if (!widget.src.endsWith(src)) widget.src = src;
}

async function connect() {
  addr.textContent = base;
  const reasons = [];
  for (const url of candidates(base)) {
    const reason = await reach(url);
    if (!reason) {
      show(url);
      return;
    }
    reasons.push(`${url} — ${reason}`);
  }
  widget.style.display = "none";
  err.style.display = "block";
  why.textContent = reasons.join("; ");
}

readSettings().then((items) => {
  base = clean((items || {}).serverUrl || DEFAULTS.serverUrl);
  void connect();
});

document.getElementById("retry").addEventListener("click", () => void connect());
// Проверка могла и соврать: запрос к /api/health рубит расширению политика
// безопасности, а сам чат при этом работает. Пусть человек решит сам.
document.getElementById("anyway").addEventListener("click", () => show(base));

/**
 * Origin страницы, в которую нас вставили.
 *
 * Спросить напрямую нельзя: родитель кросс-доменный. Chrome отдаёт цепочку в
 * `ancestorOrigins`, Firefox её не знает — там origin узнаётся из первого же
 * сообщения родителя. Пока он неизвестен, шлём по очереди на оба адреса
 * портала: браузер доставит сообщение только тому окну, чей origin совпал с
 * указанным, остальные молча отбросит. Так `"*"` не нужен ни разу — иначе
 * содержимое диалога уезжало бы любому сайту, который сумел бы нас встроить.
 */
let parentOrigin = "";
try {
  const ancestor = location.ancestorOrigins && location.ancestorOrigins[0];
  if (VK.lib.portalOrigins.includes(ancestor)) parentOrigin = ancestor;
} catch { /* Firefox не знает ancestorOrigins — узнаем из сообщения */ }

function toParent(data) {
  if (window.parent === window) return;
  for (const target of parentOrigin ? [parentOrigin] : VK.lib.portalOrigins) {
    window.parent.postMessage(data, target);
  }
}

window.addEventListener("message", (event) => {
  const data = event.data;
  if (!data || data.source !== "valerchik") return;
  if (event.source === widget.contentWindow) {
    // тема нужна и нам: под фреймом видна наша подложка
    if (data.type === "theme" && (data.theme === "light" || data.theme === "dark")) {
      document.documentElement.dataset.theme = data.theme;
    }
    toParent(data);
  } else if (event.source === window.parent && widget.contentWindow) {
    // Родитель обязан быть порталом: страницу расширения встраивает наш
    // контент-скрипт и только там. Пришло с другого адреса — это чужой сайт
    // разговаривает с чатом за человека, и слушать его нечего.
    if (!VK.lib.portalOrigins.includes(event.origin)) return;
    parentOrigin = event.origin;
    // виджет — страница этого же расширения, адресуем ему точно
    widget.contentWindow.postMessage(data, location.origin);
  }
});
