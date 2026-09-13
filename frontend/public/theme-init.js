// Тема выбирается до первого кадра: подключи мы её из React, страница успевала
// бы мигнуть светлым перед переключением в тёмное. Отдельный файл, а не
// инлайновый скрипт: внутри расширения браузера действует CSP script-src 'self',
// и инлайн там не выполнился бы. Читаем куку, а где кук нет (origin
// расширения) — localStorage, как и lib/storage.ts.
(function () {
  try {
    var m = document.cookie.match(/(?:^|;\s*)valerchik-theme=([^;]*)/);
    var c = m ? decodeURIComponent(m[1]) : "";
    if (!c) {
      try { c = localStorage.getItem("valerchik-theme") || ""; } catch (e) { c = ""; }
    }
    var dark = c === "dark"
      || (c !== "light" && window.matchMedia("(prefers-color-scheme: dark)").matches);
    document.documentElement.dataset.theme = dark ? "dark" : "light";
    var meta = document.querySelector('meta[name="theme-color"]');
    if (meta) meta.setAttribute("content", dark ? "#15171c" : "#f4f4f6");
  } catch (e) {
    document.documentElement.dataset.theme = "light";
  }
})();
