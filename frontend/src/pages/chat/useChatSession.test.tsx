import { afterEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import type { ChatMessage, WsEvent } from "@/lib/types";

/**
 * Диалог гостя без разметки: создание и возврат, поток ответа, события сокета,
 * отправка и оценка — по одному правилу на тест.
 */
const { get, post, upload, wsSend, sockets, saved } = vi.hoisted(() => ({
  get: vi.fn(), post: vi.fn(), upload: vi.fn(), wsSend: vi.fn(),
  sockets: [] as { url: string; onEvent: (event: WsEvent) => void; protocols?: string[] }[],
  saved: { current: null as { sessionId: string; owner: string } | null },
}));

vi.mock("@/lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api")>()),
  api: { get, post, upload },
}));
vi.mock("@/lib/ws", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/ws")>()),
  connectWs: (url: string, onEvent: (event: WsEvent) => void, _onStatus?: unknown, protocols?: string[]) => {
    sockets.push({ url, onEvent, protocols });
    return { send: wsSend, close: vi.fn() };
  },
}));
vi.mock("@/lib/chatSession", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/chatSession")>()),
  readChatSession: () => saved.current,
  saveChatSession: vi.fn((sessionId: string, owner: string) => { saved.current = { sessionId, owner }; }),
  clearChatSession: vi.fn(() => { saved.current = null; }),
}));
vi.mock("@/lib/speech", () => ({
  speechEnabled: () => false,
  setSpeechEnabled: vi.fn(),
  stopSpeaking: vi.fn(),
  speak: vi.fn(async () => undefined),
}));

import { useAudience } from "@/lib/audience";
import { clearChatSession, saveChatSession } from "@/lib/chatSession";
import { speak } from "@/lib/speech";
import { useChatSession, type LayoutFix } from "./useChatSession";

const CREATED = { session_id: "s1", greeting: "Здравствуйте!", chips: ["Как подать заявку?"], audience: null, owner: "o1" };

const msg = (id: string, role: ChatMessage["role"], content: string, meta: object = {}): ChatMessage =>
  ({ id, role, content, citations: [], meta, created_at: "2026-09-11T01:00:00+00:00" }) as ChatMessage;

function serve(state?: () => Promise<unknown>) {
  get.mockImplementation((path: string) => {
    if (path.endsWith("/messages")) return Promise.resolve([]);
    if (state && /^\/api\/chat\/sessions\/[^/?]+$/.test(path)) return state();
    return Promise.reject(new Error(`неожиданный запрос ${path}`));
  });
  post.mockImplementation(async (path: string) => (path === "/api/chat/sessions" ? CREATED : {}));
}

function setup() {
  const speakRef = { current: false };
  const onLayoutFixed = vi.fn((_fix: LayoutFix, apply: () => void) => apply());
  const hook = renderHook(() => useChatSession({ speakRef, onLayoutFixed }));
  return { ...hook, speakRef, onLayoutFixed };
}

async function started() {
  serve();
  const ctx = setup();
  await waitFor(() => expect(ctx.result.current.session?.session_id).toBe("s1"));
  return ctx;
}

/** Событие из сокета последнего открытого диалога. */
const emit = (event: Record<string, unknown>) =>
  act(() => sockets[sockets.length - 1]!.onEvent(event as unknown as WsEvent));

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  sockets.length = 0;
  saved.current = null;
  useAudience.getState().setAudience(null);
});

