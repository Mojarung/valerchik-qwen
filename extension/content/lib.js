/**
 * Общие проверки расширения: куда можно уходить, какому состоянию верить и что
 * считать подписью элемента.
 *
 * Всё это решения о безопасности и приватности, а на экране их не видно:
 * переход по чужому адресу выглядит как обычный переход, протухшее состояние —
 * как рабочее, введённый ИНН в записи сценария — как подпись кнопки. Поэтому
 * они собраны здесь чистыми функциями, без DOM и без сети, и проверяются
 * тестами (`extension/tests/lib.test.mjs`).
 *
 * Файл идёт первым в списке контент-скриптов манифеста и подключается в
 * chat.html: остальные части расширения берут его из `VK.lib`. Тесты грузят
 * его импортом ради того же побочного эффекта — отдельного экспорта нет
 * намеренно, иначе браузерная и тестовая сборки разошлись бы.
 */
(() => {
  const VK = (globalThis.VK ||= {});

  /** Адреса портала: только с них расширение принимает сообщения и только на них отвечает. */
  const portalOrigins = ["https://zakupki.mos.ru", "https://old.zakupki.mos.ru"];

  /** Столько живёт начатая подсказка. Дольше — человек уже занят другим. */
  const STATE_TTL_MS = 30 * 60 * 1000;

  /**
   * Поля состояния подсказки. Всё, чего тут нет, — чужое: старый формат хранил
   * в sessionStorage сценарий целиком, и подменив его, страница портала могла
   * бы сама решать, куда вести человека и что ему показывать.
   */
  const STATE_FIELDS = ["guideId", "step", "ts", "navigated", "back"];

  /** Длиннее — это уже не подпись, а содержимое страницы. */
  const MAX_LABEL = 60;

  /**
   * Абсолютный адрес перехода — или `null`, если уходить туда нельзя.
   *
   * Адрес шага приходит из сценария, а сценарий администратор пишет руками и
   * дописывает нейросетью. Поэтому он проверяется как чужой ввод: `//evil.tld`
   * без схемы разворачивается в другой хост, `http://` на том же хосте — это
   * понижение схемы, `javascript:` — вообще не переход. Пускаем только тот же
   * origin и только по https.
   */
  function safeDestination(destination, currentOrigin) {
    const raw = String(destination ?? "").trim();
    if (!raw) return null;
    let here;
    try {
      here = new URL(String(currentOrigin ?? ""));
    } catch {
      return null;
    }
    if (here.protocol !== "https:") return null;
    let target;
    try {
      target = new URL(raw, here);
    } catch {
      return null;
    }
    if (target.protocol !== "https:" || target.origin !== here.origin) return null;
    return target.href;
  }

  /**
   * Годится ли сохранённое состояние подсказки.
   *
   * `now` передаётся снаружи, чтобы проверка не зависела от часов: так её
   * можно прогнать тестом, и так же её видит движок в момент восстановления.
   * Метка из будущего — не «ещё свежее», а сбитые часы или чужая запись:
   * такому состоянию верить нельзя.
   */
  function isValidGuideState(state, now) {
    if (!state || typeof state !== "object" || Array.isArray(state)) return false;
    if (Object.keys(state).some((field) => !STATE_FIELDS.includes(field))) return false;
    if (typeof state.guideId !== "string" || !state.guideId) return false;
    if (!Number.isInteger(state.step) || state.step < 0) return false;
    if (typeof state.ts !== "number" || !Number.isFinite(state.ts)) return false;
    if (typeof state.navigated !== "boolean") return false;
    if ("back" in state && typeof state.back !== "boolean") return false;
    const at = typeof now === "number" && Number.isFinite(now) ? now : Date.now();
    const age = at - state.ts;
    return age >= 0 && age <= STATE_TTL_MS;
  }

  const norm = (value) => String(value ?? "").replace(/\s+/g, " ").trim();

  /** Подпись привязанного label: у полей портала текст живёт именно там. */
  function labelText(el) {
    try {
      const label = (el.labels && el.labels[0])
        || (typeof el.closest === "function" ? el.closest("label") : null);
      if (!label) return "";
      return label.innerText || label.textContent || "";
    } catch {
      return "";
    }
  }

  /**
   * Подпись элемента — то, что портал написал на нём сам.
   *
   * `el.value` не читаем никогда, и это главное здесь: запись сценария идёт по
   * живому личному кабинету, а в полях там ИНН, номера заявок и телефоны. Такое
   * значение уезжало бы в черновик сценария, оттуда в нейросеть и в базу — при
   * том что для локатора оно бесполезно: у следующего человека в поле своё.
   */
  function ownText(el) {
    if (!el || typeof el !== "object") return "";
    const attr = (name) => {
      try {
        return typeof el.getAttribute === "function" ? el.getAttribute(name) : "";
      } catch {
        return "";
      }
    };
    const candidates = [el.innerText, attr("aria-label"), attr("placeholder"), labelText(el)];
    for (const candidate of candidates) {
      const text = norm(candidate);
      if (!text) continue;
      return text.length > MAX_LABEL ? "" : text;
    }
    return "";
  }

  VK.lib = { portalOrigins, STATE_TTL_MS, safeDestination, isValidGuideState, ownText };
})();
