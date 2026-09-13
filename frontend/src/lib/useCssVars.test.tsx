import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { act, cleanup, render, screen } from "@testing-library/react";
import { useCssVars } from "./useCssVars";

beforeEach(() => {
  // В jsdom нет matchMedia, а хук подписывается и на смену системной темы
  window.matchMedia = ((query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addEventListener: () => {},
    removeEventListener: () => {},
    addListener: () => {},
    removeListener: () => {},
    dispatchEvent: () => false,
  })) as unknown as typeof window.matchMedia;
});

/**
 * Графики красятся атрибутами SVG и тему через CSS не видят: цвет серии обязан
 * приходить из переменной темы и обновляться при её смене, иначе на тёмном
 * фоне остаётся светлый красный и подписи становятся нечитаемыми.
 */
const VARS = ["--color-brand-ink"] as const;

function Probe() {
  const colors = useCssVars(VARS);
  return <span>{colors["--color-brand-ink"]}</span>;
}

afterEach(() => {
  cleanup();
  document.documentElement.removeAttribute("data-theme");
  document.documentElement.style.removeProperty("--color-brand-ink");
});

describe("useCssVars", () => {
  it("читает текущее значение переменной темы", () => {
    document.documentElement.style.setProperty("--color-brand-ink", "rgb(1, 2, 3)");
    render(<Probe />);
    expect(screen.getByText("rgb(1, 2, 3)")).toBeTruthy();
  });

  it("перечитывает значение, когда меняется тема", async () => {
    document.documentElement.style.setProperty("--color-brand-ink", "rgb(1, 2, 3)");
    render(<Probe />);

    await act(async () => {
      document.documentElement.style.setProperty("--color-brand-ink", "rgb(4, 5, 6)");
      document.documentElement.dataset.theme = "dark";
      await Promise.resolve();
    });

    expect(await screen.findByText("rgb(4, 5, 6)")).toBeTruthy();
  });
});
