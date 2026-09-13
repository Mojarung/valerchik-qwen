/**
 * Запись сценария прямо с портала.
 *
 * Селекторы — самая дорогая часть сценария: писать их руками по инспектору
 * долго, а нейросеть, не видя DOM, их выдумывает. Поэтому админ включает
 * запись и просто идёт по порталу так, как пошёл бы человек: каждый клик
 * превращается в шаг с локатором, а переход между страницами — в поле «go».
 * Тексты потом дописываются в админке (или нейросетью), но привязка к
 * интерфейсу получается настоящей.
 *
 * Состояние живёт в storage.local, а не в памяти: клик по кнопке уводит на
 * другую страницу, контент-скрипт умирает и поднимается заново, и запись
 * обязана это пережить.
 */
(() => {
  const VK = (globalThis.VK ||= {});
  const api = globalThis.browser ?? globalThis.chrome;
  const KEY = "recorder";
  /** Дольше — уже не переход по нажатой кнопке, а самостоятельная навигация. */
  const NAV_WINDOW_MS = 15000;
  const MAX_STEPS = 12;

  // ---- хранилище записи ---------------------------------------------------

  function read() {
    return new Promise((resolve) => {
      try {
        const maybe = api.storage.local.get({ [KEY]: null }, (items) => resolve((items || {})[KEY]));
        if (maybe && typeof maybe.then === "function") maybe.then((items) => resolve((items || {})[KEY]));
      } catch {
        resolve(null);
      }
    });
  }

  function write(state) {
    try {
      api.storage.local.set({ [KEY]: state });
    } catch { /* запись сценария — не то, ради чего стоит ронять страницу */ }
  }

  // ---- локаторы -----------------------------------------------------------

  /** Имя styled-components из хэшированного класса: LightButton__Primary-sc-1ab → LightButton__Primary. */
  function styledName(className) {
    const match = /^([A-Za-z][A-Za-z0-9_]*(?:__[A-Za-z0-9_]+)?)-sc-/.exec(className);
    return match ? match[1] : "";
  }

  const CLICKABLE = "a, button, [role=button], input, select, label, [data-testid]";

  /** Элемент, который на самом деле нажали: клик часто приходит по вложенному span. */
  function actualTarget(node) {
    if (!(node instanceof Element)) return null;
    return node.closest(CLICKABLE) || node;
  }

  /**
   * Подпись элемента для локатора — только то, что написал портал.
   *
   * Раньше сюда попадало и `el.value`: запись идёт по живому личному кабинету,
   * и в черновик сценария уезжали ИНН, номера заявок и телефоны того, кто
   * записывал, — а оттуда в нейросеть и в базу. Проверка вынесена в общий
   * lib.js и закрыта тестом, потому что на экране такая утечка не видна.
   */
  const ownText = (el) => VK.lib.ownText(el);

  /**
   * Приведёт ли локатор ровно к этой цели — спрашиваем сам движок.
   *
   * Годится и попадание в обёртку: у кнопок портала data-testid висит на
   * контейнере, а клик приходит по <span> внутри. Это одна и та же цель на
   * экране, и подсвечивать правильнее контейнер — но только если он не больше
   * нажатого: совпадение подписи это и проверяет.
   */
  function resolvesTo(loc, el) {
    try {
      const found = VK.locatorTargets ? VK.locatorTargets(loc) : [];
      if (found.length !== 1) return false;
      const hit = found[0];
      if (hit === el) return true;
      const same = (a, b) => String(a || "").replace(/\s+/g, " ").trim()
        === String(b || "").replace(/\s+/g, " ").trim();
      return hit.contains(el) && same(hit.innerText, el.innerText);
    } catch {
      return false;
    }
  }

  /**
   * Путь из тегов до ближайшего опознаваемого предка — последняя надежда,
   * когда у элемента нет ни testid, ни своих классов, ни текста.
   */
  function structuralCss(el) {
    const parts = [];
    let node = el;
    for (let depth = 0; node && node.parentElement && depth < 6; depth += 1) {
      const testid = node.getAttribute("data-testid");
      if (testid) {
        parts.unshift(`[data-testid="${testid}"]`);
        return parts.join(" > ");
      }
      const parent = node.parentElement;
      const index = [...parent.children].filter((c) => c.tagName === node.tagName).indexOf(node) + 1;
      parts.unshift(`${node.tagName.toLowerCase()}:nth-of-type(${index})`);
      node = parent;
    }
    return parts.length ? parts.join(" > ") : "";
  }

  /**
   * До трёх способов найти элемент, от самого устойчивого к самому понятному.
   * Порядок важен: движок пробует их сверху вниз, поэтому data-testid идёт
   * раньше текста, который меняют копирайтеры.
   *
   * Каждый локатор проверяем движком: если под него попадает не один элемент,
   * а несколько, вести по нему нельзя. Так и вышло со сменой города — селектор
   * по классу «LightButton» совпадал и с «Зарегистрироваться», и с «Выбрать
   * другой», и подсказка показывала на первую попавшуюся кнопку. Неоднозначный
   * селектор сужаем текстом кнопки, а если и это не помогло — выбрасываем.
   */
  function locatorsFor(el) {
    const text = ownText(el);
    const drafts = [];

    const tagged = el.closest("[data-testid]");
    if (tagged) drafts.push({ css: `[data-testid="${tagged.getAttribute("data-testid")}"]` });

    if (el.id && !/\d{3,}|:r[a-z0-9]+:/i.test(el.id)) drafts.push({ css: `#${CSS.escape(el.id)}` });

    const names = [...el.classList].map(styledName).filter(Boolean);
    if (names.length) {
      // сперва все имена сразу — так селектор конкретнее, потом самое длинное
      names.sort((a, b) => b.length - a.length);
      if (names.length > 1) drafts.push({ css: names.map((n) => `[class*="${n}"]`).join("") });
      drafts.push({ css: `[class*="${names[0]}"]` });
    }

    if (el.tagName === "A" && el.getAttribute("href")) {
      try {
        const url = new URL(el.getAttribute("href"), location.origin);
        if (url.origin === location.origin) drafts.push({ href: url.pathname });
      } catch { /* «#» и javascript: в локатор не годятся */ }
    }

    if (text) drafts.push({ text, in: el.tagName.toLowerCase() });

    const out = [];
    for (const loc of drafts) {
      if (resolvesTo(loc, el)) {
        out.push(loc);
      } else if (loc.css && text && resolvesTo({ css: loc.css, text }, el)) {
        out.push({ css: loc.css, text });
      }
      if (out.length >= 3) break;
    }
    if (!out.length) {
      const css = structuralCss(el);
      if (css && resolvesTo({ css }, el)) out.push({ css });
      else if (css) out.push(text ? { css, text } : { css });
    }
    return out;
  }

  function stepTitle(el) {
    const text = ownText(el);
    if (!text) return "Шаг без подписи";
    // Портал делает кнопки и из div: тег не показатель, а курсор-палец — да
    const clickable = el.tagName === "A" || el.tagName === "BUTTON"
      || el.getAttribute("role") === "button"
      || getComputedStyle(el).cursor === "pointer";
    if (clickable) return `Нажмите «${text}»`;
    if (el.tagName === "INPUT" || el.tagName === "SELECT") return `Заполните «${text}»`;
    return `Откройте «${text}»`;
  }

  // ---- панель записи ------------------------------------------------------

  class Recorder {
    constructor(root) {
      this.root = root;
      this.state = null;
      this.panel = null;
      this.onClick = this.handleClick.bind(this);
    }

    /** Поднять запись после перехода на другую страницу. */
    async resume() {
      const state = await read();
      if (!state || !state.on) return;
      // Клик увёл на другую страницу. Записывать это в «go» прошлого шага
      // нельзя: движок читает go как «куда идти, чтобы попасть на страницу
      // ЭТОГО шага», и такой сценарий уводил человека вперёд вместо нужной
      // страницы. Переход и так виден по адресу следующего шага, а здесь
      // остаётся только пометка, что шаг выполняется нажатием.
      const last = state.steps[state.steps.length - 1];
      const here = location.pathname;
      if (last && last.url !== here && Date.now() - (state.at || 0) < NAV_WINDOW_MS) {
        last.action = last.action === "input" ? "input" : "click";
        last.leadsTo = here;   // только для панели записи, в сценарий не уходит
      }
      this.state = state;
      write(state);
      this.mount();
    }

    start() {
      this.state = { on: true, steps: [], at: Date.now() };
      write(this.state);
      this.mount();
    }

    mount() {
      document.addEventListener("click", this.onClick, true);
      window.addEventListener("valerchik:navigate", () => this.render());
      this.render();
    }

    handleClick(event) {
      if (!this.state?.on) return;
      // свои же кнопки не пишем: панель записи живёт в теневом дереве
      if (event.target instanceof Element && event.target.closest("#valerchik-extension")) return;
      const el = actualTarget(event.target);
      if (!el) return;
      const target = locatorsFor(el);
      if (!target.length) return;
      if (this.state.steps.length >= MAX_STEPS) {
        this.render("Больше двенадцати шагов в сценарий не поместится");
        return;
      }
      this.state.steps.push({
        url: location.pathname,
        title: stepTitle(el),
        text: "",
        target,
        // Поле, галочку и подпись к ним человек заполняет сам: «Далее» на таком
        // шаге нажало бы их за него — то есть отправило бы форму или
        // переключило значение. Такие шаги движок только показывает.
        action: el.matches("input, select, textarea, label") ? "input" : "click",
      });
      this.state.at = Date.now();
      write(this.state);
      this.render();
      // клик не перехватываем: человек должен идти по порталу как обычно,
      // а переход после нажатия — это и есть поле go следующего шага
    }

    render(note = "") {
      if (!this.panel) {
        this.panel = document.createElement("div");
        this.panel.className = "vk-rec";
        this.root.appendChild(this.panel);
      }
      const steps = this.state?.steps || [];
      const shown = steps.slice(-3);
      this.panel.innerHTML = `
        <div class="vk-rec__head">
          <span class="vk-rec__dot"></span>
          <span class="vk-rec__title">Запись сценария</span>
          <span class="vk-rec__count">${steps.length} из ${MAX_STEPS}</span>
        </div>
        <ol class="vk-rec__list">
          ${shown.length
            ? shown.map((s) => `<li>${escapeHtml(s.title)}${s.leadsTo ? ` <em>→ ${escapeHtml(s.leadsTo)}</em>` : ""}</li>`).join("")
            : "<li class=\"vk-rec__empty\">Нажимайте по порталу так, как показали бы человеку</li>"}
        </ol>
        ${note ? `<p class="vk-rec__note">${escapeHtml(note)}</p>` : ""}
        <div class="vk-rec__actions">
          <button type="button" class="vk-btn2 vk-btn2--ghost" data-act="undo" ${steps.length ? "" : "disabled"}>Убрать шаг</button>
          <button type="button" class="vk-btn2 vk-btn2--ghost" data-act="cancel">Отмена</button>
          <button type="button" class="vk-btn2 vk-btn2--primary" data-act="done" ${steps.length ? "" : "disabled"}>Готово</button>
        </div>`;
      this.panel.querySelector('[data-act="undo"]').onclick = () => {
        this.state.steps.pop();
        write(this.state);
        this.render();
      };
      this.panel.querySelector('[data-act="cancel"]').onclick = () => this.stop();
      this.panel.querySelector('[data-act="done"]').onclick = () => this.finish();
    }

    finish() {
      const steps = this.state?.steps || [];
      this.stop();
      if (!steps.length) return;
      // Черновик уходит в админку: там его дополнят заголовком, вопросами и
      // текстами — и там же сохранят под правами администратора. Токен админа
      // расширению не нужен и не должен быть нужен.
      try {
        api.runtime.sendMessage({
          source: "valerchik", type: "open-editor",
          draft: {
            audience: "all",
            // leadsTo — подсказка для панели записи, в сценарии такого поля нет
            steps: steps.map(({ leadsTo, ...step }) => step),
          },
        });
      } catch { /* фонового скрипта нет — запись просто не откроется в админке */ }
    }

    stop() {
      VK.onRecorderStop?.();
      document.removeEventListener("click", this.onClick, true);
      this.state = null;
      write(null);
      this.panel?.remove();
      this.panel = null;
    }

    get active() {
      return Boolean(this.state?.on);
    }
  }

  function escapeHtml(value) {
    return String(value).replace(/[&<>"]/g, (c) => (
      { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]
    ));
  }

  VK.Recorder = Recorder;
})();
