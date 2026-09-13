/**
 * Скриншоты интерфейса (Playwright + системный Edge, без загрузки браузеров).
 *   node scripts/screenshots.mjs                    — против http://localhost:8090 (docker)
 *   BASE=http://localhost:8000 node scripts/...     — против lite-сервера
 * Результат: docs/screenshots/*.png
 */
import { chromium } from "playwright";
import { mkdirSync } from "node:fs";

const BASE = process.env.BASE || "http://localhost:8090";
const OUT = "../docs/screenshots";
mkdirSync(OUT, { recursive: true });

const browser = await chromium.launch({ channel: "msedge" });

async function shot(name, { width, height, url, actions }) {
  const ctx = await browser.newContext({ viewport: { width, height } });
  const page = await ctx.newPage();
  await page.goto(url, { waitUntil: "networkidle" });
  if (actions) await actions(page);
  await page.screenshot({ path: `${OUT}/${name}.png` });
  await ctx.close();
  console.log(name, "ok");
}

const settle = async (p) => {
  await p.waitForSelector(".hero-chip");
  await p.mouse.move(10, 10);      // взгляд маскота в предсказуемую точку
  await p.waitForTimeout(1000);
};

await shot("main-desktop", { width: 1440, height: 900, url: `${BASE}/`, actions: settle });
await shot("main-mobile", { width: 390, height: 780, url: `${BASE}/`, actions: settle });

await shot("dialog-answer", {
  width: 1440, height: 900, url: `${BASE}/`,
  actions: async (p) => {
    await p.waitForSelector(".hero-chip");
    await p.click(".hero-chip");
    await p.waitForSelector("text=уверенность", { timeout: 90000 });
    await p.waitForTimeout(600);
  },
});

await shot("widget-embed", {
  width: 1440, height: 900,
  url: `${BASE}/embed-test.html`,
  actions: async (p) => {
    await p.waitForSelector("#valerchik-btn");
    await p.click("#valerchik-btn");
    await p.frameLocator("#valerchik-frame").locator(".hero-chip").first()
      .waitFor({ timeout: 90000 });
    await p.waitForTimeout(900);
  },
});

await browser.close();
console.log("all done");
