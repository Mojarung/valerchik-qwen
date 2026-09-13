import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ChatMessage, WsEvent } from "@/lib/types";

/**
 * Характеристика страницы чата: что видит человек от первого экрана до оценки.
 * Страница разбита на хуки и компоненты, и эти тесты обязаны проходить на любом
 * её устройстве: меняется код, а не то, что происходит на экране.
 */
const { get, post, upload, wsSend, sockets, saved } = vi.hoisted(() => {
  // «Меньше движения»: GSAP-анимации в jsdom не нужны, починка раскладки идёт без неё
  window.matchMedia = ((query: string) => ({
    matches: true, media: query, onchange: null,
    addEventListener: () => undefined, removeEventListener: () => undefined,
    addListener: () => undefined, removeListener: () => undefined, dispatchEvent: () => false,
  })) as unknown as typeof window.matchMedia;
  return {
    get: vi.fn(), post: vi.fn(), upload: vi.fn(), wsSend: vi.fn(),
    sockets: [] as { url: string; onEvent: (event: WsEvent) => void; protocols?: string[] }[],
    saved: { current: null as { sessionId: string; owner: string } | null },
  };
});

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

import { clearChatSession, saveChatSession } from "@/lib/chatSession";
import { useRequestsSheet } from "@/lib/requestsSheet";
import ChatPage from "./ChatPage";

const CREATED = {
  session_id: "s1", greeting: "Здравствуйте! Я Валерчик, помогу с порталом.",
  chips: ["Как подать заявку?", "Где мои контракты?"], audience: null, owner: "o1",
};

const msg = (id: string, role: ChatMessage["role"], content: string, meta: object = {}): ChatMessage =>
  ({ id, role, content, citations: [], meta, created_at: "2026-09-11T01:00:00+00:00" }) as ChatMessage;

function serve({ state, history = [] }: { state?: () => Promise<unknown>; history?: ChatMessage[] } = {}) {
  get.mockImplementation((path: string) => {
    if (path.endsWith("/messages")) return Promise.resolve(history);
    if (path.startsWith("/api/chat/suggest")) return Promise.resolve([]);
    if (state && /^\/api\/chat\/sessions\/[^/?]+$/.test(path)) return state();
    return Promise.reject(new Error(`неожиданный запрос ${path}`));
  });
  post.mockImplementation(async (path: string) => (path === "/api/chat/sessions" ? CREATED : {}));
}

/** Событие из сокета последнего открытого диалога. */
const emit = (event: Record<string, unknown>) =>
  act(() => sockets[sockets.length - 1]!.onEvent(event as unknown as WsEvent));

const createCalls = () => post.mock.calls.filter(([path]) => path === "/api/chat/sessions");

beforeEach(() => {
  // jsdom не умеет прокручивать, а лента прокручивается к последнему сообщению
  Element.prototype.scrollIntoView = vi.fn();
  // панель обращений по умолчанию свёрнута — тест разворачивает её, когда нужен список
  act(() => useRequestsSheet.getState().setPanelCollapsed(true));
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  sockets.length = 0;
  saved.current = null;
});

