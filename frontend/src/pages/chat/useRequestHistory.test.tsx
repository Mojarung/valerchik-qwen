import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, renderHook } from "@testing-library/react";

const { post } = vi.hoisted(() => ({ post: vi.fn() }));
vi.mock("@/lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api")>()),
  api: { post },
}));

import { rememberRequest } from "@/lib/requestHistory";
import { MAX_WAIT_MS, QUIET_MS, useRequestHistory } from "./useRequestHistory";

const ID = "a".repeat(32);
const OTHER = "c".repeat(32);
const OWNER = "b".repeat(32);

/** Ответы сервера придерживаются до явного resolve — так видно, что в полёте. */
function holdResponses() {
  const pending: ((cards: unknown[]) => void)[] = [];
  post.mockImplementation(() => new Promise((resolve) => pending.push(resolve)));
  return async (cards: unknown[] = []) => { await act(async () => { pending.shift()!(cards); }); };
}

const tick = (ms: number) => act(async () => { await vi.advanceTimersByTimeAsync(ms); });
const sentIds = (call: number) =>
  (post.mock.calls[call]![1] as { sessions: { session_id: string }[] }).sessions.map((s) => s.session_id);

beforeEach(() => {
  vi.useFakeTimers();
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  post.mockReset();
  localStorage.clear();
});

describe("useRequestHistory", () => {
  it("первая загрузка уходит сразу, без тихого окна", () => {
    rememberRequest(ID, OWNER);
    holdResponses();
    renderHook(() => useRequestHistory({ currentId: null, refreshKey: "k" }));
    expect(post).toHaveBeenCalledTimes(1);
  });

  it("переключение обращения — пачка поводов с паузами короче окна даёт ровно один запрос, и он со свежими парами", async () => {
    rememberRequest(ID, OWNER);
    const respond = holdResponses();
    const { rerender } = renderHook(({ key }) => useRequestHistory({ currentId: null, refreshKey: key }),
                                    { initialProps: { key: "a" } });
    await respond();
    expect(post).toHaveBeenCalledTimes(1);

    // Как в openSession: сброс экрана, пришёл статус, догрузилась история и пары
    rerender({ key: "a|reset" });
    await tick(QUIET_MS - 100);
    rerender({ key: "b|status" });
    await tick(QUIET_MS - 100);
    act(() => rememberRequest(OTHER, OWNER));
    rerender({ key: "b|asked" });
    await tick(QUIET_MS - 100);
    act(() => { document.dispatchEvent(new Event("visibilitychange")); });
    await tick(QUIET_MS - 1);
    expect(post).toHaveBeenCalledTimes(1);

    await tick(1);
    expect(post).toHaveBeenCalledTimes(2);
    // последнее изменение (новая пара) не потерялось
    expect(sentIds(1)).toEqual([OTHER, ID]);

    await respond();
    await tick(MAX_WAIT_MS * 2);
    expect(post).toHaveBeenCalledTimes(2);
  });

  it("повод после ответа — новый запрос", async () => {
    rememberRequest(ID, OWNER);
    const respond = holdResponses();
    const { rerender } = renderHook(({ key }) => useRequestHistory({ currentId: null, refreshKey: key }),
                                    { initialProps: { key: "a" } });
    await respond();

    rerender({ key: "b" });
    await tick(QUIET_MS);
    expect(post).toHaveBeenCalledTimes(2);
    await respond();

    act(() => rememberRequest(OTHER, OWNER));
    await tick(QUIET_MS);
    expect(post).toHaveBeenCalledTimes(3);
    expect(sentIds(2)).toEqual([OTHER, ID]);
  });

  it("повод во время полёта — один догоняющий со свежими парами, а ответ отражается в карточках", async () => {
    rememberRequest(ID, OWNER);
    const respond = holdResponses();
    const { result } = renderHook(() => useRequestHistory({ currentId: null, refreshKey: "k" }));

    act(() => rememberRequest(OTHER, OWNER));
    act(() => { document.dispatchEvent(new Event("visibilitychange")); });
    await tick(QUIET_MS * 3);
    // первый ещё в полёте — второй параллельно не уходит
    expect(post).toHaveBeenCalledTimes(1);

    await respond();
    expect(post).toHaveBeenCalledTimes(2);
    expect(sentIds(1)).toEqual([OTHER, ID]);

    const card = {
      session_id: ID, status: "operator_active", line: 2, title: "Не уходит УПД",
      created_at: "2026-09-13T08:00:00+00:00", updated_at: "2026-09-13T09:00:00+00:00", closed_at: null,
      last_message_preview: "", last_message_role: "operator", line_label: "Техподдержка", addressee_label: "",
    };
    await respond([card]);
    expect(post).toHaveBeenCalledTimes(2);
    expect(result.current.items.map((i) => [i.status, i.unread])).toEqual([["operator_active", true]]);
  });

  it("непрерывный поток поводов не откладывает запрос дольше MAX_WAIT_MS", async () => {
    rememberRequest(ID, OWNER);
    const respond = holdResponses();
    const { rerender } = renderHook(({ key }) => useRequestHistory({ currentId: null, refreshKey: key }),
                                    { initialProps: { key: "0" } });
    await respond();

    // Поводы каждые полокна: тишины нет, запрос держит только потолок
    const step = QUIET_MS / 2;
    let i = 1;
    for (; i * step < MAX_WAIT_MS; i++) {
      rerender({ key: String(i) });
      await tick(step);
    }
    expect(post).toHaveBeenCalledTimes(1);
    rerender({ key: String(i) });
    await tick(step);
    expect(post).toHaveBeenCalledTimes(2);
  });

  it("размонтирование отменяет отложенный запрос и догоняющий", async () => {
    rememberRequest(ID, OWNER);
    const respond = holdResponses();
    const first = renderHook(({ key }) => useRequestHistory({ currentId: null, refreshKey: key }),
                             { initialProps: { key: "a" } });
    await respond();
    first.rerender({ key: "b" });
    first.unmount();
    await tick(MAX_WAIT_MS * 2);
    expect(post).toHaveBeenCalledTimes(1);

    // повод во время полёта, окно истекло, а ответ пришёл уже после ухода со страницы
    const second = renderHook(() => useRequestHistory({ currentId: null, refreshKey: "k" }));
    expect(post).toHaveBeenCalledTimes(2);
    act(() => rememberRequest(OTHER, OWNER));
    await tick(QUIET_MS);
    second.unmount();
    await respond();
    await tick(MAX_WAIT_MS * 2);
    expect(post).toHaveBeenCalledTimes(2);
  });
});
