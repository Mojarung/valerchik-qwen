/**
 * Стили расширения — одной строкой в Shadow DOM.
 *
 * Токены и «слаймовые» кривые повторяют frontend/src/styles/global.css один в
 * один: чат в iframe и подсказки поверх портала должны выглядеть одним
 * продуктом. Файл со стилями нельзя подключить как обычный CSS: контент-скрипт
 * живёт на чужой странице, и её глобальные правила (Semantic UI портала)
 * переопределяли бы наши. Shadow DOM изолирует в обе стороны.
 */
(() => {
  const VK = (globalThis.VK ||= {});

  VK.css = `
/* Токены — те же, что во frontend/src/styles/global.css. Оба варианта цвета
   держим в одном месте через light-dark(): раздельные блоки для светлой и
   тёмной означали бы две копии палитры, которые неминуемо разъезжаются.
   Какая тема действует, решает color-scheme элемента-хоста: его ставит main.js
   в inline-стиле (там же all: initial, который стёр бы объявление отсюда).
   По умолчанию системная, а когда человек выбрал тему в чате, тот присылает её
   хозяину — и кнопка, карточки подсказок и маскот на портале перекрашиваются
   вместе с чатом. */
:host, .vk-root {
  --color-brand: light-dark(oklch(54% 0.21 25), oklch(64% 0.19 25));
  --color-brand-dark: light-dark(oklch(46% 0.19 25), oklch(57% 0.18 25));
  --color-brand-soft: light-dark(oklch(96% 0.02 25), oklch(31% 0.06 25));
  --color-ink: light-dark(oklch(21% 0.01 260), oklch(93% 0.006 260));
  --color-ink-soft: light-dark(oklch(45% 0.01 260), oklch(68% 0.012 260));
  --color-surface: light-dark(oklch(99% 0.002 260), oklch(22% 0.008 260));
  --color-surface-2: light-dark(oklch(96.5% 0.004 260), oklch(18% 0.008 260));
  --color-line: light-dark(oklch(90% 0.005 260), oklch(31% 0.01 260));
  --color-ok: light-dark(oklch(60% 0.15 150), oklch(65% 0.15 150));
  --color-warn: light-dark(oklch(70% 0.15 80), oklch(75% 0.15 80));
  /* тень — не цвет, light-dark() её не соберёт: в тёмной теме другой набор слоёв */
  --shadow-card: 0 2px 10px oklch(54% 0.21 25 / 0.05), 0 1px 3px oklch(30% 0.02 260 / 0.05);
  --font-sans: "Inter", "Segoe UI", system-ui, -apple-system, sans-serif;
  --ease-slime: cubic-bezier(0.34, 1.56, 0.64, 1);
  --ease-out-soft: cubic-bezier(0.16, 1, 0.3, 1);
  --z: 2147483000;
  font-family: var(--font-sans);
  color: var(--color-ink);
  font-size: 14px;
  line-height: 1.45;
  -webkit-font-smoothing: antialiased;
}
@media (prefers-color-scheme: dark) {
  :host(:not([data-theme="light"])), .vk-root:not([data-theme="light"]) {
    --shadow-card: 0 2px 12px oklch(0% 0 0 / 0.45), inset 0 1px 0 oklch(100% 0 0 / 0.04);
  }
}
:host([data-theme="dark"]), .vk-root[data-theme="dark"] {
  --shadow-card: 0 2px 12px oklch(0% 0 0 / 0.45), inset 0 1px 0 oklch(100% 0 0 / 0.04);
}
*, *::before, *::after { box-sizing: border-box; }
button { font: inherit; }

/* ---- кнопка чата и фрейм (раскладка как в public/embed.js) ----
   На кнопке живой маскот — как на стартовом экране чата: светлый круг с
   кольцом-настроением, а не силуэт на красном. Крестик закрытия сменяет его
   тем же поворотом, что у виджета на сайте. */
.vk-btn {
  position: fixed; bottom: 20px; z-index: var(--z);
  width: 64px; height: 64px; border-radius: 50%; cursor: pointer;
  background: var(--color-surface); border: 1px solid var(--color-line);
  box-shadow: 0 6px 24px oklch(54% 0.21 25 / 0.28), var(--shadow-card);
  display: flex; align-items: center; justify-content: center;
  transition: transform .4s var(--ease-slime), box-shadow .2s ease, border-color .2s ease;
}
.vk-btn--right { right: 20px; }
.vk-btn--left { left: 20px; }
.vk-btn:hover { transform: translateY(-3px) scale(1.08); border-color: var(--color-brand); }
.vk-btn:active { transform: scale(.92); }
.vk-btn__face { transition: transform .4s var(--ease-slime), opacity .2s ease; }
.vk-btn.open .vk-btn__face { transform: scale(0) rotate(90deg); opacity: 0; position: absolute; }
.vk-btn > svg.vk-icon-close {
  width: 30px; height: 30px; stroke: var(--color-brand);
  transition: transform .4s var(--ease-slime);
}
.vk-btn:not(.open) > svg.vk-icon-close { transform: scale(0) rotate(-90deg); position: absolute; }
.vk-btn__badge {
  position: absolute; top: -2px; right: -2px; min-width: 18px; height: 18px; padding: 0 5px;
  border-radius: 999px; background: var(--color-brand); color: #fff;
  font-size: 11px; font-weight: 700; line-height: 18px; box-shadow: 0 1px 4px rgba(0,0,0,.25);
}
.vk-frame {
  position: fixed; bottom: 92px; z-index: var(--z);
  width: min(384px, calc(100vw - 32px)); height: min(620px, calc(100vh - 120px));
  border: none; border-radius: 24px; background: var(--color-surface);
  box-shadow: 0 12px 48px rgba(0,0,0,.22);
  transition: transform .45s var(--ease-slime), opacity .25s ease;
}
.vk-frame--right { right: 20px; transform-origin: bottom right; }
.vk-frame--left { left: 20px; transform-origin: bottom left; }
.vk-frame.hidden { transform: scale(.55) translateY(30px); opacity: 0; pointer-events: none; }

/* ---- подсказки на портале ---- */
.vk-spot {
  position: fixed; z-index: calc(var(--z) - 2); pointer-events: none;
  border-radius: 12px;
  box-shadow: 0 0 0 3px var(--color-brand), 0 0 0 9999px oklch(15% 0.02 260 / 0.42);
  transition: top .5s var(--ease-slime), left .5s var(--ease-slime),
              width .5s var(--ease-slime), height .5s var(--ease-slime), opacity .3s ease;
  opacity: 0;
}
.vk-spot--on { opacity: 1; }
.vk-spot--soft { box-shadow: 0 0 0 9999px oklch(15% 0.02 260 / 0.3); }
.vk-spot::after {
  content: ""; position: absolute; inset: -3px; border-radius: 14px;
  border: 3px solid var(--color-brand); opacity: 0;
  animation: vk-ring 1.6s ease-out infinite;
}
.vk-spot--soft::after { display: none; }
@keyframes vk-ring {
  0% { transform: scale(1); opacity: .7; }
  100% { transform: scale(1.12); opacity: 0; }
}

.vk-card {
  position: fixed; z-index: var(--z); width: 340px; max-width: calc(100vw - 24px);
  background: var(--color-surface); border: 1px solid var(--color-line);
  border-radius: 1.5rem; box-shadow: 0 12px 40px rgba(0,0,0,.18), var(--shadow-card);
  padding: 16px 18px 14px 18px;
  transition: top .5s var(--ease-slime), left .5s var(--ease-slime), opacity .25s ease,
              transform .45s var(--ease-slime);
  opacity: 0; transform: scale(.92) translateY(8px);
}
.vk-card--on { opacity: 1; transform: none; }
.vk-card__head { display: flex; align-items: center; gap: 8px; margin: 0 0 6px 0; }
.vk-card__kicker {
  font-size: 10px; font-weight: 700; letter-spacing: .06em; text-transform: uppercase;
  color: var(--color-brand); flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.vk-card__count { font-size: 11px; color: var(--color-ink-soft); white-space: nowrap; font-variant-numeric: tabular-nums; }
.vk-card__close {
  width: 28px; height: 28px; border-radius: 999px; border: 1px solid var(--color-line);
  background: var(--color-surface); color: var(--color-ink-soft); cursor: pointer;
  display: inline-flex; align-items: center; justify-content: center;
  transition: transform .35s var(--ease-slime), color .15s ease, border-color .15s ease;
}
.vk-card__close:hover { color: var(--color-brand); border-color: var(--color-brand); transform: scale(1.08); }
.vk-card__title { font-size: 15px; font-weight: 700; margin: 0 0 4px 0; line-height: 1.3; }
.vk-card__text { font-size: 13px; color: var(--color-ink); margin: 0; }
.vk-card__note {
  margin: 10px 0 0 0; padding: 8px 10px; border-radius: 12px; font-size: 12px;
  background: var(--color-brand-soft); color: var(--color-brand); display: flex; gap: 6px; align-items: flex-start;
}
.vk-card__actions { display: flex; align-items: center; gap: 8px; margin-top: 12px; }
.vk-card__dots { display: flex; gap: 4px; flex: 1; }
.vk-card__dot { width: 6px; height: 6px; border-radius: 999px; background: var(--color-line); transition: background .2s, transform .35s var(--ease-slime); }
.vk-card__dot--done { background: oklch(54% 0.21 25 / 0.45); }
.vk-card__dot--now { background: var(--color-brand); transform: scale(1.4); }
.vk-btn2 {
  display: inline-flex; align-items: center; justify-content: center; gap: 6px;
  min-height: 36px; padding: 6px 14px; border-radius: 999px; font-size: 13px; font-weight: 600;
  cursor: pointer; white-space: nowrap; user-select: none;
  transition: transform .35s var(--ease-slime), background .15s ease, color .15s ease, border-color .15s ease;
}
.vk-btn2:focus-visible { outline: 2px solid var(--color-brand); outline-offset: 2px; }
.vk-btn2--primary { background: var(--color-brand); color: #fff; border: none; box-shadow: 0 4px 14px oklch(54% 0.21 25 / 0.28); }
.vk-btn2--primary:hover { background: var(--color-brand-dark); transform: translateY(-2px) scale(1.03); }
.vk-btn2--primary:active { transform: scale(.96); }
.vk-btn2--ghost { background: var(--color-surface); color: var(--color-ink); border: 1px solid var(--color-line); }
.vk-btn2--ghost:hover { border-color: var(--color-brand); color: var(--color-brand); background: var(--color-brand-soft); }
.vk-btn2[disabled] { opacity: .45; pointer-events: none; }

.vk-mascot {
  position: fixed; left: 0; top: 0; z-index: calc(var(--z) + 1); pointer-events: none;
  width: 64px; height: 64px; will-change: transform;
}
.vk-mascot__inner { width: 100%; height: 100%; transition: transform .3s var(--ease-slime); }
.vk-mascot--flip .vk-mascot__inner { transform: scaleX(-1); }
.vk-mascot__bob { width: 100%; height: 100%; }
.vk-mascot--walking .vk-mascot__bob { animation: vk-bob .36s ease-in-out infinite; }
@keyframes vk-bob {
  0%, 100% { transform: translateY(0) rotate(-4deg); }
  50% { transform: translateY(-5px) rotate(4deg); }
}
.vk-bubble {
  position: fixed; z-index: calc(var(--z) + 1); pointer-events: none;
  max-width: 220px; padding: 8px 12px; border-radius: 16px 16px 16px 4px;
  background: var(--color-surface); border: 1px solid var(--color-line); box-shadow: var(--shadow-card);
  font-size: 12px; color: var(--color-ink); opacity: 0; transform: scale(.9);
  transition: opacity .25s ease, transform .35s var(--ease-slime);
}
.vk-bubble--on { opacity: 1; transform: none; }

/* ---- запись сценария (режим редактора) ---- */
.vk-rec {
  position: fixed; left: 20px; bottom: 20px; z-index: calc(var(--z) + 2);
  width: 320px; max-width: calc(100vw - 40px);
  background: var(--color-surface); border: 1px solid var(--color-brand);
  border-radius: 1.25rem; box-shadow: 0 12px 40px rgba(0,0,0,.2), var(--shadow-card);
  padding: 12px 14px;
}
.vk-rec__head { display: flex; align-items: center; gap: 8px; }
.vk-rec__dot {
  width: 9px; height: 9px; border-radius: 50%; background: var(--color-brand);
  animation: vk-rec-pulse 1.4s ease-in-out infinite;
}
@keyframes vk-rec-pulse { 0%, 100% { opacity: 1; transform: scale(1); } 50% { opacity: .35; transform: scale(.75); } }
.vk-rec__title { font-size: 12px; font-weight: 700; flex: 1; }
.vk-rec__count { font-size: 11px; color: var(--color-ink-soft); font-variant-numeric: tabular-nums; }
.vk-rec__list { margin: 8px 0 0; padding: 0 0 0 16px; font-size: 12px; color: var(--color-ink); }
.vk-rec__list li { margin: 2px 0; }
.vk-rec__list em { color: var(--color-ink-soft); font-style: normal; }
.vk-rec__empty { list-style: none; margin-left: -16px; color: var(--color-ink-soft); }
.vk-rec__note { margin: 8px 0 0; font-size: 11px; color: var(--color-brand); }
.vk-rec__actions { display: flex; gap: 6px; margin-top: 10px; }
.vk-rec__actions .vk-btn2 { flex: 1; min-height: 32px; padding: 4px 10px; font-size: 12px; }

/* кнопка «записать сценарий» — под кнопкой чата, только в режиме редактора */
.vk-recbtn {
  position: fixed; bottom: 92px; z-index: var(--z);
  display: inline-flex; align-items: center; gap: 6px;
  padding: 7px 12px; border-radius: 999px; cursor: pointer;
  font-size: 12px; font-weight: 600;
  background: var(--color-surface); color: var(--color-brand);
  border: 1px solid var(--color-brand); box-shadow: var(--shadow-card);
  transition: transform .35s var(--ease-slime), background .15s ease;
}
.vk-recbtn--right { right: 20px; }
.vk-recbtn--left { left: 20px; }
.vk-recbtn:hover { transform: translateY(-2px) scale(1.03); background: var(--color-brand-soft); }
.vk-recbtn__dot { width: 8px; height: 8px; border-radius: 50%; background: var(--color-brand); }

@media (prefers-reduced-motion: reduce) {
  .vk-spot, .vk-card, .vk-frame, .vk-btn, .vk-bubble, .vk-mascot__inner { transition: none !important; }
  .vk-spot::after, .vk-mascot--walking .vk-mascot__bob { animation: none !important; }
  .vk-rec__dot { animation: none !important; }
}
`;
})();
