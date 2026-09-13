import { useEffect, type RefObject } from "react";

const MAX_X = 2.6;          // предельный ход зрачка по X в единицах viewBox (белок 9.6 - зрачок 6.9)
const MAX_Y = 1.9;          // по вертикали запас меньше: зрачок и так смещён вниз
const TAU_MS = 55;          // постоянная времени догона курсора, мс (меньше — резче)
const IDLE_RETURN_MS = 2600; // через сколько без движения мыши взгляд возвращается в центр
const EPS = 0.01;           // ближе этого к цели считаем, что взгляд догнал

/**
 * Зрачки следят за курсором, пока маскот ничего не делает (idle/happy).
 * В остальных состояниях inline-трансформ снимается — работает CSS-анимация состояния.
 *
 * Цикл rAF живёт только пока взгляд реально движется: догнал курсор — засыпает
 * до следующего pointermove. Учитывает prefers-reduced-motion и тач (курсора нет).
 */
export function useEyeTracking(
  containerRef: RefObject<HTMLElement | null>,
  pupilsRef: RefObject<SVGGElement | null>,
  enabled: boolean,
): void {
  useEffect(() => {
    const pupils = pupilsRef.current;
    if (!pupils) return;

    const reset = () => {
      pupils.style.transform = "";   // отдаём управление CSS-состоянию
      pupils.style.willChange = "";
    };
    if (!enabled) {
      reset();
      return;
    }
    const noMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    const noHover = window.matchMedia("(hover: none)").matches;
    if (noMotion || noHover) {
      reset();
      return;
    }

    let targetX = 0, targetY = 0;
    let currentX = 0, currentY = 0;
    let pointerX = 0, pointerY = 0, tracking = false;
    let lastFrame = 0;
    let frame = 0;                    // 0 = цикл спит (id от rAF всегда > 0)
    let idleTimer: number | undefined;

    // Геометрию маскота кэшируем: getBoundingClientRect на каждый pointermove —
    // это принудительный layout по нескольку раз за кадр, главный источник рывков.
    let box: DOMRect | null = null;
    const invalidateBox = () => { box = null; };

    const aim = () => {
      if (!box) box = containerRef.current?.getBoundingClientRect() ?? null;
      if (!box || !box.width) return;
      // вектор от центра маскота к курсору, нормированный на половину экрана
      const dx = (pointerX - (box.left + box.width / 2)) / (window.innerWidth / 2);
      const dy = (pointerY - (box.top + box.height / 2)) / (window.innerHeight / 2);
      const len = Math.hypot(dx, dy) || 1;
      const clamped = Math.min(len, 1) / len;   // не даём вылезти за пределы белка
      targetX = dx * clamped * MAX_X;
      targetY = dy * clamped * MAX_Y;
    };

    const draw = () => {
      pupils.style.transform = `translate(${currentX.toFixed(2)}px, ${currentY.toFixed(2)}px)`;
    };

    const tick = (now: number) => {
      const dt = Math.min(now - lastFrame, 50);  // после фонового таба не прыгаем рывком
      lastFrame = now;
      // сглаживание через экспоненту, а не фиксированный шаг: на 60, 120 и 144 Гц
      // взгляд догоняет курсор за одно и то же время
      const k = 1 - Math.exp(-dt / TAU_MS);
      currentX += (targetX - currentX) * k;
      currentY += (targetY - currentY) * k;

      if (Math.abs(targetX - currentX) < EPS && Math.abs(targetY - currentY) < EPS) {
        currentX = targetX;
        currentY = targetY;
        draw();
        pupils.style.willChange = "";
        frame = 0;                     // догнали — гасим цикл, кадры больше не жжём
        return;
      }
      draw();
      frame = requestAnimationFrame(tick);
    };

    const wake = () => {
      if (frame) return;
      lastFrame = performance.now();
      pupils.style.willChange = "transform";
      frame = requestAnimationFrame(tick);
    };

    const onMove = (e: PointerEvent) => {
      pointerX = e.clientX;
      pointerY = e.clientY;
      tracking = true;
      aim();
      window.clearTimeout(idleTimer);
      idleTimer = window.setTimeout(() => { targetX = 0; targetY = 0; wake(); }, IDLE_RETURN_MS);
      wake();
    };

    const onLayoutChange = () => {
      invalidateBox();
      if (tracking) { aim(); wake(); }
    };

    window.addEventListener("pointermove", onMove, { passive: true });
    window.addEventListener("resize", onLayoutChange, { passive: true });
    window.addEventListener("scroll", onLayoutChange, { passive: true, capture: true });

    return () => {
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("resize", onLayoutChange);
      window.removeEventListener("scroll", onLayoutChange, { capture: true });
      window.clearTimeout(idleTimer);
      if (frame) cancelAnimationFrame(frame);
      reset();
    };
  }, [containerRef, pupilsRef, enabled]);
}
