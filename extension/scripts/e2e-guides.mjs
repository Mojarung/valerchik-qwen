/**
 * Проверка каталога сценариев на живом портале: для каждого сценария первый
 * вопрос уходит в чат, нажимается «Показать на портале», и проверяется, что
 * первый шаг нашёл свой элемент (spotlight зафиксирован). Шаги за входом в
 * кабинет считаются пройденными, если карточка показалась хотя бы в режиме
 * «элемент не найден» — они помечены optional.
 *
 *   node scripts/e2e-guides.mjs            — сервер на http://localhost:8090
 *   ONLY=login,contracts node scripts/e2e-guides.mjs
 */
import { chromium } from "../../frontend/node_modules/playwright/index.mjs";
import { mkdtempSync, readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const EXT = resolve(here, "..");
const catalog = JSON.parse(readFileSync(resolve(here, "../../backend/app/guides/portal.json"), "utf8"));
const only = process.env.ONLY ? new Set(process.env.ONLY.split(",")) : null;
const guides = catalog.guides.filter((g) => !only || only.has(g.id));

const context = await chromium.launchPersistentContext(mkdtempSync(join(tmpdir(), "valerchik-guides-")), {
  channel: "msedge", headless: !process.env.HEADED, viewport: { width: 1440, height: 900 }, locale: "ru-RU",
  args: [`--disable-extensions-except=${EXT}`, `--load-extension=${EXT}`],
});
// По умолчанию расширение смотрит на боевой сервер — адрес ставим явно,
// иначе локальный прогон незаметно шёл бы в прод.
const BASE = process.env.BASE || "http://localhost:8090";
{
  let [worker] = context.serviceWorkers();
  if (!worker) worker = await context.waitForEvent("serviceworker");
  await worker.evaluate((base) => chrome.storage.sync.set({ serverUrl: base }), BASE);
}
const page = await context.newPage();
const HOST = "#valerchik-extension";
const frame = () => page.frameLocator(`${HOST} iframe.vk-frame`).frameLocator("#widget");
const results = [];

async function openChat() {
  await page.locator(HOST).waitFor({ state: "attached", timeout: 20000 });
  const btn = page.locator(`${HOST} .vk-btn`);
  if (!(await btn.evaluate((b) => b.classList.contains("open")))) await btn.click();
  await frame().locator("textarea").waitFor({ timeout: 60000 });
}

async function setRole(role) {
  const idx = role === "customer" ? 1 : 0;
  const option = frame().locator('.audience-toggle__option[role="radio"]').nth(idx);
  for (let i = 0; i < 3; i += 1) {
    if ((await option.getAttribute("aria-checked")) === "true") break;
    await option.click();
    await page.waitForTimeout(400);
  }
  // Роль лежит в хранилище виджета — проверяем именно его, а не вид кнопки.
  // Виджет живёт на origin расширения, где кук нет, поэтому localStorage.
  const saved = await frame().locator("body").evaluate(() => localStorage.getItem("valerchik-audience"));
  if (saved !== role) throw new Error(`роль не переключилась: сохранено=${saved ?? "ничего"}`);
}

try {
  await page.goto("https://zakupki.mos.ru/", { waitUntil: "domcontentloaded", timeout: 60000 });
  await page.locator('[data-testid="cookies-agreement-banner__confirm-button"]').first().click({ timeout: 4000 }).catch(() => {});

  for (const guide of guides) {
    const started = Date.now();
    const row = { id: guide.id, step: guide.steps[0].title, locked: false, url: "", note: "" };
    try {
      await openChat();
      await setRole(guide.audience === "customer" ? "customer" : "supplier");
      const before = await frame().locator("button", { hasText: "Показать на портале" }).count();
      await frame().locator("textarea").fill(guide.questions[0]);
      await frame().locator("textarea").press("Enter");
      const buttons = frame().locator("button", { hasText: "Показать на портале" });
      // В чате остаётся история с прежними кнопками — ждём именно новую
      const deadline = Date.now() + 90000;
      while ((await buttons.count()) <= before) {
        if (Date.now() > deadline) throw new Error("ответ без кнопки «Показать на портале»");
        await page.waitForTimeout(400);
      }
      // подобрался ли ожидаемый сценарий — по числу шагов в подписи кнопки
      const label = await buttons.last().innerText();
      if (!label.includes(`· ${guide.steps.length} `)) row.note = `кнопка: ${label}`;
      await buttons.last().click();
      const card = page.locator(`${HOST} .vk-card--on`);
      await card.waitFor({ timeout: 30000 });
      // шаг с адресом уводит на другую страницу — ждём карточку уже там
      const target = guide.steps[0].url;
      if (target && !page.url().includes(target)) {
        await page.waitForURL((u) => new URL(u).pathname.startsWith(target), { timeout: 60000 }).catch(() => {});
        await page.locator(`${HOST} .vk-card--on`).waitFor({ timeout: 30000 });
      }
      const lockedSel = `${HOST} .vk-spot--on:not(.vk-spot--soft)`;
      row.locked = await page.locator(lockedSel).waitFor({ timeout: 15000 }).then(() => true).catch(() => false);
      row.url = new URL(page.url()).pathname;
      row.step = await page.locator(`${HOST} .vk-card__title`).innerText();
      await page.keyboard.press("Escape");
      await page.locator(`${HOST} .vk-card`).waitFor({ state: "detached", timeout: 5000 }).catch(() => {});
    } catch (error) {
      row.note = String(error.message || error).split("\n")[0].slice(0, 120);
      // что виджет показал под последним ответом: кнопку, ссылку или ничего
      const links = await frame().locator("a", { hasText: "Открыть раздел на портале" }).count().catch(() => -1);
      const last = await frame().locator(".card.rounded-bl-lg").last().innerText().catch(() => "");
      row.note += ` | ссылок: ${links}, последний ответ: ${last.replace(/\s+/g, " ").slice(0, 70)}`;
      await page.keyboard.press("Escape").catch(() => {});
    }
    row.ms = Date.now() - started;
    results.push(row);
    console.log(`${row.locked ? "OK " : "-- "} ${row.id.padEnd(22)} ${row.step.padEnd(40).slice(0, 40)} ${row.url.padEnd(34).slice(0, 34)} ${row.note}`);
    // назад на главную для следующего сценария: там и кнопка, и известное состояние
    if (new URL(page.url()).pathname !== "/") {
      await page.goto("https://zakupki.mos.ru/", { waitUntil: "domcontentloaded", timeout: 60000 });
    }
  }
} finally {
  await context.close();
}
const optionalFirst = new Set(guides.filter((g) => g.steps[0].optional).map((g) => g.id));
const failed = results.filter((r) => !r.locked && !optionalFirst.has(r.id));
console.log(`\nсценариев: ${results.length}, элемент найден: ${results.filter((r) => r.locked).length}, `
  + `не найден (обязательный шаг): ${failed.length}${failed.length ? " — " + failed.map((r) => r.id).join(", ") : ""}`);
process.exit(failed.length ? 1 : 0);
