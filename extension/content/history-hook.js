/**
 * Слежение за навигацией портала — в главном мире страницы.
 *
 * Портал построен как SPA: адрес меняется через history.pushState, событий
 * перезагрузки не бывает. Подменить pushState из обычного контент-скрипта
 * нельзя: он живёт в изолированном мире, где свой объект history, и вызовы
 * портала до подмены не доходят. Поэтому этот файл объявлен в манифесте с
 * "world": "MAIN" — он выполняется в контексте самой страницы.
 *
 * Наружу отдаём одно DOM-событие: его слышат и подсказки, и чат, оставаясь в
 * изолированном мире. Ничего, кроме факта смены адреса, отсюда не уходит.
 */
(() => {
  if (window.__valerchikNavigationHook) return;
  window.__valerchikNavigationHook = true;

  const fire = () => {
    try {
      window.dispatchEvent(new Event("valerchik:navigate"));
    } catch { /* страница закрывается — событие уже никому не нужно */ }
  };

  for (const name of ["pushState", "replaceState"]) {
    const original = history[name];
    if (typeof original !== "function") continue;
    history[name] = function patched(...args) {
      const result = original.apply(this, args);
      fire();
      return result;
    };
  }
  window.addEventListener("popstate", fire);
})();
