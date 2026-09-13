/**
 * Валерчик на чистом JS — тот же рисунок и те же анимации, что в
 * frontend/src/components/Mascot.tsx и mascot.css, только без React: на чужой
 * странице расширению нужен маскот весом в несколько килобайт, а не сборка.
 *
 * Состояния: idle (моргает, зрачки следят за курсором), thinking (перебирает
 * карточки картотеки), speaking, sorry, alert, happy. Разметка и классы
 * совпадают с React-версией намеренно — правки внешности переносятся копированием.
 */
(() => {
  const VK = (globalThis.VK ||= {});

  const SVG = `
<div class="mascot__float">
  <svg class="mascot__layer mascot__layer--tail" viewBox="0 0 120 120" aria-hidden="true">
    <path d="M84 99 C99 103 110 95 108 83 C107 74 98 71 95 77" fill="none" stroke="#fff" stroke-width="8.6" stroke-linecap="round"/>
    <path d="M84 99 C99 103 110 95 108 83 C107 74 98 71 95 77" fill="none" stroke="#f0a6b3" stroke-width="4.6" stroke-linecap="round"/>
  </svg>
  <svg class="mascot__layer" viewBox="0 0 120 120" aria-hidden="true">
    <defs>
      <linearGradient id="m-fur" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stop-color="#a2a8b6"/><stop offset="100%" stop-color="#868d9d"/></linearGradient>
      <linearGradient id="m-ear" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stop-color="#f9c8ce"/><stop offset="100%" stop-color="#f3aeb9"/></linearGradient>
      <linearGradient id="m-belly" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stop-color="#fbf6f3"/><stop offset="100%" stop-color="#f2ebe6"/></linearGradient>
    </defs>
    <circle class="mascot__ring" cx="60" cy="62" r="50"/>
    <ellipse cx="60" cy="115" rx="25" ry="3.6" fill="#8b7a80" opacity="0.16"/>
    <g class="mascot__body">
      <g class="mascot__sticker">
        <circle cx="30" cy="31" r="19" fill="#fff"/><circle cx="90" cy="31" r="19" fill="#fff"/>
        <path d="M60 20 C81 20 94 34 94 53 C94 62 91 69 88 74 C93 81 95 89 95 96 C95 108 81 114 60 114 C39 114 25 108 25 96 C25 89 27 81 32 74 C29 69 26 62 26 53 C26 34 39 20 60 20 Z" fill="#fff"/>
      </g>
      <g class="mascot__ear mascot__ear--left"><circle cx="30" cy="31" r="15" fill="url(#m-fur)"/><circle cx="30.5" cy="31.5" r="10.8" fill="url(#m-ear)"/></g>
      <g class="mascot__ear mascot__ear--right"><circle cx="90" cy="31" r="15" fill="url(#m-fur)"/><circle cx="89.5" cy="31.5" r="10.8" fill="url(#m-ear)"/></g>
      <path d="M60 24 C79 24 90 37 90 54 C90 62 87 68 84 73 C89 80 91 87 91 94 C91 105 79 110 60 110 C41 110 29 105 29 94 C29 87 31 80 36 73 C33 68 30 62 30 54 C30 37 41 24 60 24 Z" fill="url(#m-fur)"/>
      <ellipse cx="60" cy="94" rx="17.5" ry="14.5" fill="url(#m-belly)"/>
      <ellipse class="mascot__paw mascot__paw--left" cx="43" cy="92" rx="5.4" ry="7" fill="#aab0bd" stroke="#8f96a5" stroke-width="0.8"/>
      <ellipse class="mascot__paw mascot__paw--right" cx="77" cy="92" rx="5.4" ry="7" fill="#aab0bd" stroke="#8f96a5" stroke-width="0.8"/>
      <path d="M31 47 C32 30 44 25 60 25 C76 25 88 30 89 47" fill="none" stroke="#2b303a" stroke-width="3.6" stroke-linecap="round"/>
      <circle cx="31" cy="49" r="6.2" fill="#2b303a"/><circle cx="89" cy="49" r="6.2" fill="#2b303a"/>
      <path d="M89 55 C90 64 84 69 78 70" fill="none" stroke="#2b303a" stroke-width="2" stroke-linecap="round"/>
      <circle cx="77" cy="70" r="2.9" fill="#d92b3a"/>
      <path d="M44 42.5 Q49 39.5 54 42.5" fill="none" stroke="#7d8494" stroke-width="1.5" stroke-linecap="round" opacity="0.7"/>
      <path d="M76 42.5 Q71 39.5 66 42.5" fill="none" stroke="#7d8494" stroke-width="1.5" stroke-linecap="round" opacity="0.7"/>
      <path class="mascot__brow mascot__brow--left" d="M43 44 L53 46" fill="none" stroke="#3c424e" stroke-width="2.1" stroke-linecap="round"/>
      <path class="mascot__brow mascot__brow--right" d="M77 44 L67 46" fill="none" stroke="#3c424e" stroke-width="2.1" stroke-linecap="round"/>
      <ellipse cx="39" cy="64" rx="6.2" ry="4.4" fill="#f3a3b2" opacity="0.62"/>
      <ellipse cx="81" cy="64" rx="6.2" ry="4.4" fill="#f3a3b2" opacity="0.62"/>
      <g class="mascot__eyes">
        <ellipse cx="49" cy="56" rx="9.6" ry="10.6" fill="#ffffff"/><ellipse cx="71" cy="56" rx="9.6" ry="10.6" fill="#ffffff"/>
        <g class="mascot__pupil-group">
          <ellipse cx="49" cy="56.6" rx="6.9" ry="8" fill="#20242c"/><ellipse cx="71" cy="56.6" rx="6.9" ry="8" fill="#20242c"/>
          <circle cx="46.7" cy="53.4" r="2.4" fill="#fff"/><circle cx="68.7" cy="53.4" r="2.4" fill="#fff"/>
          <circle cx="51.2" cy="59.6" r="1.15" fill="#fff" opacity="0.9"/><circle cx="73.2" cy="59.6" r="1.15" fill="#fff" opacity="0.9"/>
        </g>
      </g>
      <ellipse cx="60" cy="69" rx="13.5" ry="10.5" fill="#faf5f2"/>
      <ellipse cx="60" cy="64.5" rx="4.2" ry="3.2" fill="#ef94a4"/>
      <path class="mascot__mouth" d="M55 70.5 Q60 75.5 65 70.5" fill="none" stroke="#3c424e" stroke-width="1.9" stroke-linecap="round"/>
      <g stroke="#c6ccd6" stroke-width="1.4" stroke-linecap="round">
        <path d="M47 64 L34 61.5"/><path d="M47 67.5 L33 68"/><path d="M47 71 L34.5 74"/>
        <path d="M73 64 L86 61.5"/><path d="M73 67.5 L87 68"/><path d="M73 71 L85.5 74"/>
      </g>
    </g>
    <g class="mascot__thought"><circle cx="101" cy="20" r="3.4" fill="#c9ccd4"/><circle cx="109" cy="12" r="5" fill="#dfe2e8"/></g>
    <g class="mascot__cardfile">
      <g class="mascot__cards">
        <rect class="mascot__carditem" x="7" y="84" width="8" height="13" rx="2" fill="#f4f5f7" stroke="#c9ccd4" stroke-width="0.8"/>
        <rect class="mascot__carditem" x="15" y="84" width="8" height="13" rx="2" fill="#eef0f3" stroke="#c9ccd4" stroke-width="0.8"/>
        <rect class="mascot__carditem" x="23" y="84" width="8" height="13" rx="2" fill="#f4f5f7" stroke="#c9ccd4" stroke-width="0.8"/>
        <rect class="mascot__carditem" x="31" y="84" width="8" height="13" rx="2" fill="#eef0f3" stroke="#c9ccd4" stroke-width="0.8"/>
      </g>
      <rect x="3" y="96" width="40" height="17" rx="4" fill="#5b626f"/><rect x="3" y="96" width="40" height="4" rx="2" fill="#6d7481"/><rect x="18" y="103" width="10" height="4" rx="2" fill="#9aa0ae"/>
    </g>
    <g class="mascot__sparks" stroke="#f5a623" stroke-width="2" stroke-linecap="round">
      <path d="M12 42 l0 7 M8.5 45.5 l7 0"/><path d="M108 66 l0 7 M104.5 69.5 l7 0"/><path d="M17 104 l0 5 M14.5 106.5 l5 0"/>
    </g>
  </svg>
</div>`;

  // Стили маскота — те же, что в mascot.css (без режимов fixing/listening: у
  // подсказок на портале нет ни починки раскладки, ни микрофона).
  VK.css += `
.mascot { position: relative; flex-shrink: 0; }
.mascot svg { overflow: visible; display: block; }
.mascot:not(.mascot--idle):not(.mascot--happy) .mascot__pupil-group { transition: transform 0.25s var(--ease-out-soft); }
.mascot__ring { fill: var(--color-brand-soft); transform-box: view-box; transform-origin: 60px 62px; transition: fill 0.4s ease; will-change: transform; }
.mascot--thinking .mascot__ring { animation: mascot-ring-pulse 1.5s ease-in-out infinite; }
.mascot--speaking .mascot__ring { animation: mascot-ring-pulse 0.85s ease-in-out infinite; }
@keyframes mascot-ring-pulse { 0%, 100% { transform: scale(0.94); opacity: 0.7; } 50% { transform: scale(1.04); opacity: 1; } }
.mascot__float { position: relative; width: 100%; height: 100%; }
.mascot__layer { position: absolute; inset: 0; width: 100%; height: 100%; }
.mascot__layer--tail { transform-origin: 70% 82.5%; animation: mascot-tail 2.8s ease-in-out infinite; will-change: transform; }
.mascot__body { transform-origin: 60px 72px; }
.mascot__ear { transform-box: fill-box; transform-origin: bottom center; transition: transform 0.3s var(--ease-slime); }
.mascot__paw { transform-box: fill-box; transform-origin: center; transition: transform 0.42s var(--ease-slime); }
.mascot--thinking .mascot__paw { transition: none; }
.mascot__mouth { transform-box: fill-box; transform-origin: center; }
.mascot__brow { opacity: 0; transition: opacity 0.2s, transform 0.2s; transform-box: fill-box; transform-origin: center; }
.mascot__thought, .mascot__sparks { opacity: 0; transition: opacity 0.25s; }
.mascot__eyes { transform-box: view-box; transform-origin: 60px 56px; animation: mascot-blink 5.4s linear infinite; will-change: transform; }
@keyframes mascot-tail { 0%, 100% { transform: rotate(0deg); } 50% { transform: rotate(9deg); } }
@keyframes mascot-blink { 0%, 95.4%, 100% { transform: scaleY(1); } 96.4% { transform: scaleY(0.06); } 97.4% { transform: scaleY(1); } }
.mascot__cardfile { opacity: 0; transform: translateY(10px) scale(0.7); transform-origin: 23px 105px; transition: opacity 0.25s, transform 0.35s var(--ease-slime); }
.mascot__carditem { transform-origin: bottom center; transform-box: fill-box; }
.mascot--thinking .mascot__cardfile { opacity: 1; transform: translateY(0) scale(1); }
.mascot--thinking .mascot__pupil-group { transform: translate(-2px, 2.5px); }
.mascot--thinking .mascot__thought { opacity: 1; animation: mascot-pulse 1.2s ease-in-out infinite; }
.mascot--thinking .mascot__body { animation: mascot-lean 1.4s ease-in-out infinite; }
.mascot--thinking .mascot__paw--left { animation: mascot-riffle 0.44s ease-in-out infinite; }
.mascot--thinking .mascot__ear--left { animation: mascot-ear-twitch 0.9s ease-in-out infinite; }
.mascot--thinking .mascot__carditem { animation: mascot-cardflip 0.8s ease-in-out infinite; }
.mascot--thinking .mascot__carditem:nth-child(2) { animation-delay: 0.08s; }
.mascot--thinking .mascot__carditem:nth-child(3) { animation-delay: 0.16s; }
.mascot--thinking .mascot__carditem:nth-child(4) { animation-delay: 0.24s; }
@keyframes mascot-pulse { 0%, 100% { transform: scale(1); } 50% { transform: scale(1.15); } }
@keyframes mascot-lean { 0%, 100% { transform: rotate(-4deg) translate(-2px, 1px); } 50% { transform: rotate(1deg) translate(0, 2px); } }
@keyframes mascot-riffle { 0%, 100% { transform: translate(-14px, -4px) rotate(-20deg); } 50% { transform: translate(-17px, 0) rotate(-36deg); } }
@keyframes mascot-ear-twitch { 0%, 100% { transform: rotate(0); } 30% { transform: rotate(-8deg); } }
@keyframes mascot-cardflip { 0%, 60%, 100% { transform: rotate(0) translateY(0); } 25% { transform: rotate(-16deg) translateY(-5px); } }
.mascot--speaking .mascot__mouth { animation: mascot-talk 0.32s ease-in-out infinite; }
@keyframes mascot-talk { 0%, 100% { transform: scaleY(1); } 50% { transform: scaleY(1.7) translateY(1px); } }
.mascot--sorry .mascot__brow { opacity: 1; }
.mascot--sorry .mascot__brow--left { transform: rotate(14deg); }
.mascot--sorry .mascot__brow--right { transform: rotate(-14deg); }
.mascot--sorry .mascot__ear--left { transform: rotate(-24deg) translateY(3px); }
.mascot--sorry .mascot__ear--right { transform: rotate(24deg) translateY(3px); }
.mascot--sorry .mascot__layer--tail { animation: none; transform: rotate(14deg); }
.mascot--alert .mascot__ring { fill: oklch(54% 0.21 25 / 0.18); }
.mascot--alert .mascot__brow { opacity: 1; }
.mascot--alert .mascot__brow--left { transform: rotate(-12deg); }
.mascot--alert .mascot__brow--right { transform: rotate(12deg); }
.mascot--alert .mascot__float { animation: mascot-shake 0.5s ease-in-out; }
@keyframes mascot-shake { 0%, 100% { transform: translateX(0); } 25% { transform: translateX(-2px); } 75% { transform: translateX(2px); } }
.mascot--happy .mascot__sparks { opacity: 0.75; transform-origin: 60px 60px; animation: mascot-twinkle 2.4s ease-in-out infinite; }
@keyframes mascot-twinkle { 0%, 100% { opacity: 0.55; transform: scale(0.97); } 50% { opacity: 0.85; transform: scale(1.03); } }
.mascot--happy .mascot__ear--left { transform: rotate(4deg) scale(1.04); }
.mascot--happy .mascot__ear--right { transform: rotate(-4deg) scale(1.04); }
.mascot--happy .mascot__layer--tail { animation: mascot-tail 1.5s ease-in-out infinite; }
`;

  const MOODS = new Set(["idle", "thinking", "speaking", "sorry", "alert", "happy"]);
  const MAX_X = 2.6;
  const MAX_Y = 1.9;

  /**
   * Маскот заданного размера. setMood — смена состояния; зрачки в idle/happy
   * следят за курсором (упрощённый вариант useEyeTracking без сглаживания
   * по кадрам: подсказки живут секунды, и лишний rAF-цикл им ни к чему).
   */
  VK.createMascot = (size = 64, mood = "idle") => {
    const el = document.createElement("div");
    el.className = `mascot mascot--${mood}`;
    el.style.width = `${size}px`;
    el.style.height = `${size}px`;
    el.setAttribute("aria-hidden", "true");
    el.innerHTML = SVG;
    const pupils = el.querySelector(".mascot__pupil-group");
    let current = mood;

    const onMove = (e) => {
      if (!(current === "idle" || current === "happy") || !pupils) return;
      const box = el.getBoundingClientRect();
      if (!box.width) return;
      const dx = (e.clientX - (box.left + box.width / 2)) / (window.innerWidth / 2);
      const dy = (e.clientY - (box.top + box.height / 2)) / (window.innerHeight / 2);
      const len = Math.hypot(dx, dy) || 1;
      const k = Math.min(len, 1) / len;
      pupils.style.transform = `translate(${(dx * k * MAX_X).toFixed(2)}px, ${(dy * k * MAX_Y).toFixed(2)}px)`;
    };
    const noHover = window.matchMedia("(hover: none)").matches
      || window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (!noHover) window.addEventListener("pointermove", onMove, { passive: true });

    return {
      el,
      get mood() { return current; },
      setMood(next) {
        if (!MOODS.has(next) || next === current) return;
        el.classList.remove(`mascot--${current}`);
        el.classList.add(`mascot--${next}`);
        current = next;
        // вне idle/happy зрачками управляет CSS состояния
        if (pupils && !(next === "idle" || next === "happy")) pupils.style.transform = "";
      },
      destroy() { window.removeEventListener("pointermove", onMove); el.remove(); },
    };
  };

  VK.closeIcon = `<svg class="vk-icon-close" viewBox="0 0 24 24" fill="none" stroke="#fff" stroke-width="2" stroke-linecap="round" aria-hidden="true"><path d="M18 6 6 18M6 6l12 12"/></svg>`;
})();
