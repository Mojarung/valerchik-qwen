import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import type { ChatMessage, WsEvent } from "@/lib/types";

/**
 * Открытое обращение оператора: подписка на комнаты сокета, переписка и
 * подсказки, черновик копилота, события очереди и действия с их отказами.
 */
const { get, post, upload, wsSend, handlers } = vi.hoisted(() => ({
  get: vi.fn(), post: vi.fn(), upload: vi.fn(), wsSend: vi.fn(),
  handlers: [] as ((event: WsEvent) => void)[],
}));

vi.mock("@/lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api")>()),
  api: { get, post, upload },
}));
vi.mock("@/lib/ws", () => ({
  connectWs: (_url: string, onEvent: (event: WsEvent) => void) => {
    handlers.push(onEvent);
    return { send: wsSend, close: vi.fn() };
  },
}));

import { SLOW_TIMEOUT_MS } from "@/lib/api";
import { useConversation } from "./useConversation";

const A = "a".repeat(32);
const B = "b".repeat(32);
const msg = (id: string, role: ChatMessage["role"], content: string): ChatMessage =>
  ({ id, role, content, citations: [], meta: {}, created_at: "2026-09-11T01:00:00+00:00" }) as ChatMessage;
const HISTORY = [msg("m1", "user", "Как подписать контракт?")];
const CARD = { article_id: "k1", question: "Подписание контракта", answer: "Раздел «Контракты».", score: 0.8, category: "Контракты" };

function serve() {
  get.mockImplementation(async (path: string) => {
    if (path.endsWith("/messages")) return HISTORY;
    if (path.endsWith("/suggestions")) return [CARD];
    throw new Error(`неожиданный запрос ${path}`);
  });
  post.mockResolvedValue({});
}

function setup() {
  const onQueueChanged = vi.fn();
  const onAssigned = vi.fn();
  const hook = renderHook(() => useConversation({ onQueueChanged, onAssigned }));
  return { ...hook, onQueueChanged, onAssigned };
}

async function opened(sid = A) {
  serve();
  const ctx = setup();
  await act(async () => { await ctx.result.current.open(sid); });
  return ctx;
}

/** Событие из сокета операторской. */
function emit(event: Record<string, unknown>) {
  const handler = handlers[handlers.length - 1];
  expect(handler, "сокет операторской не подключён").toBeDefined();
  act(() => handler!(event as unknown as WsEvent));
}

