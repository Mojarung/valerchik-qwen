/**
 * Живой прогон пульта: колода играет сама, покой зацикливается, стрелки листают.
 *   node scripts/smoke.mjs                       — против npm run dev (порт 5190)
 *   DECK=file:///.../dist/index.html node scripts/smoke.mjs
 */
import { chromium } from "../../frontend/node_modules/playwright/index.mjs";

const BASE = process.env.DECK || "http://localhost:5190/";
const browser = await chromium.launch({ channel: "msedge" });
const page = await browser.newPage({ viewport: { width: 1600, height: 900 } });
const errors = [];
page.on("pageerror", (err) => errors.push(String(err)));
page.on("console", (msg) => msg.type() === "error" && errors.push(msg.text()));

const label = () => page.locator("text=/\\d+ \\/ 15/").first().textContent();
const wait = (ms) => page.waitForTimeout(ms);

await page.goto(BASE);
await wait(9000);
console.log("после 9 с на титуле:", await label());
for (let i = 0; i < 14; i++) {
  await page.keyboard.press("ArrowRight");
  await wait(i === 0 ? 14000 : 2500);
  console.log(`→ ${i + 1}:`, await label());
}
await page.keyboard.press("ArrowLeft");
await wait(1500);
console.log("← :", await label());
await page.screenshot({ path: "out/smoke.png" });
console.log(errors.length ? `ошибки:\n${errors.join("\n")}` : "ошибок нет");
await browser.close();
