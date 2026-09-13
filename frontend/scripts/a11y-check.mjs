/**
 * Проверка доступности по отрендеренной странице, а не по исходникам.
 *
 * Структура (в светлой теме): поля без подписи, кнопки и ссылки без имени,
 * картинки без alt, ровно один h1, ссылка «Перейти к содержимому», живая
 * область в чате. Цвет и кегль (в обеих темах): контраст текста WCAG AA
 * (у SVG-подписей — по fill, а не по color) и текст мельче 12 px. Работает по
 * живому стенду — разметку собирает React, и в JSX эти дефекты не видны.
 *
 * Запуск: поднять сервер со собранным фронтендом и выполнить
 *   node scripts/a11y-check.mjs [http://127.0.0.1:8090]
 */
import { chromium } from "playwright";

const BASE = process.argv[2] ?? process.env.A11Y_BASE ?? "http://127.0.0.1:8090";
const THEMES = ["light", "dark"];

/** Страницы и то, чем на них подтверждается загрузка (иначе считаем пустой каркас). */
const PAGES = [
  { path: "/", ready: "textarea", live: true },
  { path: "/login", ready: "input[type=password]" },
  // У виджета нет повторяющейся навигации — только область чата в чужом iframe.
  // Обход блоков (WCAG 2.4.1) нужен там, где их есть что обходить; лишняя
  // остановка фокуса «Перейти к содержимому» перед полем ввода только мешала бы.
  { path: "/widget", ready: "textarea", bypass: false },
  { path: "/admin", ready: "text=Диалогов всего" },
  { path: "/admin/kb", ready: "table" },
  { path: "/admin/settings", ready: "text=Приветствие" },
  { path: "/admin/import", ready: "text=Задачи импорта" },
  { path: "/admin/feedback", ready: "h1" },
  { path: "/admin/gaps", ready: "h1" },
  { path: "/admin/quality", ready: "h1" },
  { path: "/admin/guides", ready: "h1" },
  { path: "/admin/mcp", ready: "h1" },
  { path: "/operator", ready: "text=Обращения" },
];

const AUDIT = () => {
  const visible = (el) => {
    const box = el.getBoundingClientRect();
    return box.width > 0 && box.height > 0 && getComputedStyle(el).visibility !== "hidden";
  };
  const named = (el) => {
    if (el.getAttribute("aria-label")?.trim()) return true;
    const by = el.getAttribute("aria-labelledby");
    if (by && by.split(/\s+/).some((id) => document.getElementById(id)?.textContent?.trim())) return true;
    if (el.labels && el.labels.length > 0) return true;
    if (el.closest("label")) return true;
    return false;
  };
  const fields = [...document.querySelectorAll("input, select, textarea")].filter(
    (el) => el.type !== "hidden" && visible(el) && !named(el),
  );
  const buttons = [...document.querySelectorAll("button, [role=button], [role=switch]")].filter(
    (el) => visible(el) && !el.textContent.trim() && !named(el) && !el.getAttribute("title"),
  );
  const links = [...document.querySelectorAll("a[href]")].filter(
    (el) => visible(el) && !el.textContent.trim() && !named(el) && !el.getAttribute("title"),
  );
  const images = [...document.querySelectorAll("img")].filter((el) => el.getAttribute("alt") === null);

  // Цвет: контраст считается по фактическому фону с композицией полупрозрачных
  // слоёв; цвет приводит к sRGB сам браузер (canvas), поэтому oklch не мешает.
  const canvas = document.createElement("canvas");
  canvas.width = canvas.height = 1;
  const ctx = canvas.getContext("2d", { willReadFrequently: true });
  const rgba = (css) => {
    ctx.clearRect(0, 0, 1, 1);
    ctx.fillStyle = "rgba(0,0,0,0)";
    ctx.fillStyle = css;
    ctx.fillRect(0, 0, 1, 1);
    const d = ctx.getImageData(0, 0, 1, 1).data;
    return [d[0], d[1], d[2], d[3] / 255];
  };
  const channel = (c) => {
    const v = c / 255;
    return v <= 0.04045 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4;
  };
  const luminance = (c) => 0.2126 * channel(c[0]) + 0.7152 * channel(c[1]) + 0.0722 * channel(c[2]);
  const over = (top, bottom) => {
    const a = top[3] + bottom[3] * (1 - top[3]);
    if (!a) return [0, 0, 0, 0];
    return [0, 1, 2].map((i) => (top[i] * top[3] + bottom[i] * bottom[3] * (1 - top[3])) / a).concat(a);
  };
  const background = (el) => {
    const layers = [];
    for (let node = el; node; node = node.parentElement) {
      if (node instanceof SVGElement && !(node instanceof SVGSVGElement)) continue;
      const c = rgba(getComputedStyle(node).backgroundColor);
      if (c[3] > 0) layers.push(c);
      if (c[3] >= 0.99) break;
    }
    let base = rgba(getComputedStyle(document.documentElement).backgroundColor);
    if (base[3] < 0.99) base = over(base, rgba(getComputedStyle(document.body).backgroundColor));
    if (base[3] < 0.99) base = over(base, [255, 255, 255, 1]);
    for (let i = layers.length - 1; i >= 0; i--) base = over(layers[i], base);
    return base;
  };
  const contrast = (fg, bg) => {
    const a = luminance(fg);
    const b = luminance(bg);
    return (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05);
  };
  // Контроль самого инструмента: известные пары обязаны дать известные числа
  const selfTest = [contrast(rgba("#767676"), rgba("#fff")).toFixed(2), contrast(rgba("#000"), rgba("#fff")).toFixed(2)];

  const lowContrast = [];
  const tiny = [];
  let texts = 0;
  for (const el of document.querySelectorAll("body *")) {
    const own = [...el.childNodes].filter((n) => n.nodeType === 3).map((n) => n.textContent.trim()).join(" ").trim();
    if (!own) continue;
    const box = el.getBoundingClientRect();
    if (box.width <= 1 || box.height <= 1) continue;
    const style = getComputedStyle(el);
    if (style.visibility === "hidden" || style.display === "none" || Number(style.opacity) < 0.1) continue;
    if (el.closest("button:disabled, [aria-disabled=true], fieldset:disabled, option, select")) continue;
    texts++;
    const size = parseFloat(style.fontSize);
    const bold = parseInt(style.fontWeight, 10) >= 700;
    const svg = el instanceof SVGElement && style.fill && style.fill !== "none" && !style.fill.startsWith("url");
    const bg = background(el);
    let fg = rgba(svg ? style.fill : style.color);
    if (fg[3] < 1) fg = over(fg, bg);
    const need = size >= 24 || (size >= 18.66 && bold) ? 3 : 4.5;
    const ratio = contrast(fg, bg);
    if (ratio < need) lowContrast.push(`${ratio.toFixed(2)} «${own.slice(0, 40)}»`);
    if (size < 12) tiny.push(`${size}px «${own.slice(0, 40)}»`);
  }

  return {
    fields: fields.map((el) => `${el.tagName.toLowerCase()}[${el.type ?? ""}] ${el.className}`.slice(0, 80)),
    buttons: buttons.map((el) => el.className.slice(0, 80)),
    links: links.map((el) => el.getAttribute("href")),
    images: images.map((el) => el.getAttribute("src")?.slice(0, 60)),
    h1: document.querySelectorAll("h1").length,
    live: document.querySelectorAll("[aria-live], [role=log], [role=status], [role=alert]").length,
    skipLink: Boolean(document.querySelector('a[href="#main"]')) && Boolean(document.getElementById("main")),
    selfTest,
    texts,
    lowContrast: [...new Set(lowContrast)],
    tiny: [...new Set(tiny)],
  };
};

