/**
 * Кадры колоды в PNG для проверки глазами: node scripts/frames.mjs 120 400 900
 * Без аргументов снимает середину демо и начало покоя каждого слайда.
 * Нужен запущенный `npm run dev` (порт 5190).
 */
import { chromium } from "../../frontend/node_modules/playwright/index.mjs";
import { mkdirSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const OUT = resolve(here, "../out/frames");
const BASE = process.env.DECK || "http://localhost:5190";
mkdirSync(OUT, { recursive: true });

const frames = process.argv.slice(2).map(Number);
const browser = await chromium.launch({ channel: "msedge" });
const page = await browser.newPage({ viewport: { width: 1920, height: 1080 } });
page.on("pageerror", (err) => console.log("[pageerror]", String(err).slice(0, 300)));
for (const frame of frames) {
  await page.goto(`${BASE}/?frame=${frame}`);
  await page.waitForTimeout(1200);
  await page.mouse.move(1919, 1079);
  await page.screenshot({ path: `${OUT}/f${String(frame).padStart(5, "0")}.png` });
}
await browser.close();
console.log(frames.length, "кадров в", OUT);
