import { describe, expect, it } from "vitest";
import { EDGE, NAV_IDLE, perViewOf, readNav, sameNav, stepOf } from "./geometry";

/**
 * Геометрия ленты карусели: шаг сетки, сколько карточек в кадре и что
 * показать в шапке — стрелки и счётчик «1–2 из 8».
 */
interface FakeCard {
  offsetLeft: number;
  offsetWidth: number;
  nextElementSibling: FakeCard | null;
}

const STEP = 168;   // карточка 160 плюс зазор 8
const WIDTH = 160;
const VIEW = 340;   // в кадре две карточки целиком

function track({ cards = 8, clientWidth = VIEW, scrollLeft = 0 } = {}): HTMLElement {
  const items: FakeCard[] = Array.from({ length: cards }, (_, i) => (
    { offsetLeft: i * STEP, offsetWidth: WIDTH, nextElementSibling: null }));
  items.forEach((card, i) => { card.nextElementSibling = items[i + 1] ?? null; });
  return {
    firstElementChild: items[0] ?? null,
    clientWidth,
    scrollLeft,
    scrollWidth: cards ? (cards - 1) * STEP + WIDTH : 0,
  } as unknown as HTMLElement;
}

describe("геометрия карусели", () => {
  it("шаг — расстояние между соседними карточками, а не ширина одной", () => {
    expect(stepOf(track())).toBe(STEP);
    expect(stepOf(track({ cards: 1 }))).toBe(WIDTH);
    expect(stepOf(track({ cards: 0 }))).toBe(1);
  });

  it("в кадре столько карточек, сколько влезает целиком, но хотя бы одна", () => {
    expect(perViewOf(track(), STEP)).toBe(2);
    expect(perViewOf(track({ clientWidth: 100 }), STEP)).toBe(1);
  });

  it("в начале ленты назад нельзя, в кадре первые карточки", () => {
    expect(readNav(track(), 8)).toEqual({ overflow: true, atStart: true, atEnd: false, from: 1, to: 2 });
  });

  it("дробный scrollLeft у края считается краем", () => {
    expect(readNav(track({ scrollLeft: EDGE - 0.5 }), 8)).toMatchObject({ overflow: true, atStart: true });
  });

  it("посреди ленты счётчик идёт по сетке", () => {
    expect(readNav(track({ scrollLeft: 2 * STEP }), 8)).toMatchObject({ atStart: false, from: 3, to: 4 });
  });

  it("у правого упора счёт от конца: видны последние карточки", () => {
    const max = 7 * STEP + WIDTH - VIEW;
    expect(readNav(track({ scrollLeft: max }), 8)).toEqual({ overflow: true, atStart: false, atEnd: true, from: 7, to: 8 });
  });

  it("всё влезло — это не карусель: ни стрелок, ни счётчика", () => {
    expect(readNav(track({ cards: 2 }), 2)).toEqual({ overflow: false, atStart: true, atEnd: true, from: 1, to: 2 });
  });

  it("одинаковую шапку не перерисовываем", () => {
    const nav = readNav(track(), 8);
    expect(sameNav(nav, { ...nav })).toBe(true);
    expect(sameNav(nav, NAV_IDLE)).toBe(false);
  });
});
