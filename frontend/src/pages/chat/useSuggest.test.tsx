import { afterEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, renderHook, waitFor } from "@testing-library/react";

/** Подсказки по мере набора: после паузы, одним запросом на слово, без устаревших. */
const get = vi.hoisted(() => vi.fn());
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return { ...actual, api: { ...actual.api, get } };
});

import { useSuggest } from "./useSuggest";

const setup = (input: string) =>
  renderHook(({ text }: { text: string }) => useSuggest(text), { initialProps: { text: input } });

afterEach(() => {
  cleanup();
  get.mockReset();
});

describe("useSuggest", () => {
  it("спрашивает только со второй буквы", async () => {
    get.mockResolvedValue(["Как подать заявку?"]);
    const { result, rerender } = setup("к");
    await new Promise((resolve) => setTimeout(resolve, 300));
    expect(get).not.toHaveBeenCalled();

    rerender({ text: "ка" });
    await waitFor(() => expect(result.current.suggestions).toHaveLength(1));
  });

  it("набор слова — один запрос на паузе, подсказки видны", async () => {
    get.mockResolvedValue(["Как подать заявку?"]);
    const { result, rerender } = setup("ка");
    rerender({ text: "как" });
    rerender({ text: "как п" });

    await waitFor(() => expect(result.current.suggestions).toEqual(["Как подать заявку?"]));
    expect(get).toHaveBeenCalledTimes(1);
    expect(get.mock.calls[0]![0]).toBe(`/api/chat/suggest?q=${encodeURIComponent("как п")}`);
  });

  it("выбранная подсказка убирает список, дальнейший набор возвращает его", async () => {
    get.mockResolvedValue(["Как подать заявку?"]);
    const { result, rerender } = setup("как п");
    await waitFor(() => expect(result.current.suggestions).toHaveLength(1));

    rerender({ text: "Как подать заявку?" });
    act(() => result.current.hideFor("Как подать заявку?"));
    expect(result.current.suggestions).toEqual([]);

    rerender({ text: "Как подать заявку? И" });
    await waitFor(() => expect(result.current.suggestions).toHaveLength(1));
  });

  it("сбой подсказок — список пустеет, а не показывает прежние", async () => {
    get.mockResolvedValueOnce(["Как подать заявку?"]);
    get.mockRejectedValueOnce(new Error("Сервис недоступен"));
    const { result, rerender } = setup("как п");
    await waitFor(() => expect(result.current.suggestions).toHaveLength(1));

    rerender({ text: "как по" });
    await waitFor(() => expect(get).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(result.current.suggestions).toEqual([]));
  });
});
