import { afterEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, renderHook, waitFor } from "@testing-library/react";

/**
 * Загрузка данных страницы: ошибка видна и повторяема, а смена адреса
 * (фильтр, страница таблицы) отменяет прежний запрос — опоздавший ответ не
 * перезаписывает уже показанный новый.
 */
const get = vi.hoisted(() => vi.fn());
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return { ...actual, api: { ...actual.api, get } };
});

import { useApiData } from "./useApiData";

afterEach(() => {
  cleanup();
  get.mockReset();
});

describe("useApiData", () => {
  it("при сбое отдаёт ошибку, а повтор загружает заново", async () => {
    get.mockRejectedValueOnce(new Error("Сервер не ответил за 30 с"));
    get.mockResolvedValueOnce({ items: [1] });
    const { result } = renderHook(() => useApiData<{ items: number[] }>("/api/x"));

    await waitFor(() => expect(result.current.error).toBe("Сервер не ответил за 30 с"));
    expect(result.current.data).toBeNull();

    act(() => result.current.reload());

    await waitFor(() => expect(result.current.data).toEqual({ items: [1] }));
    expect(result.current.error).toBeNull();
  });

  it("смена адреса отменяет прежний запрос, опоздавший ответ не перезаписывает новый", async () => {
    let lateResolve: (value: unknown) => void = () => {};
    const signals: AbortSignal[] = [];
    get.mockImplementation((path: string, opts?: { signal?: AbortSignal }) => {
      if (opts?.signal) signals.push(opts.signal);
      if (path === "/api/list?page=1") return new Promise((resolve) => { lateResolve = resolve; });
      return Promise.resolve({ page: 2 });
    });
    const { result, rerender } = renderHook(
      ({ path }: { path: string }) => useApiData<{ page: number }>(path),
      { initialProps: { path: "/api/list?page=1" } },
    );

    rerender({ path: "/api/list?page=2" });

    await waitFor(() => expect(result.current.data).toEqual({ page: 2 }));
    expect(signals[0]?.aborted).toBe(true);
    await act(async () => lateResolve({ page: 1 }));
    expect(result.current.data).toEqual({ page: 2 });
  });

  it("опрашивает, пока держится условие, и замолкает, когда оно ушло", async () => {
    get.mockResolvedValueOnce({ running: true });
    get.mockResolvedValueOnce({ running: false });
    get.mockResolvedValue({ running: false });
    const { result } = renderHook(() =>
      useApiData<{ running: boolean }>("/api/eval", { pollMs: 20, pollWhile: (d) => Boolean(d?.running) }));

    await waitFor(() => expect(result.current.data).toEqual({ running: false }));
    await new Promise((resolve) => setTimeout(resolve, 100));

    expect(get).toHaveBeenCalledTimes(2);
  });

  it("момент загрузки приходит вместе с данными, без кадра со старым временем", async () => {
    get.mockResolvedValueOnce({ n: 1 });
    const before = Date.now();
    const renders: Array<[unknown, number | null]> = [];
    const { result } = renderHook(() => {
      const state = useApiData<{ n: number }>("/api/queue");
      renders.push([state.data, state.loadedAt]);
      return state;
    });

    await waitFor(() => expect(result.current.data).toEqual({ n: 1 }));

    expect(result.current.loadedAt).toBeGreaterThanOrEqual(before);
    expect(renders.filter(([data, at]) => data !== null && at === null)).toEqual([]);
  });

  it("без адреса не висит в загрузке, даже если прежний запрос оборван на полпути", async () => {
    get.mockImplementation(() => new Promise(() => undefined));
    const { result, rerender } = renderHook(
      ({ path }: { path: string | null }) => useApiData<unknown>(path),
      { initialProps: { path: "/api/search?q=ab" as string | null } },
    );
    await waitFor(() => expect(get).toHaveBeenCalledTimes(1));

    rerender({ path: null });

    expect(result.current.loading).toBe(false);
  });
});
