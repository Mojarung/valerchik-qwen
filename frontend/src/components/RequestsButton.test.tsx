import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { act, cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useRequestsSheet } from "@/lib/requestsSheet";
import { PanelToggle, RequestsButton } from "./RequestsButton";

// Панель по умолчанию свёрнута — с этого состояния начинает и каждый тест.
beforeEach(() => {
  useRequestsSheet.setState({ open: false, openCount: 0, unread: false, panelCollapsed: true });
});

afterEach(() => {
  cleanup();
  localStorage.clear();
});

describe("PanelToggle", () => {
  it("одна кнопка на одном месте прячет и возвращает панель, без счётчика и точки нового ответа", async () => {
    render(<PanelToggle />);
    act(() => useRequestsSheet.getState().setSummary(2, true));
    const show = screen.getByRole("button", { name: "Показать мои обращения" });
    expect(show.getAttribute("aria-expanded")).toBe("false");
    expect(show.textContent).not.toContain("2");
    expect(show.querySelector("span")).toBeNull();

    await userEvent.click(show);
    expect(useRequestsSheet.getState().panelCollapsed).toBe(false);

    const hide = screen.getByRole("button", { name: "Скрыть мои обращения" });
    expect(hide.getAttribute("aria-expanded")).toBe("true");
    await userEvent.click(hide);
    expect(useRequestsSheet.getState().panelCollapsed).toBe(true);
  });
});

describe("RequestsButton", () => {
  it("называет число открытых, сообщает о новом ответе и открывает список", async () => {
    render(<RequestsButton />);
    expect(screen.getByRole("button", { name: "Мои обращения" })).toBeTruthy();

    act(() => useRequestsSheet.getState().setSummary(2, false));
    expect(screen.getByRole("button", { name: "Мои обращения, открытых: 2" })).toBeTruthy();

    act(() => useRequestsSheet.getState().setSummary(0, true));
    await userEvent.click(screen.getByRole("button", { name: "Мои обращения, есть новый ответ" }));
    expect(useRequestsSheet.getState().open).toBe(true);
  });
});
