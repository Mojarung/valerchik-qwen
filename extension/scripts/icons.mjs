/**
 * Иконки расширения из icons/icon.svg: PNG 16/32/48/128.
 *
 * Растеризует системный Edge через Playwright из frontend/node_modules — тот же
 * приём, что у скриншотов интерфейса (frontend/scripts/screenshots.mjs), поэтому
 * отдельных зависимостей у расширения нет.
 *
 *   node scripts/icons.mjs        (из каталога extension/)
 */
import { chromium } from "../../frontend/node_modules/playwright/index.mjs";
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const svg = readFileSync(resolve(here, "../icons/icon.svg"), "utf8");
const browser = await chromium.launch({ headless: true, channel: "msedge" });

for (const size of [16, 32, 48, 128]) {
  const page = await browser.newPage({ viewport: { width: size, height: size }, deviceScaleFactor: 1 });
  await page.setContent(
    `<html><body style="margin:0;background:transparent">${svg.replace("<svg ", `<svg width="${size}" height="${size}" `)}</body></html>`,
  );
  await page.screenshot({ path: resolve(here, `../icons/icon${size}.png`), omitBackground: true });
  await page.close();
  console.log(`icon${size}.png ok`);
}
await browser.close();
