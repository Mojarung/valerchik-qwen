import { afterEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, renderHook } from "@testing-library/react";
import { gsap } from "gsap";
import { useLayoutFixer } from "./useLayoutFixer";

/** «Ремонтник»: без анимации исправление применяется сразу, с ней маскот уходит из шапки чинить. */
const FIX = { messageId: "m1", text: "Как подать заявку?", original: "Rfr gjlfnm pfzdre?", kinds: ["layout"] };

function motion(reduced: boolean) {
  window.matchMedia = ((query: string) => ({ matches: reduced, media: query })) as unknown as typeof window.matchMedia;
}

function scene() {
  document.body.innerHTML = `
    <div id="home"></div><div id="fixer"></div>
    <div data-msg-id="m1"><span class="bubble-text">Rfr gjlfnm pfzdre?</span></div>`;
  return {
    home: { current: document.getElementById("home") },
    fixer: { current: document.getElementById("fixer") },
  };
}

afterEach(() => {
  cleanup();
  gsap.globalTimeline.clear();
  document.body.innerHTML = "";
});

describe("useLayoutFixer", () => {
  it("человек просит меньше движения — исправление применяется сразу, без пробежки", () => {
    motion(true);
    const { home, fixer } = scene();
    const apply = vi.fn();
    const { result } = renderHook(() => useLayoutFixer(home, fixer));

    act(() => result.current.play(FIX, apply));

    expect(apply).toHaveBeenCalledTimes(1);
    expect(result.current.mascotAway).toBe(false);
  });

  it("пузыря уже нет в ленте — исправление применяется сразу", () => {
    motion(false);
    const { home, fixer } = scene();
    const apply = vi.fn();
    const { result } = renderHook(() => useLayoutFixer(home, fixer));

    act(() => result.current.play({ ...FIX, messageId: "nope" }, apply));

    expect(apply).toHaveBeenCalledTimes(1);
  });

  it("с анимацией маскот уходит из шапки, а текст меняется не сразу", () => {
    motion(false);
    const { home, fixer } = scene();
    const apply = vi.fn();
    const { result } = renderHook(() => useLayoutFixer(home, fixer));

    act(() => result.current.play(FIX, apply));

    expect(result.current.mascotAway).toBe(true);
    expect(apply).not.toHaveBeenCalled();
  });
});
