/**
 * Линтер расширения.
 *
 * Сборки у расширения нет: браузер грузит файлы как есть, и опечатка в имени
 * переменной обнаруживается только на живом портале, где её никто не увидит —
 * контент-скрипт падает молча, страница продолжает работать. Поэтому `no-undef`
 * здесь не косметика, а единственная замена компилятору.
 *
 * Три вида файлов живут по разным правилам: контент-скрипты и фон грузятся
 * тегом (обычные скрипты, общая область видимости, браузерные глобальные),
 * служебные .mjs и этот конфиг — модули под Node.
 */
import js from "@eslint/js";
import globals from "globals";

export default [
  {
    // widget/ — сборка frontend, лежит здесь готовой; node_modules чужой вовсе
    ignores: ["widget/**", "node_modules/**"],
  },
  js.configs.recommended,
  {
    files: ["background.js", "chat-frame.js", "content/*.js", "options/*.js"],
    languageOptions: {
      ecmaVersion: 2023,
      sourceType: "script",
      globals: { ...globals.browser, ...globals.webextensions },
    },
    rules: {
      "no-undef": "error",
      // «_» в начале имени — принятая в этом коде пометка «параметр по контракту,
      // но не нужен»: у слушателей расширения сигнатура фиксированная.
      // ignoreRestSiblings — про идиому «копия объекта без поля»:
      // `({ leadsTo, ...step }) => step` выбрасывает поле, а не забывает его.
      "no-unused-vars": [
        "error",
        { argsIgnorePattern: "^_", caughtErrors: "none", ignoreRestSiblings: true },
      ],
    },
  },
  {
    // e2e-скрипты гоняют браузер: часть кода в них — то, что выполняется уже на
    // странице (document, getComputedStyle), поэтому глобальные оба набора
    files: ["scripts/*.mjs"],
    languageOptions: {
      ecmaVersion: 2023,
      sourceType: "module",
      globals: { ...globals.node, ...globals.browser, ...globals.webextensions },
    },
    rules: {
      "no-undef": "error",
      "no-unused-vars": ["error", { argsIgnorePattern: "^_", caughtErrors: "none" }],
    },
  },
  {
    files: ["eslint.config.js", "tests/*.mjs"],
    languageOptions: {
      ecmaVersion: 2023,
      sourceType: "module",
      globals: { ...globals.node },
    },
    rules: {
      "no-undef": "error",
      "no-unused-vars": ["error", { argsIgnorePattern: "^_", caughtErrors: "none" }],
    },
  },
];
