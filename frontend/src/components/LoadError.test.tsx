import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { LoadError } from "./LoadError";

afterEach(cleanup);

describe("LoadError", () => {
  it("объявляет ошибку и повторяет загрузку по кнопке", () => {
    const retry = vi.fn();
    render(<LoadError message="Сервер не ответил" onRetry={retry} />);

    expect(screen.getByRole("alert").textContent).toContain("Сервер не ответил");
    fireEvent.click(screen.getByRole("button", { name: /Повторить/ }));

    expect(retry).toHaveBeenCalledOnce();
  });
});
