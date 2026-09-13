import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { useCarousel } from "./useCarousel";

/**
 * Листание ленты: клавиши, края и память заказанной позиции. Геометрию jsdom
 * не считает — задаём её сами: восемь карточек по 160 с зазором 8, в кадре
 * 340 пикселей, то есть две карточки целиком.
 */
const STEP = 168;
const WIDTH = 160;
const VIEW = 340;
const scrollLefts = new WeakMap<Element, number>();
const scrollTo = vi.fn();

const isTrack = (el: HTMLElement) => el.dataset.track === "1";
const isCard = (el: HTMLElement) => el.parentElement?.dataset.track === "1";

function getter(name: string, get: (this: HTMLElement) => number) {
  Object.defineProperty(HTMLElement.prototype, name, { configurable: true, get });
}

beforeAll(() => {
  window.matchMedia = ((query: string) => ({ matches: true, media: query })) as unknown as typeof window.matchMedia;
  globalThis.ResizeObserver = class {
    observe() { return undefined; }
    unobserve() { return undefined; }
    disconnect() { return undefined; }
  } as unknown as typeof ResizeObserver;
  getter("clientWidth", function () { return isTrack(this) ? VIEW : 0; });
  getter("scrollWidth", function () { return isTrack(this) ? (this.children.length - 1) * STEP + WIDTH : 0; });
  getter("offsetLeft", function () {
    return isCard(this) ? [...this.parentElement!.children].indexOf(this) * STEP : 0;
  });
  getter("offsetWidth", function () { return isCard(this) ? WIDTH : 0; });
  Object.defineProperty(HTMLElement.prototype, "scrollLeft", {
    configurable: true,
    get(this: HTMLElement) { return scrollLefts.get(this) ?? 0; },
    set(this: HTMLElement, value: number) { scrollLefts.set(this, value); },
  });
  HTMLElement.prototype.scrollTo = scrollTo as unknown as HTMLElement["scrollTo"];
});

function Harness({ total = 8 }: { total?: number }) {
  const { trackRef, nav, trackProps } = useCarousel(total);
  return (
    <>
      <pre data-testid="nav">{JSON.stringify(nav)}</pre>
      <div ref={trackRef} data-track="1" data-testid="track" tabIndex={0} {...trackProps}>
        {Array.from({ length: total }, (_, i) => <div key={i}>карточка {i + 1}</div>)}
      </div>
    </>
  );
}

const track = () => screen.getByTestId("track");
const nav = () => JSON.parse(screen.getByTestId("nav").textContent ?? "{}");
const lastLeft = () => (scrollTo.mock.calls.at(-1)?.[0] as ScrollToOptions | undefined)?.left;

beforeEach(() => scrollTo.mockClear());
afterEach(() => {
  cleanup();
  document.body.innerHTML = "";
});

describe("useCarousel", () => {
  it("шапка знает, что листать есть что: первые две из восьми, назад нельзя", () => {
    render(<Harness />);
    expect(nav()).toEqual({ overflow: true, atStart: true, atEnd: false, from: 1, to: 2 });
  });

  it("стрелка вправо листает на кадр по сетке карточек", () => {
    render(<Harness />);
    fireEvent.keyDown(track(), { key: "ArrowRight" });
    expect(scrollTo).toHaveBeenCalledWith({ left: 2 * STEP, behavior: "auto" });
  });

  it("второе нажатие считается от заказанной позиции, а не от середины прокрутки", () => {
    render(<Harness />);
    fireEvent.keyDown(track(), { key: "ArrowRight" });
    fireEvent.keyDown(track(), { key: "ArrowRight" });
    expect(lastLeft()).toBe(4 * STEP);
  });

  it("Home и End — к краям ленты", () => {
    render(<Harness />);
    fireEvent.keyDown(track(), { key: "End" });
    expect(lastLeft()).toBe(7 * STEP + WIDTH - VIEW);
    fireEvent.keyDown(track(), { key: "Home" });
    expect(lastLeft()).toBe(0);
  });

  it("сочетания с Alt и Ctrl оставлены браузеру", () => {
    render(<Harness />);
    expect(fireEvent.keyDown(track(), { key: "ArrowRight", altKey: true })).toBe(true);
    expect(fireEvent.keyDown(track(), { key: "ArrowLeft", ctrlKey: true })).toBe(true);
    expect(scrollTo).not.toHaveBeenCalled();

    fireEvent.keyDown(track(), { key: "ArrowRight" });
    expect(scrollTo).toHaveBeenCalledTimes(1);
  });

  it("под открытым лайтбоксом лента стоит, но и браузер её не прокручивает", () => {
    render(<Harness />);
    const overlay = document.createElement("div");
    overlay.setAttribute("aria-modal", "true");
    document.body.append(overlay);
    expect(fireEvent.keyDown(track(), { key: "ArrowRight" })).toBe(false);
    expect(scrollTo).not.toHaveBeenCalled();

    overlay.remove();
    fireEvent.keyDown(track(), { key: "ArrowRight" });
    expect(scrollTo).toHaveBeenCalledTimes(1);
  });
});
