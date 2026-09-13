/**
 * Скриншоты для слайдов: живой интерфейс Валерчика и чистый портал без расширения.
 *
 *   BASE=http://127.0.0.1:8097 node scripts/shots.mjs
 *
 * Нужен запущенный lite-сервер (`cd backend && uv run uvicorn app.main:app --port 8097`)
 * и собранный frontend/dist. PNG ложатся в out/shots/, в слайды идут сжатые webp из src/assets/.
 */
import { chromium } from "../../frontend/node_modules/playwright/index.mjs";
import { mkdirSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const OUT = resolve(here, "../out/shots");
const BASE = process.env.BASE || "http://127.0.0.1:8097";
const ONLY = process.env.ONLY?.split(",");
mkdirSync(OUT, { recursive: true });

const browser = await chromium.launch({ channel: "msedge" });
const context = await browser.newContext({
  viewport: { width: 1440, height: 900 }, deviceScaleFactor: 2, locale: "ru-RU",
});
const page = await context.newPage();
const shot = (name) => page.screenshot({ path: `${OUT}/${name}.png` });
const want = (name) => !ONLY || ONLY.includes(name);

async function login(user, password) {
  await page.goto(`${BASE}/login`);
  await page.fill("#login", user);
  await page.fill("#password", password);
  await page.press("#password", "Enter");
  await page.waitForURL((url) => !url.pathname.startsWith("/login"), { timeout: 15000 });
}

if (want("chat")) {
  await page.goto(`${BASE}/`);
  await page.locator(".hero-chip").first().waitFor();
  await page.mouse.move(720, 120);
  await page.waitForTimeout(600);
  await shot("chat-home");
  await page.locator(".hero-chip").first().click();
  // без выбранной роли бот может сначала переспросить «поставщик или заказчик?» — снимаем что есть
  await page.getByText("источники", { exact: false }).first()
    .waitFor({ timeout: 15000 }).catch(() => {});
  await page.waitForTimeout(1500);
  await shot("chat-answer");
}

if (want("admin")) {
  await login("admin", "admin12345");
  await page.goto(`${BASE}/admin`);
  await page.waitForTimeout(2500);
  await shot("admin-dashboard");
  await page.goto(`${BASE}/admin/guides`);
  await page.locator('button[title="Изменить"]').first().waitFor();
  await page.waitForTimeout(500);
  await shot("admin-guides");
  await page.locator('button[title="Изменить"]').first().click();
  await page.waitForTimeout(700);
  await shot("admin-guide-editor");
  await page.goto(`${BASE}/admin/kb`);
  await page.waitForTimeout(1500);
  await shot("admin-kb");
  await page.goto(`${BASE}/admin/import`);
  await page.waitForTimeout(1200);
  await shot("admin-import");
}

if (want("operator")) {
  await context.clearCookies();
  await login("op_maria", "operator12345");
  await page.waitForTimeout(2000);
  await shot("operator");
}

if (want("portal")) {
  await page.goto("https://zakupki.mos.ru/", { waitUntil: "domcontentloaded", timeout: 60000 });
  await page.locator('[data-testid="cookies-agreement-banner__confirm-button"]').first()
    .click({ timeout: 6000 }).catch(() => {});
  await page.waitForTimeout(5000);
  await shot("portal-home");
  await page.getByText("Зарегистрироваться", { exact: true }).first().click();
  await page.waitForTimeout(5000);
  await shot("portal-register");
}

await browser.close();
console.log("готово:", OUT);
