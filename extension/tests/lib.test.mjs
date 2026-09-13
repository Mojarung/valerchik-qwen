/**
 * Проверки общих функций расширения.
 *
 * Три вещи, которые нельзя проверить глазами на портале: переход по чужому
 * адресу, протухшее состояние подсказки и утечка введённых данных в запись
 * сценария. Ошибка в любой из них выглядит на экране совершенно нормально —
 * поэтому они и вынесены в `content/lib.js` чистыми функциями.
 *
 * Файл грузится импортом ради побочного эффекта: как и в браузере, он кладёт
 * себя в `globalThis.VK.lib` — другого способа экспорта у контент-скрипта нет.
 */
import assert from "node:assert/strict";
import test from "node:test";

import "../content/lib.js";

const lib = globalThis.VK.lib;
const PORTAL = "https://zakupki.mos.ru";
const MINUTE = 60 * 1000;

test("safeDestination: адрес без схемы уводит на чужой хост — отказ", () => {
  assert.equal(lib.safeDestination("//evil.tld/x", PORTAL), null);
});

test("safeDestination: относительный путь разворачивается в адрес того же портала", () => {
  assert.equal(lib.safeDestination("/contract/list", PORTAL), "https://zakupki.mos.ru/contract/list");
});

test("safeDestination: тот же хост по http — понижение схемы, отказ", () => {
  assert.equal(lib.safeDestination("http://zakupki.mos.ru/x", PORTAL), null);
});

test("safeDestination: чужой https-хост и javascript: — отказ", () => {
  assert.equal(lib.safeDestination("https://evil.tld/x", PORTAL), null);
  assert.equal(lib.safeDestination("javascript:alert(1)", PORTAL), null);
  assert.equal(lib.safeDestination("", PORTAL), null);
  assert.equal(lib.safeDestination(undefined, PORTAL), null);
});

test("safeDestination: второй адрес портала обслуживается наравне с основным", () => {
  assert.equal(
    lib.safeDestination("/auth/login", "https://old.zakupki.mos.ru"),
    "https://old.zakupki.mos.ru/auth/login",
  );
});

test("isValidGuideState: свежее состояние со ссылкой на сценарий", () => {
  const now = 1_000 * MINUTE;
  assert.equal(
    lib.isValidGuideState({ guideId: "login", step: 2, ts: now - MINUTE, navigated: false }, now),
    true,
  );
});

test("isValidGuideState: сценарий целиком в состоянии — старый формат, отказ", () => {
  const now = 1_000 * MINUTE;
  const fat = { guideId: "login", step: 2, ts: now, navigated: false, steps: [{ url: "/" }] };
  assert.equal(lib.isValidGuideState(fat, now), false);
});

test("isValidGuideState: метка старше получаса — отказ", () => {
  const now = 1_000 * MINUTE;
  assert.equal(
    lib.isValidGuideState({ guideId: "login", step: 0, ts: now - 31 * MINUTE, navigated: false }, now),
    false,
  );
});

test("isValidGuideState: битые поля — отказ", () => {
  const now = 1_000 * MINUTE;
  const ok = { guideId: "login", step: 1, ts: now, navigated: false };
  assert.equal(lib.isValidGuideState({ ...ok, guideId: "" }, now), false);
  assert.equal(lib.isValidGuideState({ ...ok, step: -1 }, now), false);
  assert.equal(lib.isValidGuideState({ ...ok, step: 1.5 }, now), false);
  assert.equal(lib.isValidGuideState({ ...ok, navigated: "yes" }, now), false);
  assert.equal(lib.isValidGuideState({ ...ok, ts: "1000" }, now), false);
  assert.equal(lib.isValidGuideState(null, now), false);
  assert.equal(lib.isValidGuideState("login", now), false);
});

test("isValidGuideState: возврат назад переживает переход между страницами", () => {
  const now = 1_000 * MINUTE;
  const state = { guideId: "login", step: 1, ts: now, navigated: true, back: true };
  assert.equal(lib.isValidGuideState(state, now), true);
  assert.equal(lib.isValidGuideState({ ...state, back: "yes" }, now), false);
});

test("ownText: введённое в поле значение в запись не попадает", () => {
  assert.equal(lib.ownText({ value: "7701234567", innerText: "", getAttribute: () => null }), "");
});

test("ownText: подпись берётся из разметки, а не из набранного текста", () => {
  const attrs = { "aria-label": "Открыть документы" };
  assert.equal(
    lib.ownText({ value: "секрет", innerText: "", getAttribute: (name) => attrs[name] || null }),
    "Открыть документы",
  );
  const placeholder = { placeholder: "ИНН организации" };
  assert.equal(
    lib.ownText({ value: "7701234567", innerText: "", getAttribute: (name) => placeholder[name] || null }),
    "ИНН организации",
  );
  assert.equal(lib.ownText({ innerText: "Документы", getAttribute: () => null }), "Документы");
  assert.equal(lib.ownText(null), "");
});

test("ownText: подпись из привязанного label", () => {
  const input = {
    innerText: "",
    value: "7701234567",
    getAttribute: () => null,
    labels: [{ innerText: "  ИНН \n организации " }],
  };
  assert.equal(lib.ownText(input), "ИНН организации");
});

test("portalOrigins: оба адреса портала, без завершающей косой", () => {
  assert.deepEqual(lib.portalOrigins, ["https://zakupki.mos.ru", "https://old.zakupki.mos.ru"]);
});
