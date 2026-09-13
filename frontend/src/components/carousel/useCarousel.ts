import { useCallback, useLayoutEffect, useRef, useState, type RefObject } from "react";
import { EDGE, NAV_IDLE, perViewOf, readNav, sameNav, scrollMotion, stepOf, type Nav } from "./geometry";

/** Меньше этого сдвиг мыши — дрожание руки на клике, а не листание. */
const DRAG_THRESHOLD = 5;
/** Сколько помним заказанную стрелкой позицию. Плавная прокрутка идёт сотни
 *  миллисекунд, и без этой памяти второй быстрый клик считался бы от точки
 *  посреди анимации — лента проезжала бы полстраницы вместо страницы. */
const AIM_TTL_MS = 700;

export interface Carousel {
  trackRef: RefObject<HTMLDivElement | null>;
  nav: Nav;
  /** Листнуть на кадр: сколько карточек видно, столько и проезжаем. */
  page: (dir: 1 | -1) => void;
  /** Обработчики, которые вешаются на саму ленту. */
  trackProps: {
    onScroll: () => void;
    onKeyDown: (e: React.KeyboardEvent<HTMLDivElement>) => void;
    onPointerDown: (e: React.PointerEvent<HTMLDivElement>) => void;
    onPointerMove: (e: React.PointerEvent<HTMLDivElement>) => void;
    onPointerUp: () => void;
    onPointerCancel: () => void;
    onLostPointerCapture: () => void;
    onClickCapture: (e: React.MouseEvent) => void;
  };
}

/**
 * Горизонтальная лента: листается пальцем, мышью, стрелками и клавиатурой.
 *
 * Позиция живёт в DOM (scrollLeft), а не в состоянии React: прокрутка с
 * прилипанием, инерцией на тач-экране, разведением осей и доводкой к
 * сфокусированной табом карточке — это ровно то, что браузер уже умеет сам и
 * делает в потоке композитора. Своя дорожка на transform всё это переписывала бы
 * вручную и всё равно потеряла бы горизонтальную прокрутку трекпадом. В React
 * попадают только производные для шапки — стрелки и счётчик.
 */
