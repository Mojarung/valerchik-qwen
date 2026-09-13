import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

/**
 * Демо-доступы — подсказка для стенда. В боевом режиме пароли случайные: такая
 * подсказка вела в тупик и заодно раскрывала логины сотрудников.
 */
const meta = vi.hoisted(() => ({ demo: false }));

vi.mock("@/lib/store", () => ({
  useAuth: (select: (s: { setUser: () => void }) => unknown) => select({ setUser: () => {} }),
  useMeta: (select: (s: { demo: boolean }) => unknown) => select(meta),
}));
vi.mock("@/lib/api", () => ({ api: { post: vi.fn() } }));
// Маскот к проверке не относится, а его анимации тянут API браузера, которых нет в jsdom
vi.mock("@/components/Mascot", () => ({ Mascot: () => null }));

import LoginPage from "./LoginPage";

function renderPage() {
  render(
    <MemoryRouter>
      <LoginPage />
    </MemoryRouter>,
  );
}

afterEach(cleanup);

describe("LoginPage", () => {
  it("в боевом режиме не подсказывает логины и пароли", () => {
    meta.demo = false;
    renderPage();

    expect(screen.queryByText(/Демо-доступы/)).toBeNull();
    expect(screen.getByLabelText("Логин").getAttribute("placeholder")).toBeNull();
  });

  it("на демо-стенде показывает демо-доступы", () => {
    meta.demo = true;
    renderPage();

    expect(screen.getByText(/Демо-доступы/)).toBeTruthy();
  });
});