const problems = [];

/**
 * Скачанный playwright'ом chromium есть не всегда (на машине разработчика его
 * может не быть вовсе), а системный Chrome или Edge — почти наверняка есть.
 * Проверка доступности не должна упираться в 150 МБ загрузки.
 */
async function launch() {
  const attempts = [undefined, "chrome", "msedge"];
  let lastError;
  for (const channel of attempts) {
    try {
      return await chromium.launch(channel ? { channel } : {});
    } catch (error) {
      lastError = error;
    }
  }
  throw lastError;
}

const browser = await launch();

for (const theme of THEMES) {
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 }, colorScheme: theme });
  await context.addCookies([{ name: "valerchik-theme", value: theme, url: BASE }]);
  // Вход сотрудника: кука ставится сервером, поэтому логинимся запросом из
  // контекста браузера — так же, как это делает интерфейс.
  const login = await context.request.post(`${BASE}/api/auth/login`, {
    data: { username: "admin", password: "admin12345" },
    headers: { "X-Requested-With": "valerchik" },
  });
  if (!login.ok()) {
    console.error(`Не удалось войти администратором: ${login.status()}. Стенд поднят на ${BASE}?`);
    process.exit(2);
  }
  const page = await context.newPage();
  // Необработанный отказ промиса или исключение — тоже дефект: экран застывает,
  // а консоль молчит, пока её не откроешь.
  const pageErrors = [];
  page.on("pageerror", (error) => pageErrors.push(error.message));

  for (const { path, ready, live, bypass = true } of PAGES) {
    const where = `${path} [${theme}]`;
    await page.goto(`${BASE}${path}`, { waitUntil: "domcontentloaded" });
    try {
      await page.locator(ready).first().waitFor({ state: "attached", timeout: 15_000 });
    } catch {
      problems.push(`${where}: страница не загрузилась (не дождались «${ready}»)`);
      continue;
    }
    // данные разделов приходят следом за каркасом — меряем уже заполненную страницу
    await page.waitForTimeout(1_200);
    if (pageErrors.length) {
      problems.push(`${where}: ошибки на странице: ${pageErrors.splice(0).slice(0, 3).join(" | ")}`);
    }
    const result = await page.evaluate(AUDIT);
    if (result.selfTest.join() !== "4.54,21.00") {
      console.error(`Инструмент контраста сломан: контрольные пары дали ${result.selfTest}`);
      process.exit(2);
    }
    const say = (what, list) => {
      if (list.length) problems.push(`${where}: ${what}: ${list.length} — ${list.slice(0, 4).join(" | ")}`);
    };
    if (result.texts === 0) problems.push(`${where}: на странице не найден текст — замер не состоялся`);
    say("контраст ниже AA", result.lowContrast);
    say("текст мельче 12 px", result.tiny);
    if (theme !== THEMES[0]) continue;
    say("поля без подписи", result.fields);
    say("кнопки без имени", result.buttons);
    say("ссылки без имени", result.links);
    say("картинки без alt", result.images);
    if (result.h1 !== 1) problems.push(`${where}: заголовков h1 на странице ${result.h1}, нужен ровно один`);
    if (bypass && !result.skipLink) problems.push(`${where}: нет ссылки «Перейти к содержимому» или якоря #main`);
    if (live && result.live === 0) problems.push(`${where}: нет живой области — приход ответа не объявляется`);
  }
  await context.close();
}

await browser.close();

if (problems.length) {
  console.error("Доступность: найдены дефекты\n" + problems.map((p) => `  • ${p}`).join("\n"));
  process.exit(1);
}
console.log(`Доступность: ${PAGES.length} страниц в ${THEMES.length} темах без замечаний`);
