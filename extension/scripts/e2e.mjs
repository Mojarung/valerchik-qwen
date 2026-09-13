/**
 * Живая проверка расширения: Edge с загруженным расширением открывает настоящий
 * портал, задаёт вопрос в чате и проходит сценарий подсказок по шагам.
 *
 *   node scripts/e2e.mjs                      — сервер Валерчика на http://localhost:8090
 *   BASE=http://localhost:8000 node scripts/e2e.mjs
 *   HEADED=1 node scripts/e2e.mjs             — с окном браузера
 *
 * Скриншоты — в docs/screenshots/extension-*.png. Нужен запущенный сервер
 * (lite: `cd backend && uv run uvicorn app.main:app --port 8090`) и собранный
 * frontend/dist, откуда lite-сервер отдаёт /widget.
 */
import { chromium } from "../../frontend/node_modules/playwright/index.mjs";
import { mkdirSync, mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const EXT = resolve(here, "..");
const OUT = resolve(here, "../../docs/screenshots");
const BASE = process.env.BASE || "http://localhost:8090";
const HEADED = Boolean(process.env.HEADED);
mkdirSync(OUT, { recursive: true });

const userDataDir = mkdtempSync(join(tmpdir(), "valerchik-ext-"));
const context = await chromium.launchPersistentContext(userDataDir, {
  channel: "msedge",
  headless: !HEADED,
  viewport: { width: 1440, height: 900 },
  locale: "ru-RU",
  args: [`--disable-extensions-except=${EXT}`, `--load-extension=${EXT}`],
});

// По умолчанию расширение смотрит на боевой сервер — проверка ставит адрес явно,
// иначе локальный прогон незаметно шёл бы в прод.
{
  let [worker] = context.serviceWorkers();
  if (!worker) worker = await context.waitForEvent("serviceworker");
  await worker.evaluate((base) => chrome.storage.sync.set({ serverUrl: base }), BASE);
}

const page = await context.newPage();
const log = (...args) => console.log(new Date().toISOString().slice(11, 19), ...args);
// консоль страницы и фрейма — единственный способ увидеть, почему что-то не поднялось
page.on("console", (msg) => {
  if (["error", "warning"].includes(msg.type())) log(`[${msg.type()}]`, msg.text().slice(0, 300));
});
page.on("pageerror", (err) => log("[pageerror]", String(err).slice(0, 300)));

try {
  await page.goto("https://zakupki.mos.ru/", { waitUntil: "domcontentloaded", timeout: 60000 });
  await page.locator('[data-testid="cookies-agreement-banner__confirm-button"]').first()
    .click({ timeout: 4000 }).catch(() => {});

  // хост нулевого размера — «невидимый» для Playwright, ждём просто появления в DOM
  const host = page.locator("#valerchik-extension");
  await host.waitFor({ state: "attached", timeout: 20000 });
  log("расширение вставило кнопку");

  const button = page.locator("#valerchik-extension .vk-btn");
  await button.click();
  // чат вложен дважды: страница расширения chat.html, а в ней — собранный виджет
  const frame = page.frameLocator("#valerchik-extension iframe.vk-frame").frameLocator("#widget");
  await frame.locator(".hero-chip").first().waitFor({ timeout: 90000 });
  log("чат открылся во фрейме");
  await page.mouse.move(10, 10);
  await page.waitForTimeout(700);
  await page.screenshot({ path: `${OUT}/extension-chat.png` });

  // Тема: переключатель в подвале чата красит не только чат — кнопка и
  // карточки подсказок живут на самом портале, их рисует контент-скрипт.
  // Проверяем именно цвет: атрибут можно проставить и при мёртвых стилях.
  const btnColor = () => host.evaluate(
    (el) => getComputedStyle(el.shadowRoot.querySelector(".vk-btn")).backgroundColor,
  );
  const lightColor = await btnColor();
  await frame.locator('button[title="Включить тёмную тему"]').click();
  await page.waitForFunction(
    () => document.getElementById("valerchik-extension")?.dataset.theme === "dark",
    null, { timeout: 15000 },
  );
  await page.waitForTimeout(900);   // волна перекраски в чате
  const darkColor = await btnColor();
  if (darkColor === lightColor) throw new Error(`кнопка не перекрасилась: ${darkColor}`);
  await page.screenshot({ path: `${OUT}/extension-dark.png` });
  log(`тёмная тема доехала до кнопки на портале: ${lightColor} -> ${darkColor}`);
  await frame.locator('button[title="Включить светлую тему"]').click();
  await page.waitForFunction(
    () => document.getElementById("valerchik-extension")?.dataset.theme === "light",
    null, { timeout: 15000 },
  );
  if ((await host.getAttribute("data-theme")) !== "light") throw new Error("тема не вернулась в светлую");

  // роль — поставщик, вопрос про регистрацию
  await frame.locator('.audience-toggle__option[role="radio"]').first().click();
  await page.waitForTimeout(400);
  await frame.locator("textarea").fill("Как зарегистрироваться на портале?");
  await frame.locator("textarea").press("Enter");
  const guideButton = frame.locator("button", { hasText: "Показать на портале" });
  await guideButton.waitFor({ timeout: 90000 });
  log("ответ с кнопкой «Показать на портале»");
  await page.waitForTimeout(500);
  await page.screenshot({ path: `${OUT}/extension-answer.png` });

  // элемент шага найден — спот вырезан из затемнения без «мягкого» режима
  const locked = "#valerchik-extension .vk-spot--on:not(.vk-spot--soft)";

  await guideButton.click();
  const card = page.locator("#valerchik-extension .vk-card--on");
  await card.waitFor({ timeout: 20000 });
  await page.locator(locked).waitFor({ timeout: 20000 });
  await page.waitForTimeout(1500);   // маскот дошёл до карточки
  const title1 = await card.locator(".vk-card__title").innerText();
  log("шаг 1:", title1);
  await page.screenshot({ path: `${OUT}/extension-step1.png` });

  // Человек нажимает подсвеченную кнопку портала. Дальше портал ведёт себя
  // по-разному: то сразу на форму регистрации Keycloak, то на форму входа со
  // ссылкой «Нет аккаунта? Зарегистрируйтесь». Подсказка обязана пережить оба
  // случая: в первом — перескочить шаг со ссылкой, во втором — показать её.
  if (!title1.includes("Зарегистрироваться")) throw new Error(`Неожиданный шаг 1: «${title1}»`);
  await page.locator('[data-testid="top-menu__register-btn"]').first().click();
  await page.waitForURL(/openid-connect\/(registrations|auth)/, { timeout: 60000 });
  const onRegistration = /openid-connect\/registrations/.test(page.url());
  log("портал увёл на:", onRegistration ? "форму регистрации" : "форму входа");

  const card2 = page.locator("#valerchik-extension .vk-card--on");
  await card2.waitFor({ timeout: 30000 });
  await page.locator(locked).waitFor({ timeout: 30000 });
  await page.waitForTimeout(1500);
  const title2 = await card2.locator(".vk-card__title").innerText();
  const count2 = await card2.locator(".vk-card__count").innerText();
  log("после клика:", title2, `(${count2})`);
  await page.screenshot({ path: `${OUT}/extension-step2.png` });

  if (onRegistration) {
    // шаг со ссылкой пропущен движком: его страницы портал не показал
    if (!title2.includes("способ регистрации") || !count2.startsWith("3")) {
      throw new Error(`Неожиданный шаг после клика: «${title2}» (${count2})`);
    }
    // «Назад» — к пропущенному шагу: его страница другая, движок переходит на форму входа
    await card2.locator('[data-act="prev"]').click();
    await page.waitForURL(/openid-connect\/auth/, { timeout: 60000 });
    const card3 = page.locator("#valerchik-extension .vk-card--on");
    await card3.waitFor({ timeout: 30000 });
    await page.locator(locked).waitFor({ timeout: 30000 });   // ссылка «Нет аккаунта? Зарегистрируйтесь»
    const title3 = await card3.locator(".vk-card__title").innerText();
    log("шаг назад:", title3);
    if (!title3.includes("Зарегистрируйтесь")) throw new Error(`Неожиданный шаг назад: «${title3}»`);
  } else if (!title2.includes("Зарегистрируйтесь")) {
    throw new Error(`Неожиданный шаг на форме входа: «${title2}» (${count2})`);
  }
  await page.waitForTimeout(800);
  await page.screenshot({ path: `${OUT}/extension-step-login.png` });
  const card3 = page.locator("#valerchik-extension .vk-card--on");
  // «Далее» на шаге «нажмите» нажимает за человека — ссылка ведёт на форму регистрации
  await card3.locator('[data-act="next"]').click();
  await page.waitForURL(/login-actions\/registration/, { timeout: 60000 });
  await page.locator("#valerchik-extension .vk-card--on").waitFor({ timeout: 30000 });
  log("«Далее» нажал ссылку за человека:", await page.locator("#valerchik-extension .vk-card__title").innerText());
  await page.keyboard.press("Escape");
  await page.locator("#valerchik-extension .vk-card").waitFor({ state: "detached", timeout: 5000 });
  log("Esc закрыл подсказки");
  console.log("E2E OK");
} catch (error) {
  await page.screenshot({ path: `${OUT}/extension-debug.png` }).catch(() => {});
  for (const f of page.frames()) log("frame:", f.url().slice(0, 120));
  throw error;
} finally {
  await context.close();
}
