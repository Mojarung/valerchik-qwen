/** Геометрия ленты карусели: всё, что считается из DOM без состояния React. */

/** Допуск на дробный scrollLeft: при масштабе страницы и HiDPI упор редко
 *  приходится ровно в ноль, и без допуска стрелка «назад» в самом начале
 *  оставалась бы включённой. */
export const EDGE = 2;

/** Производные от позиции прокрутки: всё, что нужно шапке карусели. */
export interface Nav {
  /** Карточки не помещаются целиком — только тогда карусель вообще карусель. */
  overflow: boolean;
  atStart: boolean;
  atEnd: boolean;
  from: number;
  to: number;
}

export const NAV_IDLE: Nav = { overflow: false, atStart: true, atEnd: true, from: 1, to: 1 };

/** Плавность программной прокрутки спрашиваем в момент вызова, а не при
 *  монтировании: настройку системы можно переключить с открытым чатом, а
 *  глобальное правило reduced-motion из global.css про scroll-behavior не знает. */
export const scrollMotion = (): ScrollBehavior =>
  window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth";

/** Шаг сетки — расстояние между левыми краями соседних карточек.
 *  Считаем разностью offsetLeft, а не «ширина плюс gap из computed style»:
 *  разбор строки с пикселями врёт на дробных значениях при масштабе страницы,
 *  а offsetLeft не зависит от scrollLeft, поэтому замер стабилен в любой момент. */
export function stepOf(track: HTMLElement): number {
  const first = track.firstElementChild as HTMLElement | null;
  if (!first) return 1;
  const second = first.nextElementSibling as HTMLElement | null;
  return Math.max(1, second ? second.offsetLeft - first.offsetLeft : first.offsetWidth);
}

/** Сколько карточек в кадре целиком — но хотя бы одна. */
export const perViewOf = (track: HTMLElement, step: number) =>
  Math.max(1, Math.floor(track.clientWidth / step));

export function readNav(track: HTMLElement, total: number): Nav {
  const step = stepOf(track);
  const perView = perViewOf(track, step);
  const max = track.scrollWidth - track.clientWidth;
  const atEnd = track.scrollLeft >= max - EDGE;
  // Последняя «страница» неполная: у правого упора scrollLeft не кратен шагу, и
  // округление давало бы «5–7 из 8» там, где целиком видны 6–8. В упоре считаем
  // от конца — там показан ровно последний экран карточек.
  const from = atEnd
    ? Math.max(1, total - perView + 1)
    : Math.max(1, Math.min(total - perView + 1, Math.round(track.scrollLeft / step) + 1));
  return {
    overflow: max > EDGE,
    atStart: track.scrollLeft <= EDGE,
    atEnd,
    from,
    to: Math.min(total, from + perView - 1),
  };
}

export const sameNav = (a: Nav, b: Nav) =>
  a.overflow === b.overflow && a.atStart === b.atStart && a.atEnd === b.atEnd
  && a.from === b.from && a.to === b.to;