export function useCarousel(total: number): Carousel {
  const trackRef = useRef<HTMLDivElement>(null);
  const [nav, setNav] = useState<Nav>(NAV_IDLE);

  const frame = useRef(0);
  const sync = useCallback(() => {
    const track = trackRef.current;
    if (!track) return;
    // Замер — снаружи setNav: обновляющая функция обязана быть чистой, а в
    // StrictMode её зовут дважды, то есть вдвое чаще читают геометрию.
    // Внутрь уходит только сравнение: за свайп через две карточки это
    // два-три перерисовывания вместо сотни событий прокрутки.
    const next = readNav(track, total);
    setNav((was) => (sameNav(was, next) ? was : next));
  }, [total]);

  const onScroll = () => {
    if (frame.current) return;                 // события прокрутки идут пачкой — хватит одного замера на кадр
    frame.current = requestAnimationFrame(() => { frame.current = 0; sync(); });
  };

  useLayoutEffect(() => {
    const track = trackRef.current;
    if (!track) return;
    sync();                                    // до первого кадра, иначе шапка моргнёт появлением стрелок
    // Ширина пузыря меняется и от окна, и от размера виджета на чужой странице,
    // а медиазапросы про неё ничего не знают: в виджете sm: не срабатывает никогда.
    const observer = new ResizeObserver(sync);
    observer.observe(track);
    return () => {
      observer.disconnect();
      cancelAnimationFrame(frame.current);
      // Обнулить обязательно: отменённый кадр свой колбэк не выполнит, а
      // onScroll считает ненулевое значение признаком «замер уже заказан» —
      // после пересоздания эффекта карусель молча перестала бы обновлять шапку.
      frame.current = 0;
    };
  }, [sync]);

  // Куда мы уже приказали доехать: пока плавная прокрутка идёт, отсчитываем
  // следующий шаг отсюда, а не от позиции посреди анимации.
  const aim = useRef({ left: 0, until: 0 });

  const glide = useCallback((track: HTMLDivElement, left: number) => {
    const target = Math.max(0, Math.min(track.scrollWidth - track.clientWidth, left));
    aim.current = { left: target, until: performance.now() + AIM_TTL_MS };
    track.scrollTo({ left: target, behavior: scrollMotion() });
  }, []);

  const page = useCallback((dir: 1 | -1) => {
    const track = trackRef.current;
    if (!track) return;
    const step = stepOf(track);
    const base = performance.now() < aim.current.until ? aim.current.left : track.scrollLeft;
    // Округление к сетке: после свайпа лента может стоять между карточками, и
    // без него каждый следующий клик уносил бы всё дальше от точки прилипания.
    glide(track, Math.round((base + dir * perViewOf(track, step) * step) / step) * step);
  }, [glide]);

  // Перетаскивание мышью поверх нативной прокрутки. Тач не трогаем вовсе:
  // палец и так листает ленту с инерцией, а перехват испортил бы разведение
  // осей — вертикальный свайп обязан прокручивать переписку, а не карусель.
  const drag = useRef<{ id: number; x: number; left: number; moved: boolean } | null>(null);
  const suppressClick = useRef(false);

  const onPointerDown = (e: React.PointerEvent<HTMLDivElement>) => {
    if (drag.current) return;                  // посторонняя кнопка мыши посреди жеста — не наше дело
    // Флаг снимаем на входе в жест, а не в onClick: клик может не прийти вовсе,
    // если кнопку отпустили за окном, и тогда он залипнет до конца жизни карусели.
    suppressClick.current = false;
    aim.current.until = 0;                     // человек взял ленту в руки — прежняя цель больше не цель
    if (e.pointerType !== "mouse" || e.button !== 0) return;
    const track = trackRef.current;
    if (!track || track.scrollWidth - track.clientWidth <= EDGE) return;   // листать нечего
    drag.current = { id: e.pointerId, x: e.clientX, left: track.scrollLeft, moved: false };
  };

  // Висит и на pointerup, и на pointercancel, и на потере захвата: внутри
  // прокручиваемой переписки браузер регулярно отбирает жест себе, и pointerup
  // не приходит — без этого курсор навсегда остаётся «сжатой рукой».
  const endDrag = () => {
    const from = drag.current;
    const track = trackRef.current;
    drag.current = null;
    if (!track || !from || !from.moved) return;
    delete track.dataset.dragging;
    if (track.hasPointerCapture?.(from.id)) track.releasePointerCapture(from.id);
    track.style.scrollSnapType = "";
    // Возврат scroll-snap-type доснапливает не во всех движках — доводим явно,
    // иначе карточка остаётся висеть между позициями.
    const step = stepOf(track);
    glide(track, Math.round(track.scrollLeft / step) * step);
  };

  const onPointerMove = (e: React.PointerEvent<HTMLDivElement>) => {
    const from = drag.current;
    const track = trackRef.current;
    if (!from || !track || e.pointerId !== from.id) return;
    // Кнопка уже не нажата: до порога захвата нет, поэтому pointerup, случившийся
    // за пределами ленты, до нас не дошёл. Без этой проверки лента поехала бы за
    // голым курсором при следующем наведении — у мыши pointerId всегда один и тот же.
    if (!(e.buttons & 1)) { endDrag(); return; }
    const dx = e.clientX - from.x;
    if (!from.moved) {
      if (Math.abs(dx) < DRAG_THRESHOLD) return;
      from.moved = true;
      suppressClick.current = true;
      // Захват на currentTarget, а не на target: карточка под курсором может
      // перемонтироваться (доехала ленивая картинка) и молча унести захват с собой.
      track.setPointerCapture(from.id);
      // Прилипание на время жеста выключено: с mandatory браузер доснапливает
      // каждое присваивание scrollLeft, и лента дёргается прямо под курсором.
      track.style.scrollSnapType = "none";
      track.dataset.dragging = "1";
    }
    const want = from.left - dx;
    track.scrollLeft = want;                   // прямо в DOM: перерисовок за жест ноль
    // За упором браузер молча зажимает scrollLeft, а dx продолжает расти — и
    // обратный ход не двигал бы ленту, пока курсор не вернётся за точку нажатия.
    // Переставляем якорь по факту: у края жест просто не копит долг.
    if (Math.abs(track.scrollLeft - want) > 1) {
      from.x = e.clientX;
      from.left = track.scrollLeft;
    }
  };

  // Гасим ровно один клик после перетаскивания — иначе свайп мышью открывал бы
  // лайтбокс и уводил на портал в новой вкладке. Оба вызова обязательны и
  // именно в фазе перехвата: preventDefault не отменяет React-обработчик кнопки
  // зума, а stopPropagation не отменяет переход по ссылке.
  const onClickCapture = (e: React.MouseEvent) => {
    if (!suppressClick.current) return;
    suppressClick.current = false;
    e.preventDefault();
    e.stopPropagation();
  };

  // Клавиши слушает сама лента, а не окно: на window уже сидит перехватчик
  // лайтбокса, и повешенная туда карусель ездила бы под открытым оверлеем.
  const onKeyDown = (e: React.KeyboardEvent<HTMLDivElement>) => {
    const track = trackRef.current;
    if (!track) return;
    // Сочетания оставляем системе: Alt+← и Alt+→ — это «назад» и «вперёд» в
    // истории браузера, и подменять их листанием карточек нельзя.
    if (e.altKey || e.ctrlKey || e.metaKey || e.shiftKey) return;
    const dir = e.key === "ArrowRight" ? 1 : e.key === "ArrowLeft" ? -1 : 0;
    if (!dir && e.key !== "Home" && e.key !== "End") return;
    // preventDefault обязателен и раньше всех проверок: браузер сам прокручивает
    // ленту к следующей точке прилипания, потому что фокус стоит внутри неё.
    // Просто не листать своим кодом недостаточно — уедет всё равно.
    e.preventDefault();
    // Кнопка зума лежит внутри ленты, а лайтбокс фокус к себе не забирает:
    // после клика по фотографии карточки уезжали бы под затемнением, и закрытая
    // крупная фотография не совпадала бы ни с одной видимой карточкой.
    if (document.querySelector('[aria-modal="true"]')) return;
    if (dir) page(dir);
    else glide(track, e.key === "Home" ? 0 : track.scrollWidth);
  };

  return {
    trackRef, nav, page,
    trackProps: {
      onScroll, onKeyDown, onPointerDown, onPointerMove,
      onPointerUp: endDrag, onPointerCancel: endDrag, onLostPointerCapture: endDrag, onClickCapture,
    },
  };
}
