/**
 * Валерчик — встраиваемый мини-чат для любого сайта.
 *
 * Подключение (одна строка перед </body>):
 *   <script src="https://ВАШ-ХОСТ/embed.js" defer></script>
 *
 * Необязательные атрибуты:
 *   data-url="https://другой-хост"  — если чат живёт на другом домене
 *   data-color="#d6001c"            — цвет кнопки
 *   data-position="right|left"      — угол экрана (по умолчанию right)
 */
(function () {
  "use strict";
  var script = document.currentScript;
  var base = (script && script.getAttribute("data-url")) || new URL(script.src).origin;
  var color = (script && script.getAttribute("data-color")) || "#c2242c";
  var side = (script && script.getAttribute("data-position")) === "left" ? "left" : "right";
  var SLIME = "cubic-bezier(0.34, 1.56, 0.64, 1)";

  var style = document.createElement("style");
  style.textContent =
    "#valerchik-btn{position:fixed;bottom:20px;" + side + ":20px;z-index:2147483000;" +
    "width:60px;height:60px;border-radius:50%;border:none;cursor:pointer;background:" + color + ";" +
    "box-shadow:0 6px 24px rgba(198,36,44,.4);display:flex;align-items:center;justify-content:center;" +
    "transition:transform .4s " + SLIME + ",box-shadow .2s ease;}" +
    "#valerchik-btn:hover{transform:translateY(-3px) scale(1.08);}" +
    "#valerchik-btn:active{transform:scale(.92);}" +
    "#valerchik-btn svg{width:30px;height:30px;transition:transform .4s " + SLIME + ";}" +
    "#valerchik-btn.open svg.chat{transform:scale(0) rotate(90deg);position:absolute;}" +
    "#valerchik-btn:not(.open) svg.close{transform:scale(0) rotate(-90deg);position:absolute;}" +
    "#valerchik-frame{position:fixed;bottom:92px;" + side + ":20px;z-index:2147483000;" +
    "width:min(384px,calc(100vw - 32px));height:min(620px,calc(100vh - 120px));" +
    "border:none;border-radius:24px;background:#fff;" +
    "box-shadow:0 12px 48px rgba(0,0,0,.22);transform-origin:bottom " + side + ";" +
    "transition:transform .45s " + SLIME + ",opacity .25s ease;}" +
    "#valerchik-frame.hidden{transform:scale(.55) translateY(30px);opacity:0;pointer-events:none;}";
  document.head.appendChild(style);

  var frame = document.createElement("iframe");
  frame.id = "valerchik-frame";
  frame.className = "hidden";
  frame.title = "Чат поддержки Портала поставщиков";
  frame.allow = "microphone";
  document.body.appendChild(frame);

  var btn = document.createElement("button");
  btn.id = "valerchik-btn";
  btn.setAttribute("aria-label", "Открыть чат поддержки");
  btn.innerHTML =
    '<svg class="chat" viewBox="0 0 24 24" fill="none" stroke="#fff" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>' +
    '<svg class="close" viewBox="0 0 24 24" fill="none" stroke="#fff" stroke-width="2" stroke-linecap="round"><path d="M18 6 6 18M6 6l12 12"/></svg>';
  document.body.appendChild(btn);

  var loaded = false;

  /**
   * Раздел портала, на котором открыт чат.
   *
   * Короткие вопросы («как отменить») без него неразрешимы: отменить можно и
   * оферту, и котировочную сессию, и заявку. Передаём путь и заголовок, но
   * НИКОГДА не query-строку — портал кладёт туда номера заявок и ИНН, а это
   * персональные данные, которым не место в чужом сервисе и его логах.
   */
  function pageContext() {
    return {
      path: String(location.pathname || "").slice(0, 200),
      title: String(document.title || "").slice(0, 120)
    };
  }

  function sendContext() {
    if (!loaded || !frame.contentWindow) return;
    try {
      frame.contentWindow.postMessage(
        { source: "valerchik", type: "page", page: pageContext() }, base
      );
    } catch (e) { /* фрейм ещё не готов — контекст доедет со следующей сменой страницы */ }
  }

  // Виджет открывают один раз и дальше ходят по сайту: без отслеживания навигации
  // он навсегда остался бы с контекстом той страницы, где его открыли. Портал —
  // SPA, поэтому событий перезагрузки не будет, и адрес приходится ловить у
  // history. Обёртка вызывает исходную реализацию и ничего не меняет в поведении;
  // всё в try — на сайте с заблокированным history виджет должен просто работать
  // дальше, пусть и без контекста.
  frame.addEventListener("load", sendContext);

  // Событие load опережает готовность чата: страница виджета приезжает ленивым
  // чанком и подписывается на сообщения позже, поэтому отправленный по load
  // контекст пропадал впустую. Ждём отмашки от самого фрейма и отвечаем на неё.
  window.addEventListener("message", function (event) {
    if (event.source !== frame.contentWindow) return;
    var data = event.data;
    if (!data || data.source !== "valerchik" || data.type !== "ready") return;
    sendContext();
  });

  try {
    ["pushState", "replaceState"].forEach(function (name) {
      var original = history[name];
      history[name] = function () {
        var result = original.apply(this, arguments);
        sendContext();
        return result;
      };
    });
    window.addEventListener("popstate", sendContext);
  } catch (e) { /* контекст необязателен, чат работает и без него */ }

  btn.addEventListener("click", function () {
    var opening = frame.classList.contains("hidden");
    if (opening && !loaded) {
      frame.src = base + "/widget";
      loaded = true;
    }
    frame.classList.toggle("hidden", !opening);
    btn.classList.toggle("open", opening);
    btn.setAttribute("aria-label", opening ? "Закрыть чат поддержки" : "Открыть чат поддержки");
  });
})();
