/**
 * Сколько раз в секунду реально перерисовывается Валерчик во время перехода.
 *   node scripts/fps.mjs   (нужен npm run dev)
 */
import { chromium } from "../../frontend/node_modules/playwright/index.mjs";

const BASE = process.env.DECK || "http://localhost:5190/";
const browser = await chromium.launch({ channel: "msedge", args: ["--disable-gpu-vsync"] });
const page = await browser.newPage({ viewport: { width: 1920, height: 1080 } });
await page.goto(BASE);
await page.waitForTimeout(7000);

for (let i = 0; i < 3; i++) {
  await page.keyboard.press("ArrowRight");
  const changes = await page.evaluate(() => new Promise((done) => {
    const host = () => document.querySelector('svg[viewBox="0 0 120 120"]')?.parentElement;
    let last = "";
    let count = 0;
    const start = performance.now();
    const tick = () => {
      const el = host();
      const now = el ? `${el.style.left}|${el.style.top}|${el.style.transform}` : "";
      if (now !== last) count++;
      last = now;
      if (performance.now() - start < 2000) requestAnimationFrame(tick);
      else done(count / 2);
    };
    requestAnimationFrame(tick);
  }));
  console.log(`переход ${i + 1}: ${changes} кадров/с`);
  await page.waitForTimeout(9000);
}
await browser.close();
