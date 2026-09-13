/**
 * Движок пошаговых подсказок: Валерчик ходит по странице портала и показывает,
 * куда нажимать.
 *
 * Сценарий приходит из ответа чата (backend/app/guides/portal.json): список
 * шагов, у каждого — адрес страницы, локаторы элемента по убыванию надёжности,
 * заголовок и пояснение. Движок находит элемент, вырезает его из затемнения,
 * ставит рядом карточку с текстом, а маскот «пробегает» к ней Г-образным
 * путём — как ремонтник раскладки в чате.
 *
 * Никакого распознавания экрана: страница разобрана заранее, селекторы сняты с
 * живого портала, и всё происходит за миллисекунды. Цена — сценарии надо
 * обновлять, когда портал меняет вёрстку; зато работает мгновенно и без сети.
 *
 * Шаги переживают переходы между страницами: состояние лежит в sessionStorage,
 * и после загрузки новой страницы движок продолжает с того места. Портал —
 * SPA, поэтому смену адреса ловим и у history тоже.
 */
(() => {
  const VK = (globalThis.VK ||= {});
  const api = globalThis.browser ?? globalThis.chrome;

  const STATE_KEY = "valerchik-guide";
  /** Сколько ждём сценарий от фонового скрипта, прежде чем сдаться. */
  const FETCH_TIMEOUT_MS = 6000;
  // Сколько ждём элемент шага. Портал на медленной сети дорисовывает кнопки
  // секундами после загрузки, и за прежние 8 с подсказка успевала сказать «не
  // вижу этот элемент» про кнопку, которая появлялась следом. Необязательный
  // шаг (его страницу портал может и не показать) ждём меньше, но тоже с
  // запасом: иначе его пропускали, пока страница ещё грузилась.
  const FIND_TIMEOUT_MS = 20000;
  const FIND_TIMEOUT_OPTIONAL_MS = 6000;
  const POLL_MS = 200;
  const CARD_WIDTH = 340;
  const MASCOT_SIZE = 64;
  const GAP = 14;

  const norm = (s) => String(s || "").replace(/\s+/g, " ").trim().toLowerCase();
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const reduced = () => window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  // ---- поиск элемента --------------------------------------------------

  function isVisible(el) {
    if (!(el instanceof Element)) return false;
    const r = el.getBoundingClientRect();
    if (r.width < 2 || r.height < 2) return false;
    const s = getComputedStyle(el);
    return s.visibility !== "hidden" && s.display !== "none" && s.opacity !== "0";
  }

  /**
   * Портал сам выключает управление, которое пока нажимать нельзя: «Выбрать
   * электронную подпись» неактивна без плагина КриптоПро, кнопка отправки — до
   * заполнения формы. Подсвечивать такое как «нажмите сюда» — врать человеку,
   * поэтому среди целей шага выбираем сперва живую, а на выключенной честно
   * пишем, что портал её не даёт.
   */
  function isDisabled(el) {
    if (!(el instanceof Element)) return true;
    if (el.disabled === true) return true;
    if (el.getAttribute("aria-disabled") === "true") return true;
    if (el.classList.contains("disabled")) return true;
    // pointer-events: none выключает нажатие, но у обычной вёрстки так помечают
    // и декоративные обёртки — принимаем это за «нельзя» только у управления
    const control = el.matches("a, button, input, select, textarea, [role=button]");
    return control && getComputedStyle(el).pointerEvents === "none";
  }

  const isUsable = (el) => isVisible(el) && !isDisabled(el);

  /** Из списка кандидатов — первый работающий, иначе просто видимый. */
  const pick = (pool) => pool.find(isUsable) || pool.find(isVisible) || null;

  const TEXT_POOL = "a, button, [role=button], h1, h2, h3, h4, label, span, div, p, li, td, th";

  /**
   * Разные элементы среди совпавших: вложенные выкидываем.
   *
   * Текстовый локатор почти всегда ловит и кнопку, и <span> внутри неё — это
   * одна и та же цель на экране, а не выбор. А вот два разных элемента с одним
   * классом — это уже неоднозначность, и вести по такому локатору нельзя.
   */
  function distinct(list) {
    return list.filter((el, i) => !list.some((other, j) => j !== i && el.contains(other)));
  }

  /** Все видимые кандидаты по локатору, в порядке предпочтения. */
  function candidatesFor(loc) {
    let pool;
    if (loc.href) {
      pool = [...document.querySelectorAll(`a[href^="${CSS.escape(loc.href)}"], a[href^="${location.origin}${loc.href}"]`)];
      return pool.filter(isVisible);
    }
    if (loc.css) {
      try { pool = [...document.querySelectorAll(loc.css)]; } catch { return []; }
      if (!loc.text) return pool.filter(isVisible);
    } else {
      const tags = loc.in && loc.in !== "*" ? loc.in : TEXT_POOL;
      pool = [...document.querySelectorAll(tags)];
    }
    const want = norm(loc.text);
    if (!want) return [];
    // Среди совпавших берём самый короткий по тексту: это самый глубокий узел,
    // а не обёртка на полстраницы, внутри которой это слово тоже есть
    const hits = pool.filter((el) => {
      if (!isVisible(el)) return false;
      const t = norm(el.innerText);
      return loc.contains ? t.includes(want) : t === want;
    });
    hits.sort((a, b) => norm(a.innerText).length - norm(b.innerText).length);
    return hits;
  }

  /** Элемент по одному локатору — тот же выбор, что делает движок на шаге. */
  function findByLocator(loc) {
    return pick(candidatesFor(loc));
  }

  async function findTarget(step, signal) {
    const timeout = step.optional ? FIND_TIMEOUT_OPTIONAL_MS : FIND_TIMEOUT_MS;
    const deadline = performance.now() + timeout;
    while (!signal.aborted) {
      // За один проход собираем все цели шага и предпочитаем ту, что реально
      // нажимается: у шага «способ регистрации» их две, и без плагина живой
      // остаётся только «Регистрация без ЭП» — на неё и нужно показывать.
      // Ждать, пока выключенная оживёт, нельзя: кнопка отправки формы включится
      // только после заполнения, а показать её надо сейчас.
      let disabled = null;
      let ambiguous = null;
      for (const loc of step.target || []) {
        const found = distinct(candidatesFor(loc));
        if (!found.length) continue;
        // Селектор, под который попало несколько разных элементов, ведёт наугад:
        // «[class*="LightButton"]» на портале — это и «Зарегистрироваться», и
        // «Выбрать другой». Такой локатор пропускаем и пробуем следующий, обычно
        // это текст кнопки. Если однозначного нет ни одного, вернёмся к нему.
        if (found.length > 1) {
          ambiguous = ambiguous || pick(found);
          continue;
        }
        if (!isDisabled(found[0])) return found[0];
        disabled = disabled || found[0];
      }
      if (disabled || ambiguous) return disabled || ambiguous;
      if (performance.now() > deadline) return null;
      await sleep(POLL_MS);
    }
    return null;
  }

  function pathMatches(url) {
    if (!url) return true;
    const path = location.pathname.replace(/\/+$/, "") || "/";
    const want = url.replace(/\/+$/, "") || "/";
    if (want === "/") return path === "/" || /^\/[a-z]{2}$/.test(path);
    return path === want || path.startsWith(want.endsWith("/") ? want : `${want}/`) || path.startsWith(url);
  }

  // ---- состояние ---------------------------------------------------------

  /**
   * Сохранённое место в сценарии — или `null`, если верить ему нельзя.
   *
   * sessionStorage принадлежит странице портала: писать туда может и сам
   * портал, и любой его скрипт. Раньше там лежал сценарий целиком, и подменив
   * запись, страница указывала бы движку, куда вести человека и что ему
   * говорить от нашего имени. Теперь оттуда берётся только ссылка на сценарий
   * — id и номер шага, — а сам сценарий приезжает с нашего сервера.
   */
  function loadState() {
    try {
      const raw = sessionStorage.getItem(STATE_KEY);
      if (!raw) return null;
      const state = JSON.parse(raw);
      if (!VK.lib.isValidGuideState(state, Date.now())) {
        sessionStorage.removeItem(STATE_KEY);
        return null;
      }
      return state;
    } catch {
      return null;
    }
  }

  /** В хранилище уезжает только ссылка на сценарий: сам он живёт в памяти движка. */
  function saveState(state) {
    const slim = {
      guideId: String(state.guide?.id || ""),
      step: Number(state.step),
      ts: Date.now(),
      navigated: Boolean(state.navigated),
      back: Boolean(state.back),
    };
    // Сценарий без id восстановить неоткуда — тогда честнее не хранить ничего,
    // чем оставить запись, по которой движок поднимется пустым.
    if (!VK.lib.isValidGuideState(slim, slim.ts)) { clearState(); return; }
    try { sessionStorage.setItem(STATE_KEY, JSON.stringify(slim)); } catch { /* приватный режим */ }
  }

  /**
   * Сценарий по id — через фоновый скрипт.
   *
   * Со страницы портала (https) запрос к localhost браузер рубит сам —
   * Local Network Access; фоновому скрипту расширения этот запрет не писан.
   * Ответа может не быть вовсе (сервер не поднят, расширение обновилось на
   * ходу), поэтому ждём ограниченное время и продолжаем без сценария.
   */
  function fetchGuide(id) {
    return new Promise((resolve) => {
      let done = false;
      const finish = (guide) => {
        if (done) return;
        done = true;
        clearTimeout(timer);
        resolve(guide?.steps?.length ? guide : null);
      };
      const timer = setTimeout(() => finish(null), FETCH_TIMEOUT_MS);
      try {
        Promise.resolve(api.runtime.sendMessage({ source: "valerchik", type: "fetch-guide", id }))
          .then((reply) => finish(reply?.guide), () => finish(null));
      } catch {
        finish(null);
      }
    });
  }

  function clearState() {
    try { sessionStorage.removeItem(STATE_KEY); } catch { /* ничего */ }
  }

  // ---- разметка ----------------------------------------------------------

  const ICON_X = `<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M18 6 6 18M6 6l12 12"/></svg>`;
  const ICON_INFO = `<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" style="flex:none;margin-top:1px"><circle cx="12" cy="12" r="10"/><path d="M12 16v-4M12 8h.01"/></svg>`;

  class GuideEngine {
    constructor(root) {
      this.root = root;
      this.state = null;
      this.target = null;
      this.abort = null;
      this.mascotPos = null;
      this.onKey = this.onKey.bind(this);
      this.reposition = this.reposition.bind(this);
      this.onNavigate = this.onNavigate.bind(this);
      this.listeners = [];
    }

    // --- публичное ---

    start(guide) {
      if (!guide?.steps?.length) return;
      this.stop(false);
      this.state = { guide, step: 0, navigated: false };
      saveState(this.state);
      this.mount();
      void this.show();
    }

    /**
     * Продолжить подсказку после перехода на другую страницу портала.
     *
     * Сценарий берём заново с сервера по сохранённому id: страница между
     * шагами могла подменить хранилище, а сценарий с сервера — тот самый,
     * который показывали с самого начала. Не отдали — забываем состояние, иначе
     * движок висел бы на странице пустой карточкой.
     */
    async resume() {
      const state = loadState();
      if (!state) return false;
      const guide = await fetchGuide(state.guideId);
      if (!guide) { clearState(); return false; }
      this.state = {
        guide,
        // сценарий на сервере мог стать короче, чем был в начале обхода
        step: Math.min(state.step, guide.steps.length - 1),
        navigated: state.navigated,
        back: Boolean(state.back),
      };
      this.mount();
      void this.show();
      return true;
    }

    stop(fireEvent = true) {
      clearTimeout(this.byeTimer);
      this.abort?.abort();
      this.abort = null;
      clearState();
      this.unmount();
      this.state = null;
      this.target = null;
      if (fireEvent) VK.onGuideStop?.();
    }

    get active() { return Boolean(this.state); }

    // --- сборка слоя ---

    mount() {
      if (this.layer) return;
      const layer = document.createElement("div");
      layer.className = "vk-root vk-guide";
      layer.innerHTML = `
        <div class="vk-spot"></div>
        <div class="vk-mascot"><div class="vk-mascot__inner"><div class="vk-mascot__bob"></div></div></div>
        <div class="vk-bubble"></div>
        <div class="vk-card" role="dialog" aria-live="polite"></div>`;
      this.root.appendChild(layer);
      this.layer = layer;
      this.spot = layer.querySelector(".vk-spot");
      this.mascotEl = layer.querySelector(".vk-mascot");
      this.bubble = layer.querySelector(".vk-bubble");
      this.card = layer.querySelector(".vk-card");
      this.mascot = VK.createMascot(MASCOT_SIZE, "happy");
      layer.querySelector(".vk-mascot__bob").appendChild(this.mascot.el);
      // первое появление — из угла с кнопкой чата, как будто вышел из неё
      const start = VK.chatButtonPosition?.() || { x: window.innerWidth - 90, y: window.innerHeight - 90 };
      this.mascotPos = { x: start.x, y: start.y };
      this.mascotEl.style.transform = `translate(${start.x}px, ${start.y}px)`;

      window.addEventListener("keydown", this.onKey, true);
      window.addEventListener("resize", this.reposition, { passive: true });
      window.addEventListener("scroll", this.reposition, { passive: true, capture: true });
      window.addEventListener("popstate", this.onNavigate);
      window.addEventListener("valerchik:navigate", this.onNavigate);
    }

    unmount() {
      window.removeEventListener("keydown", this.onKey, true);
      window.removeEventListener("resize", this.reposition);
      window.removeEventListener("scroll", this.reposition, { capture: true });
      window.removeEventListener("popstate", this.onNavigate);
      window.removeEventListener("valerchik:navigate", this.onNavigate);
      this.detachTarget();
      this.mascot?.destroy();
      this.mascot = null;
      this.layer?.remove();
      this.layer = null;
    }

    // --- шаги ---

    get step() { return this.state?.guide.steps[this.state.step]; }
    get total() { return this.state?.guide.steps.length || 0; }

    async show() {
      const state = this.state;
      const step = this.step;
      if (!state || !step) return;
      this.abort?.abort();
      const abort = (this.abort = new AbortController());
      this.detachTarget();

      // Портал мог перескочить страницу: кнопка «Зарегистрироваться» ведёт
      // сразу на форму регистрации, минуя форму входа со ссылкой. Если текущая
      // страница — это страница одного из СЛЕДУЮЩИХ шагов, идём к нему, а не
      // тащим человека назад.
      if (step.url && !pathMatches(step.url) && !state.back) {
        const ahead = this.stepAhead(state.step);
        if (ahead !== null) {
          state.step = ahead;
          state.navigated = false;
          saveState(state);
          void this.show();
          return;
        }
      }

      // Шаг на другой странице: переходим и продолжаем после загрузки. Куда
      // именно — `go` шага, если задан (адрес `url` бывает лишь образцом для
      // сравнения: у страниц Keycloak он не открывается напрямую). Второй раз
      // на тот же шаг не переходим — портал мог перенаправить на вход, и тогда
      // честнее сказать об этом, чем бегать по кругу.
      if (step.url && !pathMatches(step.url)) {
        const destination = step.go === undefined ? step.url : step.go;
        // Переход на страницу, где мы и так находимся, — это лишняя перезагрузка
        // и следом извинение «не удалось открыть». Последний шаг сценария входа
        // именно так и заканчивался: человек вошёл, попал в кабинет, и движок
        // отправлял его в тот же кабинет заново.
        const here = location.pathname.replace(/\/+$/, "") || "/";
        const target = String(destination || "").replace(/\/+$/, "") || "/";
        // Адрес шага — чужой ввод: сценарии пишет админка, дописывает нейросеть.
        // Уводить человека с портала расширение не имеет права ни при какой
        // опечатке, поэтому за пределы текущего origin переход не выпускаем.
        const href = VK.lib.safeDestination(destination, location.origin);
        if (!state.navigated && href && target !== here) {
          state.navigated = true;
          saveState(state);
          this.renderCard({ step, note: "Перехожу на нужную страницу…", searching: true });
          this.mascot.setMood("happy");
          await sleep(reduced() ? 0 : 650);
          if (abort.signal.aborted) return;
          location.assign(href);
          return;
        }
        const needsLogin = location.pathname.startsWith("/auth/");
        this.renderCard({
          step, notFound: true,
          note: needsLogin
            ? "Для этого шага нужно войти в личный кабинет. Войдите — и я продолжу."
            : destination
              ? "Не удалось открыть нужную страницу. Откройте её сами — я продолжу."
              : "Этот шаг открывается с предыдущего: вернитесь и нажмите подсвеченную кнопку.",
        });
        this.placeCard(null);
        this.mascot.setMood("sorry");
        return;
      }

      this.mascot.setMood("thinking");
      this.renderCard({ step, searching: true });
      const el = await findTarget(step, abort.signal);
      if (abort.signal.aborted) return;

      if (!el) {
        // Элемента нет, но страница подходит одному из следующих шагов и его
        // элемент уже на месте — значит, этот шаг портал пропустил сам
        // Флаг «пришли Назад» гасит перескок ровно один раз — на этом показе.
      // Иначе он оставался в состоянии навсегда, и после перезагрузки страницы
      // движок переставал пропускать шаги, которые портал прошёл сам.
      const goingBack = state.back;
      if (goingBack) {
        state.back = false;
        saveState(state);
      }
      const ahead = goingBack ? null : this.stepAhead(state.step, true);
        if (ahead !== null) {
          state.step = ahead;
          state.navigated = false;
          saveState(state);
          void this.show();
          return;
        }
        this.renderCard({
          step, notFound: true,
          note: step.optional
            ? "Не вижу этот элемент на странице — возможно, нужно войти или открыть нужный раздел. Ниже всё равно рассказал, что делать."
            : "Не нашёл этот элемент на странице: портал мог измениться. Ниже — что нужно сделать.",
        });
        this.placeCard(null);
        this.mascot.setMood("sorry");
        return;
      }

      this.target = el;
      el.scrollIntoView({ block: "center", inline: "nearest", behavior: reduced() ? "auto" : "smooth" });
      await sleep(reduced() ? 0 : 350);
      if (abort.signal.aborted) return;
      this.renderCard({
        step,
        note: isDisabled(el)
          ? "Портал пока не даёт нажать это: элемент оживёт, когда всё готово — например, после установки плагина или заполнения полей выше."
          : "",
      });
      this.placeCard(el);
      this.attachTarget(el, step);
      this.mascot.setMood("speaking");
      setTimeout(() => { if (this.mascot && !abort.signal.aborted) this.mascot.setMood("happy"); }, 1400);
    }

    /**
     * Ближайший следующий шаг, чья страница открыта сейчас; с `withTarget` —
     * ещё и с найденным элементом. Только шаги с адресом: шаг «на любой
     * странице» перескакивать нельзя, он подходит всегда.
     */
    stepAhead(from, withTarget = false) {
      const steps = this.state?.guide.steps || [];
      for (let j = from + 1; j < steps.length; j += 1) {
        const candidate = steps[j];
        if (!candidate.url || !pathMatches(candidate.url)) continue;
        if (!withTarget) return j;
        // Однозначный локатор, а не «хоть что-то похожее»: общий селектор вроде
        // [class*="LightButton"] совпадает с половиной кнопок портала, и по нему
        // движок перескакивал на шаг, до которого человек ещё не дошёл.
        if ((candidate.target || []).some((loc) => distinct(candidatesFor(loc)).length === 1)) {
          return j;
        }
      }
      return null;
    }

    next() {
      if (!this.state) return;
      if (this.state.step >= this.total - 1) { this.finish(); return; }
      // «Далее» на шаге «нажмите сюда» — это просьба нажать за человека: клик
      // уводит туда же, куда увёл бы его собственный, и следующий шаг увидит
      // нужную страницу. Только для ссылок и кнопок навигации: формы отправлять
      // за человека нельзя, поэтому такие шаги помечены action: input.
      const step = this.step;
      // выключенную кнопку нажимать бессмысленно — просто идём дальше по шагам
      if (step?.action === "click" && this.target && document.contains(this.target)
          && !isDisabled(this.target)) {
        this.target.click();   // обработчик клика сам сдвинет шаг
        return;
      }
      this.state.step += 1;
      this.state.navigated = false;
      this.state.back = false;
      saveState(this.state);
      void this.show();
    }

    prev() {
      if (!this.state || this.state.step === 0) return;
      this.state.step -= 1;
      this.state.navigated = false;
      // Человек сам попросил назад — перескакивать вперёд к «уже открытой»
      // странице нельзя, иначе кнопка «Назад» не работала бы на развилках
      this.state.back = true;
      saveState(this.state);
      void this.show();
    }

    finish() {
      this.mascot?.setMood("happy");
      // Через 2.6 с подсказки уберутся сами — но если за это время человек
      // запустит другой сценарий, тот не должен погибнуть вместе с этим.
      clearTimeout(this.byeTimer);
      this.renderCard({ step: { title: "Готово!", text: "Если что-то осталось непонятным — спросите меня в чате." }, done: true });
      this.placeCard(null);
      this.detachTarget();
      clearState();
      this.byeTimer = setTimeout(() => this.stop(), 2600);
    }

    // --- целевой элемент ---

    attachTarget(el, step) {
      if (step.action !== "click") return;
      // Клик по подсвеченному элементу — это и есть выполнение шага: идём дальше
      // сами. Состояние сохраняем до перехода: клик может увести на другую страницу.
      const onClick = () => {
        if (!this.state) return;
        if (this.state.step >= this.total - 1) { clearState(); setTimeout(() => this.finish(), 300); return; }
        this.state.step += 1;
        this.state.navigated = false;
        this.state.back = false;
        saveState(this.state);
        setTimeout(() => { if (this.state) void this.show(); }, 450);
      };
      el.addEventListener("click", onClick, { capture: true, once: true });
      this.listeners.push(() => el.removeEventListener("click", onClick, { capture: true }));
    }

    detachTarget() {
      this.listeners.forEach((off) => off());
      this.listeners = [];
      this.target = null;
    }

    // --- позиционирование ---

    renderCard({ step, note = "", searching = false, notFound = false, done = false }) {
      const index = this.state?.step ?? 0;
      const total = this.total;
      const dots = Array.from({ length: total }, (_, i) =>
        `<span class="vk-card__dot ${i < index ? "vk-card__dot--done" : ""} ${i === index ? "vk-card__dot--now" : ""}"></span>`).join("");
      const last = index >= total - 1;
      this.card.innerHTML = `
        <div class="vk-card__head">
          <span class="vk-card__kicker">${escapeHtml(this.state?.guide.title || "Валерчик")}</span>
          ${done ? "" : `<span class="vk-card__count">${index + 1} из ${total}</span>`}
          <button class="vk-card__close" type="button" title="Закрыть подсказки (Esc)" aria-label="Закрыть">${ICON_X}</button>
        </div>
        <h3 class="vk-card__title">${escapeHtml(step.title)}</h3>
        <p class="vk-card__text">${escapeHtml(step.text)}</p>
        ${note ? `<div class="vk-card__note">${ICON_INFO}<span>${escapeHtml(note)}</span></div>` : ""}
        ${done ? "" : `
        <div class="vk-card__actions">
          <div class="vk-card__dots">${dots}</div>
          <button class="vk-btn2 vk-btn2--ghost" type="button" data-act="prev" ${index === 0 ? "disabled" : ""}>Назад</button>
          <button class="vk-btn2 vk-btn2--primary" type="button" data-act="next" ${searching ? "disabled" : ""}>${last ? "Готово" : "Далее"}</button>
        </div>`}`;
      this.card.querySelector(".vk-card__close").onclick = () => this.stop();
      this.card.querySelector('[data-act="prev"]')?.addEventListener("click", () => this.prev());
      this.card.querySelector('[data-act="next"]')?.addEventListener("click", () => this.next());
      this.card.classList.toggle("vk-card--on", true);
      this.spot.classList.toggle("vk-spot--soft", notFound || searching || done);
      if (searching) this.placeCard(null);
    }

    /** Карточка у элемента (или по центру, если элемента нет) + маскот у её угла. */
    placeCard(el) {
      const vw = window.innerWidth;
      const vh = window.innerHeight;
      const cardW = Math.min(CARD_WIDTH, vw - 24);
      const cardH = this.card.offsetHeight || 160;
      let left;
      let top;
      if (el) {
        const r = el.getBoundingClientRect();
        const pad = 6;
        Object.assign(this.spot.style, {
          left: `${r.left - pad}px`, top: `${r.top - pad}px`,
          width: `${r.width + pad * 2}px`, height: `${r.height + pad * 2}px`,
        });
        this.spot.classList.add("vk-spot--on");
        const below = r.bottom + GAP + cardH < vh - 12;
        top = below ? r.bottom + GAP : Math.max(12, r.top - GAP - cardH);
        left = Math.min(Math.max(12, r.left), vw - cardW - 12);
        // элемент во всю ширину (форма, заголовок) — карточку под его левый край
        if (r.width > vw * 0.7) left = Math.min(Math.max(12, r.left + 24), vw - cardW - 12);
      } else {
        Object.assign(this.spot.style, {
          left: `${vw / 2}px`, top: `${vh / 2}px`, width: "0px", height: "0px",
        });
        this.spot.classList.add("vk-spot--on");
        left = (vw - cardW) / 2;
        top = Math.max(12, vh / 2 - cardH / 2);
      }
      Object.assign(this.card.style, { left: `${left}px`, top: `${top}px`, width: `${cardW}px` });
      this.placeMascot(left, top, cardW);
    }

    placeMascot(cardLeft, cardTop, cardW) {
      // у левого верхнего угла карточки, а если там нет места — у правого
      let x = cardLeft - MASCOT_SIZE * 0.55;
      let y = cardTop - MASCOT_SIZE * 0.6;
      if (x < 4) x = cardLeft + cardW - MASCOT_SIZE * 0.45;
      if (y < 4) y = cardTop + 8;
      this.walkTo(x, y);
    }

    walkTo(x, y) {
      const from = this.mascotPos || { x, y };
      this.mascotPos = { x, y };
      const dist = Math.hypot(x - from.x, y - from.y);
      this.mascotEl.classList.toggle("vk-mascot--flip", x < from.x - 8);
      if (reduced() || dist < 6) {
        this.mascotEl.style.transform = `translate(${x}px, ${y}px)`;
        return;
      }
      // Путь буквой «Г»: сначала по горизонтали, потом по вертикали — поворот
      // читается как поворот, а не как срез по диагонали (как у ремонтника в чате)
      const duration = Math.min(1100, 320 + dist * 0.9);
      this.mascotEl.classList.add("vk-mascot--walking");
      this.walk?.cancel();
      this.walk = this.mascotEl.animate([
        { transform: `translate(${from.x}px, ${from.y}px)` },
        { transform: `translate(${x}px, ${from.y}px)`, offset: 0.55 },
        { transform: `translate(${x}px, ${y}px)` },
      ], { duration, easing: "cubic-bezier(0.4, 0, 0.2, 1)", fill: "forwards" });
      this.walk.onfinish = () => {
        this.mascotEl.style.transform = `translate(${x}px, ${y}px)`;
        this.walk?.cancel();
        this.walk = null;
        this.mascotEl.classList.remove("vk-mascot--walking");
      };
    }

    reposition() {
      if (!this.state || !this.target) return;
      if (this.raf) return;
      this.raf = requestAnimationFrame(() => {
        this.raf = 0;
        if (!this.target || !document.contains(this.target)) return;
        this.placeCard(this.target);
      });
    }

    // --- события ---

    onKey(e) {
      if (!this.state) return;
      const typing = /^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement?.tagName || "")
        || document.activeElement?.isContentEditable;
      if (e.key === "Escape") { e.stopPropagation(); this.stop(); return; }
      if (typing) return;
      if (e.key === "ArrowRight") { e.preventDefault(); this.next(); }
      if (e.key === "ArrowLeft") { e.preventDefault(); this.prev(); }
    }

    onNavigate() {
      // SPA сменил страницу — элемент шага мог появиться или исчезнуть
      if (!this.state) return;
      clearTimeout(this.navTimer);
      this.navTimer = setTimeout(() => { if (this.state) void this.show(); }, 300);
    }
  }

  function escapeHtml(s) {
    return String(s ?? "").replace(/[&<>"']/g, (c) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
    })[c]);
  }

  // Записи сценариев нужен ровно тот же поиск, каким пойдёт движок:
  // локатор, который приводит к другому элементу, записывать нельзя.
  VK.findByLocator = findByLocator;
  VK.locatorTargets = (loc) => distinct(candidatesFor(loc));

  VK.GuideEngine = GuideEngine;
})();
