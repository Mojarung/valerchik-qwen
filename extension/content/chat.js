/**
 * Кнопка чата и фрейм с Валерчиком — то же, что public/embed.js, но внутри
 * расширения: страница портала нам не принадлежит, и одну строку со скриптом
 * на неё не добавить. Расширение делает это за портал.
 *
 * Во фрейме — страница расширения chat.html, а уже в ней собранный внутрь
 * расширения виджет: сам чат — тот же React-код, что и на сайте, один в один.
 * Прямой фрейм на сервер портал открыть не даст (см. chat-frame.js). Отсюда уходит
 * раздел портала (путь и заголовок, без query-строки — там номера заявок и
 * ИНН) и признак «хозяин умеет вести по шагам»; обратно приходят сценарии,
 * которые исполняет guide.js, и выбранная тема оформления.
 */
(() => {
  const VK = (globalThis.VK ||= {});

  VK.createChat = ({ root, chatUrl, side, onGuide, onTheme }) => {
    const origin = new URL(chatUrl).origin;
    let loaded = false;
    let unread = 0;

    const frame = document.createElement("iframe");
    frame.className = `vk-frame vk-frame--${side} hidden`;
    frame.title = "Чат поддержки Портала поставщиков";
    frame.allow = "microphone";

    // На кнопке — сам Валерчик, тот же рисунок и та же анимация, что в чате:
    // моргает, следит зрачками за курсором, радуется наведению
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = `vk-btn vk-btn--${side}`;
    btn.setAttribute("aria-label", "Открыть чат поддержки");
    const face = VK.createMascot(56, "idle");
    face.el.classList.add("vk-btn__face");
    btn.append(face.el);
    btn.insertAdjacentHTML("beforeend", VK.closeIcon);
    btn.addEventListener("pointerenter", () => face.setMood("happy"));
    btn.addEventListener("pointerleave", () => face.setMood("idle"));

    root.append(frame, btn);

    function pageContext() {
      return {
        path: String(location.pathname || "").slice(0, 200),
        title: String(document.title || "").slice(0, 120),
      };
    }

    function sendContext() {
      if (!loaded || !frame.contentWindow) return;
      try {
        frame.contentWindow.postMessage({
          source: "valerchik", type: "page", page: pageContext(),
          host: { name: "valerchik-extension", guides: true },
        }, origin);
      } catch { /* фрейм ещё не готов — контекст доедет со следующей навигацией */ }
    }

    function setOpen(open) {
      if (open && !loaded) {
        frame.src = chatUrl;
        loaded = true;
      }
      frame.classList.toggle("hidden", !open);
      btn.classList.toggle("open", open);
      btn.setAttribute("aria-label", open ? "Закрыть чат поддержки" : "Открыть чат поддержки");
      if (open) { unread = 0; renderBadge(); }
    }

    function renderBadge() {
      let badge = btn.querySelector(".vk-btn__badge");
      if (!unread) { badge?.remove(); return; }
      if (!badge) { badge = document.createElement("span"); badge.className = "vk-btn__badge"; btn.appendChild(badge); }
      badge.textContent = String(unread);
    }

    btn.addEventListener("click", () => setOpen(frame.classList.contains("hidden")));

    window.addEventListener("message", (event) => {
      if (event.source !== frame.contentWindow) return;
      const data = event.data;
      if (!data || data.source !== "valerchik") return;
      if (data.type === "ready") sendContext();
      // Человек переключил тему в чате — перекрашиваем и то, что мы рисуем
      // поверх портала: кнопку, карточки подсказок, пузырь маскота
      if (data.type === "theme") onTheme?.(data.theme);
      if (data.type === "guide" && data.guide) {
        // Подсказки поверх страницы — чат сворачиваем, иначе он закрывает то,
        // что показываем; вернуться к нему можно той же кнопкой
        setOpen(false);
        onGuide?.(data.guide);
      }
    });

    // Портал — SPA: адрес меняется без перезагрузки. Подменить history отсюда
    // нельзя — контент-скрипт живёт в изолированном мире, где свой объект
    // history, и вызовы портала до подмены не доходили: чат всю сессию считал,
    // что человек на той странице, где он его открыл. Подмена сделана в
    // content/history-hook.js, который выполняется в контексте самой страницы,
    // а сюда приходит только событие.
    window.addEventListener("valerchik:navigate", sendContext);
    window.addEventListener("popstate", sendContext);

    return {
      open: () => setOpen(true),
      close: () => setOpen(false),
      toggle: () => setOpen(frame.classList.contains("hidden")),
      /** Откуда маскот «выходит» на страницу — из кнопки чата. */
      buttonPosition: () => {
        const r = btn.getBoundingClientRect();
        return { x: r.left + r.width / 2 - 32, y: r.top - 40 };
      },
    };
  };
})();