beforeEach(() => {
  handlers.length = 0;
});
afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("useConversation", () => {
  it("открытие: подписка на комнаты обращения, переписка и подсказки загружены", async () => {
    const { result } = await opened();

    expect(result.current.selected).toBe(A);
    expect(wsSend).toHaveBeenCalledWith({ type: "subscribe", room: `session:${A}` });
    expect(wsSend).toHaveBeenCalledWith({ type: "subscribe", room: `copilot:${A}` });
    expect(result.current.messages).toEqual(HISTORY);
    expect(result.current.suggestions).toEqual([CARD]);
    expect(result.current.historyState).toBe("idle");

    await act(async () => { await result.current.open(B); });
    expect(wsSend).toHaveBeenCalledWith({ type: "unsubscribe", room: `session:${A}` });
    expect(wsSend).toHaveBeenCalledWith({ type: "unsubscribe", room: `copilot:${A}` });
    expect(result.current.selected).toBe(B);
  });

  it("переписка не загрузилась — ошибка на месте, повтор загружает", async () => {
    serve();
    get.mockRejectedValueOnce(new Error("Сервис недоступен"));
    const { result } = setup();
    await act(async () => { await result.current.open(A); });

    expect(result.current.historyState).toBe("error");
    expect(result.current.historyError).toBe("Сервис недоступен");

    act(() => result.current.retryHistory());
    await waitFor(() => expect(result.current.historyState).toBe("idle"));
    expect(result.current.messages).toEqual(HISTORY);
  });

  it("опоздавшая переписка прежнего обращения не попадает в новое", async () => {
    serve();
    let late: (value: ChatMessage[]) => void = () => undefined;
    get.mockImplementation(async (path: string) => {
      if (path === `/api/operator/sessions/${A}/messages`) return new Promise((resolve) => { late = resolve; });
      if (path.endsWith("/messages")) return [msg("n1", "user", "Второе обращение")];
      return [];
    });
    const { result } = setup();

    act(() => { void result.current.open(A); });
    await act(async () => { await result.current.open(B); });
    await act(async () => { late(HISTORY); });

    expect(result.current.selected).toBe(B);
    expect(result.current.messages.map((m) => m.id)).toEqual(["n1"]);
  });

  it("черновик копилота принимается только к последнему сообщению клиента", async () => {
    const { result } = await opened();

    emit({ type: "copilot", suggestions: [CARD], draft_pending: true, reply_to: "m2" });
    expect(result.current.draftLoading).toBe(true);
    expect(result.current.draft).toBeNull();

    emit({ type: "copilot_draft", reply_to: "m1", draft: "Ответ на прежний вопрос", generated: true });
    expect(result.current.draft).toBeNull();

    emit({ type: "copilot_draft", reply_to: "m2", draft: "Откройте раздел «Контракты».", generated: true });
    expect(result.current.draft).toMatchObject({ text: "Откройте раздел «Контракты».", generated: true });
    expect(result.current.draftLoading).toBe(false);
  });

  it("реплика клиента из сокета добавляется один раз и гасит «печатает»", async () => {
    const { result } = await opened();

    emit({ type: "user_typing", text: "Как под" });
    expect(result.current.userTyping).toBe("Как под");
    const next = msg("m2", "user", "Как подписать?");
    emit({ type: "message", message: next });
    emit({ type: "message", message: next });

    expect(result.current.messages).toHaveLength(2);
    expect(result.current.userTyping).toBe("");
  });

  // Настоящая, а не поддельная пауза: throttle читает Date.now(), а подмена
  // глобальных таймеров здесь однажды задела 420-мс таймаут в соседнем файле
  // сьюта при совместном запуске — реальные 2с надёжнее подмены.
  it("notifyTyping: сигнал в комнату обращения, но не чаще, чем раз в throttle-окно", async () => {
    const { result } = await opened();
    wsSend.mockClear();

    act(() => result.current.notifyTyping());
    expect(wsSend).toHaveBeenCalledWith({ type: "typing", room: `session:${A}` });
    expect(wsSend).toHaveBeenCalledTimes(1);

    // Клавиша сразу следом — не отдельный кадр сокета, тот же throttle-период
    act(() => result.current.notifyTyping());
    expect(wsSend).toHaveBeenCalledTimes(1);

    await new Promise((resolve) => setTimeout(resolve, 2050));
    act(() => result.current.notifyTyping());
    expect(wsSend).toHaveBeenCalledTimes(2);
  }, 10000);

  it("события очереди — перезапрос; назначение — наружу; отнятое обращение — ошибка на месте", async () => {
    const { result, onQueueChanged, onAssigned } = await opened();

    emit({ type: "new_ticket", session_id: B });
    expect(onQueueChanged).toHaveBeenCalledTimes(1);

    const assignment = { type: "assigned", session_id: B, line: 1, waiting_seconds: 120, question: "Где контракт?", reason: "" };
    emit(assignment);
    expect(onAssigned).toHaveBeenCalledWith(assignment);

    emit({ type: "reclaimed", session_id: A });
    expect(result.current.actionError).toMatch(/вернулось в очередь/);
  });

  it("ответ: отказ — false и текст ошибки, успех — true и ошибка снята", async () => {
    const { result } = await opened();
    post.mockRejectedValueOnce(new Error("Сообщение не прошло проверку"));

    let ok = true;
    await act(async () => { ok = await result.current.reply("  Добрый день  "); });
    expect(ok).toBe(false);
    expect(result.current.actionError).toBe("Сообщение не прошло проверку");

    await act(async () => { ok = await result.current.reply("  Добрый день  "); });
    expect(ok).toBe(true);
    expect(post).toHaveBeenLastCalledWith(`/api/operator/sessions/${A}/message`, { text: "Добрый день", request_id: expect.any(String) });
    expect(result.current.actionError).toBe("");
  });

  it("черновик по запросу ждёт дольше обычного и честно говорит, что статьи нет", async () => {
    const { result } = await opened();
    post.mockResolvedValueOnce({ draft: "", generated: false, status: "error", sources: [], error: "Модель недоступна" });

    await act(async () => { await result.current.requestDraft(); });

    expect(post).toHaveBeenLastCalledWith(
      `/api/operator/sessions/${A}/draft`, undefined, { timeoutMs: SLOW_TIMEOUT_MS });
    expect(result.current.draft).toMatchObject({text: "", status: "error"});
    expect(result.current.actionError).toBe("Модель недоступна");
  });

  it("взять и завершить: отказ виден, успешное завершение закрывает обращение", async () => {
    const { result } = await opened();
    post.mockRejectedValueOnce(new Error("Обращение уже взял другой оператор"));

    let ok = true;
    await act(async () => { ok = await result.current.take(); });
    expect(ok).toBe(false);
    expect(result.current.actionError).toBe("Обращение уже взял другой оператор");

    await act(async () => { ok = await result.current.close(); });
    expect(ok).toBe(true);
    expect(result.current.selected).toBeNull();
  });

});
