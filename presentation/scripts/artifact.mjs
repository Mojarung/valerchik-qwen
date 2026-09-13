/**
 * Собранный dist/index.html → страница для публикации ссылкой (claude.ai Artifact):
 * без doctype/html/head/body, с явным фоном — хостинг сам оборачивает страницу.
 *   npm run build && node scripts/artifact.mjs
 */
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const html = readFileSync(resolve(here, "../dist/index.html"), "utf8");
const head = html.match(/<head>([\s\S]*?)<\/head>/)?.[1] ?? "";
const body = html.match(/<body>([\s\S]*?)<\/body>/)?.[1] ?? "";
const headRest = head
  .replace(/<meta charset[^>]*>/i, "")
  .replace(/<meta name="viewport"[^>]*>/i, "")
  .replace(/<title>[\s\S]*?<\/title>/i, "");

const page = `<title>Валерчик для жюри</title>
<style>html,body{margin:0;background:oklch(98.7% 0.004 40);color:oklch(21% 0.01 260)}</style>
${headRest.trim()}
${body.trim()}
`;
mkdirSync(resolve(here, "../out"), { recursive: true });
const out = resolve(here, "../out/valerchik-deck.html");
writeFileSync(out, page);
console.log(out, `${(page.length / 1024).toFixed(0)} КБ`);
