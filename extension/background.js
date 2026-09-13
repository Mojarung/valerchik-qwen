/**
 * Фоновый скрипт расширения: кнопка на панели браузера и открытие черновика
 * записанного сценария в админке.
 *
 * На портале нажатие открывает или сворачивает чат (сообщение контент-скрипту),
 * на любой другой вкладке — открывает портал: помощник живёт только там.
 *
 * Файл объявлен в манифесте и как service_worker (Chrome), и как background
 * script (Firefox): каждый браузер берёт своё, код один и тот же.
 */
const api = globalThis.browser ?? globalThis.chrome;
const PORTAL = "https://zakupki.mos.ru/";
// Боевой сервер по умолчанию: загруженное расширение сразу работает, без
// похода в настройки. Локальный стенд — адресом в настройках расширения.
const DEFAULTS = { serverUrl: "https://201-24-127-126.sslip.io" };

api.action.onClicked.addListener((tab) => {
  const onPortal = typeof tab?.url === "string" && /^https:\/\/(old\.)?zakupki\.mos\.ru\//.test(tab.url);
  if (!onPortal || tab.id === undefined) {
    api.tabs.create({ url: PORTAL });
    return;
  }
  // Ответ не нужен; в Chrome sendMessage без получателя отклоняет промис —
  // это тот случай, когда страница ещё не дозагрузилась, и делать тут нечего.
  Promise.resolve(api.tabs.sendMessage(tab.id, { source: "valerchik", type: "toggle-chat" }))
    .catch(() => api.tabs.reload(tab.id));
});

/**
 * Записанный на портале черновик уходит в админку через адресную строку, в
 * якорь: сервер якорь не видит и в логи не пишет, а страница админки уже
 * открыта под правами администратора — сохранит он сам, своим токеном.
 * Расширению админский токен не нужен и не должен быть нужен.
 */
api.runtime.onMessage.addListener((message) => {
  if (message?.source !== "valerchik" || message.type !== "open-editor") return;
  const draft = JSON.stringify(message.draft || {});
  const encoded = btoa(String.fromCharCode(...new TextEncoder().encode(draft)));
  Promise.resolve(api.storage.sync.get(DEFAULTS)).then((items) => {
    const base = String((items || DEFAULTS).serverUrl || DEFAULTS.serverUrl).replace(/\/+$/, "");
    api.tabs.create({ url: `${base}/admin/guides#draft=${encodeURIComponent(encoded)}` });
  });
});

/** Сценарии называются так же, как в каталоге: латиница, цифры, дефис. */
const GUIDE_ID = /^[a-z0-9][a-z0-9_-]{0,63}$/i;

/**
 * Сценарий по идентификатору — для подсказки, которая продолжается после
 * перехода на другую страницу портала.
 *
 * Ходит за ним фон, а не контент-скрипт: страница портала открыта по https, и
 * обращение оттуда к localhost браузер блокирует сам (Local Network Access).
 * Заодно так адрес сервера остаётся делом расширения — странице портала его
 * знать незачем.
 *
 * Наружу отдаём только `{guide}` или `{error}`: контент-скрипт не разбирает
 * коды ответа, на любой неудаче он просто забывает состояние подсказки.
 */
api.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  if (message?.source !== "valerchik" || message.type !== "fetch-guide") return false;
  const id = String(message.id ?? "");
  // id уезжает в путь запроса — пускаем только то, чем сценарии называются
  if (!GUIDE_ID.test(id)) {
    sendResponse({ error: "Недопустимый идентификатор сценария" });
    return false;
  }
  Promise.resolve(api.storage.sync.get(DEFAULTS))
    .then((items) => {
      const base = String((items || DEFAULTS).serverUrl || DEFAULTS.serverUrl).replace(/\/+$/, "");
      return fetch(`${base}/api/guides/${encodeURIComponent(id)}`, { cache: "no-store" });
    })
    .then((resp) => (resp.ok ? resp.json() : Promise.reject(new Error(`сервер ответил ${resp.status}`))))
    .then((guide) => sendResponse({ guide }))
    .catch((error) => sendResponse({ error: String(error?.message || error) }));
  // ответ уйдёт позже — просим браузер не закрывать канал
  return true;
});
