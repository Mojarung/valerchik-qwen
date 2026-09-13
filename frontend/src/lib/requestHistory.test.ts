import { afterEach, describe, expect, it, vi } from "vitest";
import {
  MAX_REQUESTS, markRequestSeen, readRequests, rememberRequest, REQUESTS_CHANGED, REQUESTS_KEY,
} from "./requestHistory";

const id = (n: number) => n.toString(16).padStart(32, "0");
const owner = (n: number) => `f${n.toString(16).padStart(31, "0")}`;

afterEach(() => {
  localStorage.clear();
  vi.restoreAllMocks();
});

describe("requestHistory", () => {
  it("помнит обращение один раз, свежее — первым", () => {
    rememberRequest(id(1), owner(1));
    rememberRequest(id(2), owner(2));
    rememberRequest(id(1), owner(1));

    expect(readRequests().map((r) => r.sessionId)).toEqual([id(1), id(2)]);
  });

  it("повторное открытие не сбрасывает отметку «просмотрено»", () => {
    rememberRequest(id(1), owner(1));
    markRequestSeen(id(1), "2026-09-13T10:00:00+00:00");
    rememberRequest(id(1), owner(1));

    expect(readRequests()[0]?.seenAt).toBe("2026-09-13T10:00:00+00:00");
  });

  it("отметка «просмотрено» не откатывается назад", () => {
    rememberRequest(id(1), owner(1));
    markRequestSeen(id(1), "2026-09-13T10:00:00+00:00");
    markRequestSeen(id(1), "2026-09-13T09:00:00+00:00");

    expect(readRequests()[0]?.seenAt).toBe("2026-09-13T10:00:00+00:00");
  });

  it("хранит не больше лимита — старые уходят", () => {
    for (let n = 1; n <= MAX_REQUESTS + 5; n++) rememberRequest(id(n), owner(n));

    const stored = readRequests();
    expect(stored).toHaveLength(MAX_REQUESTS);
    expect(stored[0]?.sessionId).toBe(id(MAX_REQUESTS + 5));
    expect(stored.some((r) => r.sessionId === id(1))).toBe(false);
  });

  it("мусор в хранилище не ломает чат: битый JSON и чужие записи отбрасываются", () => {
    localStorage.setItem(REQUESTS_KEY, "{не json");
    expect(readRequests()).toEqual([]);

    localStorage.setItem(REQUESTS_KEY, JSON.stringify([
      { sessionId: id(1), owner: owner(1), seenAt: "" },
      { sessionId: "../../admin", owner: owner(2), seenAt: "" },
      "строка",
      null,
    ]));
    expect(readRequests().map((r) => r.sessionId)).toEqual([id(1)]);
  });

  it("недоступное хранилище (приватный режим) — пустой список без исключений", () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => { throw new Error("denied"); });
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => { throw new Error("denied"); });

    expect(() => rememberRequest(id(1), owner(1))).not.toThrow();
    expect(readRequests()).toEqual([]);
  });

  it("сообщает об изменении, чтобы список перечитался", () => {
    const onChange = vi.fn();
    window.addEventListener(REQUESTS_CHANGED, onChange);
    rememberRequest(id(1), owner(1));
    window.removeEventListener(REQUESTS_CHANGED, onChange);

    expect(onChange).toHaveBeenCalledTimes(1);
  });
});
