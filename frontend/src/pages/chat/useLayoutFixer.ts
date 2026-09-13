import { useCallback, useState, type RefObject } from "react";
import { gsap } from "gsap";
import type { MascotMood } from "@/lib/types";
import type { LayoutFix } from "./useChatSession";

export interface LayoutFixer {
  /** Настроение маскота-ремонтника, который бежит чинить сообщение. */
  fixerMood: MascotMood;
  /** Маскот ушёл из шапки чинить: на его месте пусто, пока он не вернётся. */
  mascotAway: boolean;
  /** Показать починку и применить исправление (apply) в тот миг, когда текст невидим. */
  play: (fix: LayoutFix, apply: () => void) => void;
}

/**
 * Валерчик выбегает из шапки к сообщению, крутит ключом, чинит раскладку и убегает назад.
 * Позиции берём из DOM в момент запуска: лента могла прокрутиться.
 */
export function useLayoutFixer(
  homeRef: RefObject<HTMLElement | null>, fixerRef: RefObject<HTMLElement | null>,
): LayoutFixer {
  const [fixerMood, setFixerMood] = useState<MascotMood>("idle");
  const [mascotAway, setMascotAway] = useState(false);

  const play = useCallback(({ messageId, text }: LayoutFix, apply: () => void) => {
    const bubble = document.querySelector<HTMLElement>(`[data-msg-id="${messageId}"]`);
    const textEl = bubble?.querySelector<HTMLElement>(".bubble-text");
    const home = homeRef.current;
    const fixer = fixerRef.current;
    const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (!bubble || !textEl || !home || !fixer || reduced) {
      apply();   // без анимации — просто чиним
      return;
    }

    const from = home.getBoundingClientRect();
    const to = bubble.getBoundingClientRect();
    // Встаём слева от пузыря по центру его высоты. Зазор с запасом: у SVG маскота
    // overflow visible, уши и хвост торчат за пределы своей коробки и налезали на текст.
    const targetLeft = Math.max(8, to.left - 76);
    const dx = targetLeft - from.left;
    const dy = to.top - from.top + to.height / 2 - 28;

    setFixerMood("idle");
    setMascotAway(true);

    const tl = gsap.timeline({
      onComplete: () => { setMascotAway(false); setFixerMood("idle"); },
    });
    // Путь буквой «Г»: сначала вдоль шапки вправо, потом вниз к сообщению — и так же
    // обратно. Каждый отрезок со своим плавным разгоном и торможением, поэтому поворот
    // читается как поворот, а не как срез по диагонали.
    tl.set(fixer, { left: from.left, top: from.top, x: 0, y: 0, autoAlpha: 0, scale: 0.5 })
      .to(fixer, { autoAlpha: 1, scale: 1, duration: 0.22, ease: "power2.out" })
      .to(fixer, { x: dx, duration: 0.66, ease: "power1.inOut" })
      .to(fixer, { y: dy, duration: 0.46, ease: "power1.inOut" })
      .call(() => setFixerMood("fixing"))
      // Смена текста одним спокойным движением: старый набор уплывает вверх, размываясь,
      // новый проявляется снизу. Посимвольный перебор букв читался как дребезг.
      .to(textEl, { autoAlpha: 0, filter: "blur(5px)", y: -6, duration: 0.34,
                    ease: "power2.in" }, "+=0.3")
      .call(() => {
        // подменяем, пока текст невидим: DOM — сразу, состояние — следом
        textEl.textContent = text;
        apply();
      })
      .set(textEl, { y: 7, filter: "blur(5px)" })
      .to(textEl, { autoAlpha: 1, filter: "blur(0px)", y: 0, duration: 0.5, ease: "power2.out" })
      .call(() => {
        bubble.classList.add("bubble-repaired");
        setTimeout(() => bubble.classList.remove("bubble-repaired"), 900);
      })
      .call(() => setFixerMood("happy"), undefined, "+=0.2")
      .to(fixer, { y: 0, duration: 0.46, ease: "power1.inOut" }, "+=0.3")
      .to(fixer, { x: 0, duration: 0.66, ease: "power1.inOut" })
      .to(fixer, { autoAlpha: 0, scale: 0.5, duration: 0.2 }, "-=0.18");
  }, [homeRef, fixerRef]);

  return { fixerMood, mascotAway, play };
}
