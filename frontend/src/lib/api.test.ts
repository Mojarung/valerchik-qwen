import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, api, isAbort } from "./api";

/**
 * У запроса есть предел ожидания и отмена. Без предела зависший сервер держал
 * страницу в «загрузке» бесконечно, без отмены ответ на прежний фильтр
 * перезаписывал уже показанный новый, а обрыв сети доходил до человека
 * английским «Failed to fetch».
 */
function hangingFetch() {
  return vi.fn(
    (_url: string, init?: RequestInit) =>
      new Promise<Response>((_resolve, reject) => {
        init?.signal?.addEventListener("abort", () =>
          reject(init.signal?.reason ?? new DOMException("aborted", "AbortError")));
      }),
  );
}

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("api", () => {
  it("обрывает запрос по таймауту и объясняет это по-русски", async () => {
    vi.useFakeTimers();
    vi.stubGlobal("fetch", hangingFetch());
    const caught = api.get("/api/slow", { timeoutMs: 1000 }).catch((e: unknown) => e);

    await vi.advanceTimersByTimeAsync(1000);
    const error = await caught;

    expect(error).toBeInstanceOf(ApiError);
    expect((error as ApiError).status).toBe(0);
    expect((error as ApiError).message).toMatch(/не ответил/);
  }, 3000);

  it("отмену вызывающим кодом отличает от сбоя", async () => {
    vi.stubGlobal("fetch", hangingFetch());
    const controller = new AbortController();
    const caught = api.get("/api/slow", { signal: controller.signal }).catch((e: unknown) => e);

    controller.abort();

    expect(isAbort(await caught)).toBe(true);
  }, 3000);

  it("обрыв сети — понятная ошибка вместо «Failed to fetch»", async () => {
    vi.stubGlobal("fetch", vi.fn(() => Promise.reject(new TypeError("Failed to fetch"))));

    const error = await api.get("/api/x").catch((e: unknown) => e);

    expect(error).toBeInstanceOf(ApiError);
    expect((error as ApiError).message).toMatch(/Нет связи с сервером/);
  });
});
