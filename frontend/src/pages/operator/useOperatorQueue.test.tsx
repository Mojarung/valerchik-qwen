import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, renderHook } from "@testing-library/react";
import type { OperatorSession } from "@/lib/types";

/**
 * Очередь оператора: фильтр, перезапрос пачкой по событиям сокета и часы
 * ожидания, которые идут на глазах и не «молодеют» после свежей загрузки.
 */
const get = vi.hoisted(() => vi.fn());
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return { ...actual, api: { ...actual.api, get } };
});

import { useOperatorQueue } from "./useOperatorQueue";

const ticket = { session_id: "a".repeat(32), status: "waiting_operator", waiting_seconds: 30 } as OperatorSession;

/** Прогнать таймеры и дать ответам сервера дойти до состояния. */
const flush = (ms = 0) => act(async () => { await vi.advanceTimersByTimeAsync(ms); });

beforeEach(() => {
  vi.useFakeTimers();
  get.mockResolvedValue([ticket]);
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
  get.mockReset();
});

describe("useOperatorQueue", () => {
  it("фильтр меняет адрес очереди", async () => {
    const { result } = renderHook(() => useOperatorQueue());
    await flush();
    expect(get.mock.calls[0]![0]).toBe("/api/operator/sessions?status=queue");
    expect(result.current.sessions).toEqual([ticket]);

    act(() => result.current.setFilter("all"));
    await flush();
    expect(result.current.filter).toBe("all");
    expect(get.mock.calls[1]![0]).toBe("/api/operator/sessions?status=all");
  });

  it("пачка событий сокета — один перезапрос через паузу", async () => {
    const { result } = renderHook(() => useOperatorQueue());
    await flush();

    for (let i = 0; i < 5; i++) act(() => result.current.scheduleReload());
    await flush(299);
    expect(get).toHaveBeenCalledTimes(1);
    await flush(1);
    expect(get).toHaveBeenCalledTimes(2);
  });

  it("уход со страницы отменяет отложенный перезапрос", async () => {
    const { result, unmount } = renderHook(() => useOperatorQueue());
    await flush();
    act(() => result.current.scheduleReload());
    unmount();
    await flush(1000);
    expect(get).toHaveBeenCalledTimes(1);
  });

  it("ожидание растёт с часами и не «молодеет» после свежей загрузки", async () => {
    const { result } = renderHook(() => useOperatorQueue());
    await flush();
    await flush(60_000);
    expect(result.current.waitingOf(ticket)).toBe(30); // Polling refreshed the server's value.
    expect(result.current.waitingOf({ ...ticket, waiting_seconds: null })).toBeNull();

    // Часы тикают раз в 15 с: на 70-й секунде они ещё показывают 60-ю, а список
    // уже свежий — прошедшее время не может стать отрицательным.
    await flush(10_000);
    act(() => result.current.reload());
    await flush();
    expect(get.mock.calls.length).toBeGreaterThanOrEqual(2);
    expect(result.current.waitingOf(ticket)).toBe(30);
  });
});
