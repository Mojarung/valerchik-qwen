/**
 * Точка входа контент-скрипта: собирает Shadow DOM, чат и движок подсказок.
 *
 * Порядок файлов задан манифестом: styles → mascot → guide → chat → main; все
 * они складывают свои части в один объект VK, потому что контент-скрипты одной
 * записи манифеста делят область видимости.
 */
(() => {
  const VK = (globalThis.VK ||= {});
  const api = globalThis.browser ?? globalThis.chrome;
  const DEFAULTS = { serverUrl: "https://194-31-175-172.sslip.io", side: "right", editorMode: false };

  // Страница могла вставить нас дважды (переустановка расширения без перезагрузки)
  if (document.getElementById("valerchik-extension")) return;

  function readSettings() {
    return new Promise((resolve) => {
      try {
        // Chrome отдаёт результат в колбэк, Firefox — промисом; принимаем любой
        const maybe = api.storage.sync.get(DEFAULTS, (items) => resolve({ ...DEFAULTS, ...(items || {}) }));
        if (maybe && typeof maybe.then === "function") {
          maybe.then((items) => resolve({ ...DEFAULTS, ...(items || {}) }));
        }
      } catch {
        resolve(DEFAULTS);
      }
    });
  }

  /**
   * Тема оформления приходит из чата: там её выбирает человек, а рисуем в этой
   * теме мы — кнопку, карточки подсказок, пузырь маскота. Запоминаем в
   * storage.local, потому что подсказка живёт дольше открытого чата: человек
   * переходит по страницам портала, чат при этом свёрнут и ничего не пришлёт.
   * Пусто — тема системная, как и было.
   */
  function readTheme() {
    return new Promise((resolve) => {
      try {
        const maybe = api.storage.local.get({ theme: "" }, (items) => resolve((items || {}).theme || ""));
        if (maybe && typeof maybe.then === "function") maybe.then((items) => resolve((items || {}).theme || ""));
      } catch {
        resolve("");
      }
    });
  }

  async function init() {
    const settings = await readSettings();
    // Адрес сервера читает сама страница chat.html; здесь нужен только адрес
    // этой страницы внутри расширения
    const chatUrl = api.runtime.getURL("chat.html");

    const theme = await readTheme();

    const host = document.createElement("div");
    host.id = "valerchik-extension";
    // color-scheme — здесь, а не в таблице стилей: `all: initial` отрезает нас от
    // правил портала, но заодно сбрасывает и color-scheme, а inline-объявление
    // сильнее любого правила из :host. Без него light-dark() в стилях всегда
    // отдавала бы светлый вариант, и кнопка оставалась бы белой в тёмной теме.
    host.style.cssText = "all: initial; color-scheme: light dark;"
      + " position: fixed; inset: 0 auto auto 0; width: 0; height: 0; z-index: 2147483000;";
    /** Тема хоста: color-scheme решает, какой вариант вернёт light-dark(). */
    const setTheme = (next) => {
      if (next !== "light" && next !== "dark") return;
      host.dataset.theme = next;
      host.style.colorScheme = next;
    };
    setTheme(theme);
    const root = host.attachShadow({ mode: "open" });
    const style = document.createElement("style");
    style.textContent = VK.css;
    root.appendChild(style);
    document.documentElement.appendChild(host);

    const guide = new VK.GuideEngine(root);
    const chat = VK.createChat({
      root, chatUrl, side: settings.side === "left" ? "left" : "right",
      onGuide: (g) => guide.start(g),
      onTheme: (next) => {
        setTheme(next);
        try { api.storage.local.set({ theme: next }); } catch { /* переживём одну страницу */ }
      },
    });
    VK.chatButtonPosition = chat.buttonPosition;
    VK.onGuideStop = () => { /* чат остаётся свёрнутым: человек вернётся к нему сам */ };
    VK.chat = chat;
    VK.guide = guide;

    // Подсказка началась на прошлой странице — продолжаем с того же шага
    guide.resume();

    // Режим редактора: админ записывает сценарий, обходя портал кликами.
    // Обычному человеку эта кнопка не нужна и не показывается.
    if (settings.editorMode) {
      const recorder = new VK.Recorder(root);
      VK.recorder = recorder;
      const record = document.createElement("button");
      record.type = "button";
      record.className = `vk-recbtn vk-recbtn--${settings.side === "left" ? "left" : "right"}`;
      record.innerHTML = '<span class="vk-recbtn__dot"></span>Записать сценарий';
      record.onclick = () => {
        record.style.display = "none";
        recorder.start();
      };
      // Запись закончилась или отменена — кнопка возвращается, иначе следующую
      // запись нельзя было начать без перезагрузки страницы
      VK.onRecorderStop = () => { record.style.display = ""; };
      root.appendChild(record);
      await recorder.resume();
      if (recorder.active) record.style.display = "none";
    }

    // Кнопка на панели браузера: открыть или свернуть чат
    try {
      api.runtime.onMessage.addListener((message, _sender, sendResponse) => {
        if (message?.source === "valerchik" && message.type === "toggle-chat") {
          chat.toggle();
          sendResponse?.({ ok: true });
        }
        return false;
      });
    } catch { /* без фонового скрипта кнопка не работает, чат — работает */ }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", () => void init(), { once: true });
  } else {
    void init();
  }
})();
