import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

/** Импорт статей из JSON: понятная ошибка вместо английского текста движка, отказ сервера — в окне. */
const post = vi.hoisted(() => vi.fn());
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return { ...actual, api: { ...actual.api, post } };
});

import { UPLOAD_TIMEOUT_MS } from "@/lib/api";
import { ImportJsonModal } from "./ImportJsonModal";

function setup() {
  const onImported = vi.fn();
  render(<ImportJsonModal onImported={onImported} onClose={vi.fn()} />);
  const field = screen.getByRole("textbox") as HTMLTextAreaElement;
  const paste = (text: string) => fireEvent.change(field, { target: { value: text } });
  return { onImported, field, paste };
}
const importButton = () => screen.getByRole("button", { name: "Импортировать" });

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("ImportJsonModal", () => {
  it("не JSON — понятная ошибка по-русски, текст на месте, на сервер ничего не ушло", async () => {
    const { field, paste } = setup();
    paste("[{\"question\": ");
    await userEvent.click(importButton());

    expect((await screen.findByRole("alert")).textContent).toMatch(/Это не JSON/);
    expect(field.value).toBe("[{\"question\": ");
    expect(post).not.toHaveBeenCalled();
  });

  it("статьи уходят на сервер с долгим таймаутом, наружу — сколько импортировано", async () => {
    post.mockResolvedValue({ imported: 2 });
    const { onImported, paste } = setup();
    const items = [
      { question: "Как подписать?", answer: "Откройте карточку." },
      { question: "Где заявки?", answer: "В разделе «Заявки»." },
    ];
    paste(JSON.stringify(items));
    await userEvent.click(importButton());

    expect(post).toHaveBeenCalledWith("/api/kb/import", { items }, { timeoutMs: UPLOAD_TIMEOUT_MS });
    await waitFor(() => expect(onImported).toHaveBeenCalledWith(2));
  });

  it("сервер отказал — причина в окне", async () => {
    post.mockRejectedValue(new Error("items.0.answer: Field required"));
    const { paste } = setup();
    paste("[{\"question\": \"Как подписать?\"}]");
    await userEvent.click(importButton());

    expect((await screen.findByRole("alert")).textContent).toContain("items.0.answer: Field required");
  });
});
