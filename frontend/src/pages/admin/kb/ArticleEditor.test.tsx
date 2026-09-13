import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

/**
 * Редактор статьи: отказ сохранения и загрузки картинки виден в самом окне —
 * тост лежал под подложкой модалки, — а правка остаётся на месте.
 */
const upload = vi.hoisted(() => vi.fn());
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return { ...actual, api: { ...actual.api, upload } };
});

import { ArticleEditor } from "./ArticleEditor";

const ARTICLE = {
  id: "a1", question: "Как подписать контракт?", answer: "Откройте карточку.", category: "Контракты",
  keywords: ["подпись"], line: 1, status: "active", audience: "all" as const, images: [],
};

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("ArticleEditor", () => {
  it("во время сохранения не отправляет повторный запрос и не теряет редактор", async () => {
    let finish!: () => void;
    const onSave = vi.fn(() => new Promise<void>((resolve) => { finish = resolve; }));
    const onClose = vi.fn();
    render(<ArticleEditor initial={ARTICLE} onSave={onSave} onClose={onClose} />);
    const save = screen.getByRole("button", { name: /Сохранить/ });
    fireEvent.click(save);
    fireEvent.click(save);
    expect(onSave).toHaveBeenCalledTimes(1);
    expect(save.matches(":disabled")).toBe(true);
    expect(screen.getByLabelText("Ответ").matches(":disabled")).toBe(true);
    fireEvent.keyDown(document, { key: "Escape" });
    expect(onClose).not.toHaveBeenCalled();
    finish();
    await waitFor(() => expect(save.matches(":disabled")).toBe(false));
  });

  it("сохранение не прошло — ошибка в окне, правка на месте", async () => {
    const onSave = vi.fn(async () => { throw new Error("Статья с таким вопросом уже есть"); });
    render(<ArticleEditor initial={ARTICLE} onSave={onSave} onClose={vi.fn()} />);
    const dialog = screen.getByRole("dialog");
    const answer = within(dialog).getByLabelText("Ответ") as HTMLTextAreaElement;
    await userEvent.type(answer, " Нажмите «Подписать».");
    await userEvent.click(within(dialog).getByRole("button", { name: /Сохранить/ }));

    expect((await within(dialog).findByRole("alert")).textContent).toContain("Статья с таким вопросом уже есть");
    expect(answer.value).toBe("Откройте карточку. Нажмите «Подписать».");
  });

  it("сохраняет статью целиком: с ролью и списком картинок, даже пустым", async () => {
    const onSave = vi.fn(async () => undefined);
    render(<ArticleEditor initial={{ question: "Как подписать контракт?", answer: "Откройте карточку." }}
      onSave={onSave} onClose={vi.fn()} />);
    await userEvent.click(screen.getByRole("button", { name: /Сохранить/ }));

    expect(onSave).toHaveBeenCalledWith(expect.objectContaining({ audience: "all", images: [] }));
  });

  it("картинка не загрузилась — ошибка в окне", async () => {
    upload.mockRejectedValue(new Error("Картинка больше 10 МБ"));
    render(<ArticleEditor initial={ARTICLE} onSave={vi.fn()} onClose={vi.fn()} />);
    const input = document.querySelector<HTMLInputElement>("input[type=file]");
    expect(input, "в редакторе нет выбора картинки").not.toBeNull();
    fireEvent.change(input!, { target: { files: [new File(["x"], "screen.png", { type: "image/png" })] } });

    expect((await screen.findByRole("alert")).textContent).toContain("Картинка больше 10 МБ");
  });
});