describe("useChatSession", () => {
  it("заводит диалог: владелец сохранён до первого запроса, сокет — с его токеном", async () => {
    const { result } = await started();

    expect(result.current.messages).toEqual([]);
    const historyCall = get.mock.calls.findIndex(([path]) => String(path).endsWith("/messages"));
    expect(vi.mocked(saveChatSession).mock.invocationCallOrder[0])
      .toBeLessThan(get.mock.invocationCallOrder[historyCall]!);
    expect(sockets[0]?.url).toContain("/ws/chat/s1");
    expect(sockets[0]?.protocols).toEqual(["valerchik.v1", "owner.o1"]);
  });

  it.each([
    ["resolved", "resolved", null],
    ["closed_violation", "violation", null],
    ["operator_active", null, 3],
  ])("возвращает прежний диалог со статусом %s", async (status, closed, line) => {
    saved.current = { sessionId: "s9", owner: "o9" };
    serve(async () => ({ session_id: "s9", status, line: 3, chips: [], audience: null }));
    const { result } = setup();

    await waitFor(() => expect(result.current.session?.session_id).toBe("s9"));
    expect(result.current.closed).toBe(closed);
    expect(result.current.escalatedLine).toBe(line);
    expect(post.mock.calls.filter(([path]) => path === "/api/chat/sessions")).toEqual([]);
  });

  it("прежний диалог не восстановился — чистит куку и заводит новый", async () => {
    saved.current = { sessionId: "gone", owner: "o0" };
    serve(() => Promise.reject(new Error("Диалог не найден")));
    const { result } = setup();

    await waitFor(() => expect(result.current.session?.session_id).toBe("s1"));
    expect(clearChatSession).toHaveBeenCalled();
  });

  it("новый диалог по кнопке сбрасывает ленту и растит номер начала", async () => {
    const { result } = await started();
    emit({ type: "message", message: msg("m1", "user", "Вопрос") });
    const before = result.current.generation;

    await act(async () => { await result.current.startSession(true); });

    expect(result.current.messages).toEqual([]);
    expect(result.current.generation).toBe(before + 1);
    expect(sockets).toHaveLength(2);
  });

  it("поток ответа: куски копятся, готовое сообщение их сменяет, повтор по id не дублируется", async () => {
    const { result } = await started();

    emit({ type: "typing" });
    expect(result.current.mood).toBe("thinking");
    emit({ type: "assistant_chunk", text: "Откройте " });
    emit({ type: "assistant_chunk", text: "раздел" });
    expect(result.current.streamText).toBe("Откройте раздел");
    expect(result.current.mood).toBe("speaking");

    const answer = msg("m2", "assistant", "Откройте раздел «Заявки».", { answered: true });
    emit({ type: "message", message: answer });
    emit({ type: "message", message: answer });

    expect(result.current.streamText).toBe("");
    expect(result.current.messages).toHaveLength(1);
    expect(result.current.mood).toBe("happy");
  });

  it("не нашёл ответа — извиняется; эскалация — линия; роль из слов — в ползунок", async () => {
    const { result } = await started();

    emit({ type: "message", message: msg("m2", "assistant", "Не знаю.", { answered: false }) });
    expect(result.current.mood).toBe("sorry");
    emit({ type: "escalated", line: 2 });
    expect(result.current.escalatedLine).toBe(2);
    emit({ type: "audience", audience: "customer" });
    expect(useAudience.getState().audience).toBe("customer");
  });

  it("после эскалации отправка не изображает поиск ботом, а оператор снимает «thinking» и называет себя", async () => {
    const { result } = await started();

    emit({ type: "escalated", line: 2 });
    expect(result.current.operatorName).toBeNull();

    // Реплика в очереди — Валерчик её уже не ищет, «thinking» не должен появляться
    act(() => { result.current.send("Ещё жду"); });
    expect(result.current.mood).not.toBe("thinking");

    emit({
      type: "message",
      message: msg("m-join", "system", "Оператор Мария Соколова подключился к диалогу.",
        { kind: "operator_joined", operator_name: "Мария Соколова" }),
    });
    expect(result.current.operatorName).toBe("Мария Соколова");
    expect(result.current.mood).toBe("idle");

    // Застрявшее «thinking» с реплики, заданной прямо перед ответом оператора, тоже снимается
    act(() => { result.current.setMoodFor("thinking"); });
    emit({ type: "message", message: msg("m-op", "operator", "Здравствуйте, помогу.") });
    expect(result.current.mood).toBe("idle");
  });

  // Реальные, а не поддельные таймеры: рядом в сьюте есть тест с настоящим
  // 420-мс таймаутом (починка раскладки), и подмена глобального setTimeout
  // здесь однажды увела его в сторону при совместном запуске файлов.
  it("operator_typing: гаснет сам через паузу, повтор её продлевает, а реплика — сразу", async () => {
    const { result } = await started();

    emit({ type: "operator_typing" });
    expect(result.current.operatorTyping).toBe(true);

    await new Promise((resolve) => setTimeout(resolve, 2000));
    expect(result.current.operatorTyping).toBe(true);   // таймаут 2.5с ещё не истёк
    emit({ type: "operator_typing" });                  // повтор — окно начинается заново

    await new Promise((resolve) => setTimeout(resolve, 2000));
    expect(result.current.operatorTyping).toBe(true);   // продлилось повтором

    await waitFor(() => expect(result.current.operatorTyping).toBe(false), { timeout: 2000 });

    emit({ type: "operator_typing" });
    expect(result.current.operatorTyping).toBe(true);
    emit({ type: "message", message: msg("m-op2", "operator", "Секунду.") });
    expect(result.current.operatorTyping).toBe(false);  // реплика гасит сразу, не дожидаясь таймаута
  }, 10000);

  it("закрытие за нарушение: диалог закрыт, маскот настороже", async () => {
    const { result } = await started();

    emit({ type: "session_closed", reason: "violation" });

    expect(result.current.closed).toBe("violation");
    expect(result.current.mood).toBe("alert");
  });

  it("починка раскладки: исправление уходит наружу, apply подменяет текст и хранит оригинал", async () => {
    const { result, onLayoutFixed } = await started();
    emit({ type: "message", message: msg("m1", "user", "Rfr gjlfnm?") });

    emit({ type: "layout_fixed", message_id: "m1", text: "Как подать?", original: "Rfr gjlfnm?" });

    await waitFor(() => expect(onLayoutFixed).toHaveBeenCalled());
    expect(onLayoutFixed.mock.calls[0]![0]).toEqual({
      messageId: "m1", text: "Как подать?", original: "Rfr gjlfnm?", kinds: ["layout"],
    });
    // apply зовётся из таймера хука, вне act: React применяет подмену текста
    // следующей отрисовкой, а не в тот же миг. Читать ленту сразу — через раз
    // застать старый текст (так тест и мигал в полном прогоне), поэтому ждём
    // отрисовку, а проверяем ровно то же.
    await waitFor(() => expect(result.current.messages[0]).toMatchObject({
      content: "Как подать?", meta: { layout_fixed: true, original: "Rfr gjlfnm?" },
    }));
  });

  it("опечатки: та же починка, в meta флаг typo_fixed, раскладку не приписывает", async () => {
    const { result, onLayoutFixed } = await started();
    emit({ type: "message", message: msg("m1", "user", "Как пройти регестрацию?") });

    emit({
      type: "layout_fixed", message_id: "m1", text: "Как пройти регистрацию?",
      original: "Как пройти регестрацию?", kinds: ["typo"],
    });

    await waitFor(() => expect(onLayoutFixed).toHaveBeenCalled());
    await waitFor(() => expect(result.current.messages[0]).toMatchObject({
      content: "Как пройти регистрацию?", meta: { typo_fixed: true, original: "Как пройти регестрацию?" },
    }));
    expect(result.current.messages[0]!.meta).not.toHaveProperty("layout_fixed");
  });

  it("отправка: текст без пробелов по краям уходит в сокет; пустой и в закрытый диалог — нет", async () => {
    const { result } = await started();

    let sent = false;
    act(() => { sent = result.current.send("  Как подать заявку?  "); });
    expect(sent).toBe(true);
    expect(wsSend).toHaveBeenCalledWith(expect.objectContaining({ type: "message", text: "Как подать заявку?" }));
    expect(result.current.mood).toBe("thinking");

    act(() => { sent = result.current.send("   "); });
    expect(sent).toBe(false);
    emit({ type: "session_closed", reason: "resolved" });
    act(() => { sent = result.current.send("Ещё вопрос"); });
    expect(sent).toBe(false);
    expect(wsSend).toHaveBeenCalledTimes(1);
  });

  it("оценка: сбой — false и понятная ошибка; успех — true и радость маскота", async () => {
    const { result } = await started();
    post.mockImplementationOnce(async () => { throw new Error("Сервис недоступен"); });

    let ok = true;
    await act(async () => { ok = await result.current.submitRating(4, ""); });
    expect(ok).toBe(false);
    expect(result.current.error).toBe("Сервис недоступен");

    await act(async () => { ok = await result.current.submitRating(5, "Спасибо"); });
    expect(ok).toBe(true);
    expect(post).toHaveBeenLastCalledWith("/api/chat/sessions/s1/feedback", { rating: 5, comment: "Спасибо" });
    expect(result.current.mood).toBe("happy");
  });

  it("озвучивает ответ, только когда озвучка включена", async () => {
    const { speakRef } = await started();

    emit({ type: "message", message: msg("m2", "assistant", "Первый ответ.", { answered: true }) });
    expect(speak).not.toHaveBeenCalled();
    speakRef.current = true;
    emit({ type: "message", message: msg("m3", "assistant", "Второй ответ.", { answered: true }) });
    expect(speak).toHaveBeenCalledWith("Второй ответ.");
  });

  it("адресат словами: из события эскалации и из состояния восстановленного диалога", async () => {
    const { result } = await started();
    emit({ type: "escalated", line: 2, line_label: "Техническая поддержка", addressee_label: "" });
    expect(result.current.addressee).toEqual({ line: 2, lineLabel: "Техническая поддержка", externalLabel: "" });
    cleanup();

    saved.current = { sessionId: "s9", owner: "o9" };
    serve(async () => ({
      session_id: "s9", status: "waiting_operator", line: 1, chips: [], audience: null,
      line_label: "Консультации", addressee_label: "Служба контроля качества портала (СКК)",
    }));
    const resumed = setup();
    await waitFor(() => expect(resumed.result.current.session?.session_id).toBe("s9"));
    expect(resumed.result.current.addressee?.externalLabel).toBe("Служба контроля качества портала (СКК)");
    expect(resumed.result.current.escalatedLine).toBe(1);
  });

  it("реплика до открытия диалога не теряется: ждёт в очереди, уходит, как только он готов", async () => {
    serve();
    let open!: (info: typeof CREATED) => void;
    post.mockImplementation((path: string) => (path === "/api/chat/sessions"
      ? new Promise((resolve) => { open = resolve; })
      : Promise.resolve({})));
    const { result } = setup();
    await waitFor(() => expect(post).toHaveBeenCalled());

    let sent = false;
    act(() => { sent = result.current.send("  Как подать заявку?  "); });
    expect(sent).toBe(true);
    expect(result.current.connecting).toBe(true);
    expect(result.current.pending).toEqual([expect.objectContaining({ text: "Как подать заявку?", queued: true })]);
    expect(wsSend).not.toHaveBeenCalled();

    await act(async () => { open(CREATED); });
    await waitFor(() => expect(wsSend).toHaveBeenCalledWith(
      expect.objectContaining({ type: "message", text: "Как подать заявку?" })));
    expect(wsSend).toHaveBeenCalledTimes(1);
    // ушла, но копия видна, пока сервер не покажет саму реплику — иначе мигнул бы стартовый экран
    expect(result.current.pending).toEqual([expect.objectContaining({ queued: false })]);

    emit({ type: "message", message: msg("m1", "user", "Как подать заявку?") });
    expect(result.current.pending).toEqual([]);
  });

  it("диалог не открылся: реплика остаётся неотправленной, новая попытка её отправляет", async () => {
    serve();
    post.mockImplementationOnce(async () => { throw new Error("Сервис недоступен"); });
    const { result } = setup();
    await waitFor(() => expect(result.current.error).toBe("Сервис недоступен"));
    expect(result.current.connecting).toBe(false);

    // Новая реплика и есть повод попробовать ещё раз — диалог открывается заново
    act(() => { result.current.send("Не приходит письмо"); });
    await waitFor(() => expect(wsSend).toHaveBeenCalledWith(
      expect.objectContaining({ text: "Не приходит письмо" })));
    expect(result.current.session?.session_id).toBe("s1");
    expect(result.current.error).toBeNull();
  });

  it("отказ сервера по сокету: причина видна, копия реплики снята, маскот не «думает»", async () => {
    serve();
    let open!: (info: typeof CREATED) => void;
    post.mockImplementation((path: string) => (path === "/api/chat/sessions"
      ? new Promise((resolve) => { open = resolve; })
      : Promise.resolve({})));
    const { result } = setup();
    await waitFor(() => expect(post).toHaveBeenCalled());
    act(() => { result.current.send("Вопрос"); });
    await act(async () => { open(CREATED); });
    await waitFor(() => expect(wsSend).toHaveBeenCalled());

    emit({ type: "error", detail: "Слишком много сообщений, подождите минуту" });

    expect(result.current.error).toBe("Слишком много сообщений, подождите минуту");
    expect(result.current.pending).toEqual([]);
    expect(result.current.mood).toBe("idle");
  });
});
