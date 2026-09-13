import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, renderHook, waitFor } from "@testing-library/react";

/**
 * Выборка статей базы знаний: фильтры в адресе с паузой, страницы по 20,
 * отметки в пределах выборки и режим «всё по фильтру» для массовых действий.
 */
const get = vi.hoisted(() => vi.fn());
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return { ...actual, api: { ...actual.api, get } };
});

import { useKbListing } from "./useKbListing";

const LISTING = { items: [{ id: "a1" }, { id: "a2" }], total: 57, page: 1 };
const lastParams = () => new URL(String(get.mock.calls.at(-1)?.[0]), "http://стенд").searchParams;

async function loaded() {
  const hook = renderHook(() => useKbListing());
  await waitFor(() => expect(hook.result.current.listing).not.toBeNull());
  return hook;
}

beforeEach(() => {
  get.mockResolvedValue(LISTING);
});
afterEach(() => {
  cleanup();
  get.mockReset();
});

describe("useKbListing", () => {
  it("фильтр уходит в адрес и возвращает на первую страницу", async () => {
    const { result } = await loaded();
    act(() => result.current.setPage(3));
    act(() => result.current.setQuery("подпись"));

    expect(result.current.page).toBe(1);
    await waitFor(() => expect(lastParams().get("query")).toBe("подпись"));
    expect(lastParams().get("page")).toBe("1");
  });

  it("страниц — по двадцать статей", async () => {
    const { result } = await loaded();
    expect(result.current.pages).toBe(3);
  });

  it("отметки живут в пределах выборки: смена фильтра их снимает", async () => {
    const { result } = await loaded();
    act(() => result.current.toggleOne("a1"));
    expect([...result.current.selected]).toEqual(["a1"]);

    act(() => result.current.setStatus("draft"));
    expect(result.current.selected.size).toBe(0);
  });

  it("страница целиком: частичная отметка видна, повторное нажатие снимает всё", async () => {
    const { result } = await loaded();
    act(() => result.current.toggleOne("a1"));
    expect(result.current).toMatchObject({ allSelected: false, partlySelected: true });

    act(() => result.current.togglePage());
    expect(result.current.allSelected).toBe(true);
    act(() => result.current.togglePage());
    expect(result.current.selected.size).toBe(0);
  });

  it("всё по фильтру: счёт по выборке, цель — фильтр; снятая галочка возвращает к id", async () => {
    const { result } = await loaded();
    act(() => result.current.togglePage());
    act(() => result.current.toggleAllMatching());
    expect(result.current.selectionCount).toBe(57);
    expect(result.current.bulkTarget()).toEqual({ filter: { query: "", status: "", audience: "" } });

    act(() => result.current.toggleOne("a1"));
    expect(result.current.allMatching).toBe(false);
    expect(result.current.bulkTarget()).toEqual({ ids: ["a2"] });
  });
});