describe("ChatPage", () => {
  it("новый диалог: приветствие и подсказки, нажатая подсказка уходит в сокет", async () => {
    serve();
    render(<ChatPage />);

    expect(await screen.findByText(CREATED.greeting)).toBeTruthy();
    expect(saveChatSession).toHaveBeenCalledWith("s1", "o1");
    expect(sockets[0]?.url).toContain("/ws/chat/s1");
    expect(sockets[0]?.protocols).toEqual(["valerchik.v1", "owner.o1"]);

    await userEvent.click(screen.getByRole("button", { name: "Как подать заявку?" }));
    expect(wsSend).toHaveBeenCalledWith(expect.objectContaining({ type: "message", text: "Как подать заявку?" }));
  });

  it("ответ набегает потоком и встаёт в ленту целиком", async () => {
    serve();
    render(<ChatPage />);
    await screen.findByText(CREATED.greeting);

    emit({ type: "message", message: msg("m1", "user", "Как подать заявку?") });
    emit({ type: "typing" });
    emit({ type: "assistant_chunk", text: "Откройте " });
    emit({ type: "assistant_chunk", text: "раздел" });
    expect(screen.getByText("Откройте раздел")).toBeTruthy();

    emit({ type: "message", message: msg("m2", "assistant", "Откройте раздел «Заявки».", { answered: true }) });
    expect(await screen.findByText("Откройте раздел «Заявки».")).toBeTruthy();
    expect(screen.queryByText("Откройте раздел")).toBeNull();
  });

  it("возвращает прежний диалог: он ждёт оператора — в шапке адресат словами, видна плашка линии", async () => {
    saved.current = { sessionId: "s9", owner: "o9" };
    serve({
      state: async () => ({
        session_id: "s9", status: "waiting_operator", line: 2, chips: [], audience: null,
        line_label: "Техническая поддержка", addressee_label: "",
      }),
      history: [msg("m1", "user", "Не приходит письмо с кодом"), msg("m2", "assistant", "Передаю оператору.")],
    });
    render(<ChatPage />);

    expect(await screen.findByText(/Обращение передано в направление «Техническая поддержка»/)).toBeTruthy();
    expect(screen.getByText("Кому: Техническая поддержка")).toBeTruthy();
    expect(screen.getByText("Не приходит письмо с кодом")).toBeTruthy();
    expect(sockets[0]?.protocols).toEqual(["valerchik.v1", "owner.o9"]);
    expect(createCalls()).toEqual([]);
  });

  it("прежний диалог не восстановился — молча заводит новый", async () => {
    saved.current = { sessionId: "gone", owner: "o0" };
    serve({ state: () => Promise.reject(new Error("Диалог не найден")) });
    render(<ChatPage />);

    expect(await screen.findByText(CREATED.greeting)).toBeTruthy();
    expect(clearChatSession).toHaveBeenCalled();
    expect(createCalls()).toHaveLength(1);
  });

  it("закрытый диалог просит оценку; сбой отправки виден, карточка остаётся", async () => {
    serve();
    post.mockImplementation(async (path: string) => {
      if (path === "/api/chat/sessions") return CREATED;
      if (path.endsWith("/feedback")) throw new Error("Сервис недоступен");
      return {};
    });
    render(<ChatPage />);
    await screen.findByText(CREATED.greeting);
    emit({ type: "message", message: msg("m1", "user", "Как подать заявку?") });
    emit({ type: "session_closed", reason: "resolved" });

    const card = (await screen.findByText("Оцените консультацию")).parentElement!;
    await userEvent.click(within(card).getAllByRole("button")[3]!);
    await userEvent.click(screen.getByRole("button", { name: "Отправить оценку" }));

    expect(await screen.findByText("Сервис недоступен")).toBeTruthy();
    expect(screen.getByText("Оцените консультацию")).toBeTruthy();
    expect(screen.getByText(/Диалог завершён/)).toBeTruthy();
  });

  it("исправленная раскладка подменяет текст сообщения", async () => {
    serve();
    render(<ChatPage />);
    await screen.findByText(CREATED.greeting);
    emit({ type: "message", message: msg("m1", "user", "Rfr gjlfnm pfzdre?") });
    emit({ type: "layout_fixed", message_id: "m1", text: "Как подать заявку?", original: "Rfr gjlfnm pfzdre?" });

    expect(await screen.findByText("Как подать заявку?", {}, { timeout: 2000 })).toBeTruthy();
  });

  it("вопрос, набранный до открытия диалога, виден в ленте и уходит, как только диалог готов", async () => {
    serve();
    let open!: (info: typeof CREATED) => void;
    post.mockImplementation((path: string) => (path === "/api/chat/sessions"
      ? new Promise((resolve) => { open = resolve; })
      : Promise.resolve({})));
    render(<ChatPage />);
    const field = screen.getByRole("textbox", { name: "Сообщение" });

    fireEvent.change(field, { target: { value: "Не приходит письмо" } });
    fireEvent.keyDown(field, { key: "Enter" });

    // Enter не пропал: текст ушёл из поля в ленту и подписан, что с ним будет
    expect(screen.getByText("Не приходит письмо")).toBeTruthy();
    expect(screen.getByText(/отправлю, как только чат откроется/)).toBeTruthy();
    expect((field as HTMLTextAreaElement).value).toBe("");
    expect(wsSend).not.toHaveBeenCalled();

    await act(async () => { open(CREATED); });
    await waitFor(() => expect(wsSend).toHaveBeenCalledWith(
      expect.objectContaining({ type: "message", text: "Не приходит письмо" })));
    emit({ type: "message", message: msg("m1", "user", "Не приходит письмо") });
    // копия уступила место самой реплике — в ленте она одна
    expect(screen.getAllByText("Не приходит письмо")).toHaveLength(1);
    expect(screen.queryByText(/отправлю, как только/)).toBeNull();
  });

  it("«Мои обращения»: прошлое обращение открывается из списка вместе с историей и статусом", async () => {
    const PAST = "a".repeat(32);
    const PAST_OWNER = "b".repeat(32);
    localStorage.setItem("valerchik-requests", JSON.stringify([{ sessionId: PAST, owner: PAST_OWNER, seenAt: "" }]));
    get.mockImplementation((path: string) => {
      if (path === `/api/chat/sessions/${PAST}/messages`) {
        return Promise.resolve([msg("p1", "user", "Как создать оферту?"), msg("p2", "operator", "Оферта создана.")]);
      }
      if (path === `/api/chat/sessions/${PAST}`) {
        return Promise.resolve({ session_id: PAST, status: "resolved", line: 1, chips: [], audience: null,
                                 line_label: "Консультации", addressee_label: "" });
      }
      if (path.endsWith("/messages")) return Promise.resolve([]);
      if (path.startsWith("/api/chat/suggest")) return Promise.resolve([]);
      return Promise.reject(new Error(`неожиданный запрос ${path}`));
    });
    post.mockImplementation(async (path: string, body?: { sessions?: { session_id: string }[] }) => {
      if (path === "/api/chat/sessions") return CREATED;
      if (path === "/api/chat/sessions/history") {
        expect(body?.sessions?.map((s) => s.session_id)).toEqual([PAST]);
        return [{
          session_id: PAST, status: "resolved", line: 1, title: "Как создать оферту?",
          created_at: "2026-09-12T08:00:00+00:00", updated_at: "2026-09-12T09:00:00+00:00", closed_at: null,
          last_message_preview: "Оферта создана.", last_message_role: "operator",
          line_label: "Консультации", addressee_label: "",
        }];
      }
      return {};
    });
    render(<ChatPage />);
    await screen.findByText(CREATED.greeting);

    const panel = screen.getByRole("complementary", { name: "Мои обращения" });
    await userEvent.click(within(panel).getByRole("button", { name: /^Показать мои обращения/ }));
    const past = await within(panel).findByRole("button", { name: /Как создать оферту\?/ });
    // оператор ответил, а человек этого ещё не видел
    expect(within(past).getByText("Новый ответ")).toBeTruthy();
    await userEvent.click(past);

    expect(saveChatSession).toHaveBeenLastCalledWith(PAST, PAST_OWNER);
    expect(await screen.findByText("Оферта создана.")).toBeTruthy();
    expect(screen.getByText("Диалог завершён. Спасибо за обращение!", { exact: false })).toBeTruthy();
    expect(sockets[sockets.length - 1]?.protocols).toEqual(["valerchik.v1", `owner.${PAST_OWNER}`]);
    localStorage.clear();
  });

  it("«Мои обращения»: панель открывается свёрнутой, тот же переключатель её возвращает", async () => {
    serve();
    render(<ChatPage />);
    await screen.findByText(CREATED.greeting);
    const panel = screen.getByRole("complementary", { name: "Мои обращения" });
    expect(within(panel).queryByRole("heading", { name: "Мои обращения" })).toBeNull();

    await userEvent.click(within(panel).getByRole("button", { name: "Показать мои обращения" }));
    expect(within(panel).getByRole("heading", { name: "Мои обращения" })).toBeTruthy();
    expect(localStorage.getItem("valerchik-requests-panel")).toBe("expanded");

    await userEvent.click(within(panel).getByRole("button", { name: "Скрыть мои обращения" }));
    expect(within(panel).queryByRole("heading", { name: "Мои обращения" })).toBeNull();
    expect(localStorage.getItem("valerchik-requests-panel")).toBeNull();
  });
});
