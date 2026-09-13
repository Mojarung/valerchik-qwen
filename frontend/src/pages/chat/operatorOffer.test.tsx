import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ChatMessage, WsEvent } from "@/lib/types";

/**
 * «Позвать оператора» под отказом и «да / нет» под вопросом «передать
 * специалисту?». По ТЗ бот без ответа обязан предложить оператора, и кнопка —
 * то, что делает предложение настоящим. Проверяем и правило (под каким ответом
 * кнопки живы), и страницу целиком: что уходит в сокет и когда кнопки гаснут.
 */
const { get, post, upload, wsSend, sockets, saved } = vi.hoisted(() => {
  // «Меньше движения»: GSAP-анимации в jsdom не нужны
  window.matchMedia = ((query: string) => ({
    matches: true, media: query, onchange: null,
    addEventListener: () => undefined, removeEventListener: () => undefined,
    addListener: () => undefined, removeListener: () => undefined, dispatchEvent: () => false,
  })) as unknown as typeof window.matchMedia;
  return {
    get: vi.fn(), post: vi.fn(), upload: vi.fn(), wsSend: vi.fn(),
    sockets: [] as { url: string; onEvent: (event: WsEvent) => void }[],
    saved: { current: null as { sessionId: string; owner: string } | null },
  };
});

vi.mock("@/lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api")>()),
  api: { get, post, upload },
}));
vi.mock("@/lib/ws", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/ws")>()),
  connectWs: (url: string, onEvent: (event: WsEvent) => void) => {
    sockets.push({ url, onEvent });
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

import ChatPage from "@/pages/ChatPage";
import { liveOperatorOffer, operatorOfferOf } from "./operatorOffer";

const CREATED = {
  session_id: "s1", greeting: "Здравствуйте! Я Валерчик, помогу с порталом.",
  chips: [], audience: null, owner: "o1",
};

const msg = (id: string, role: ChatMessage["role"], content: string, meta: object = {}): ChatMessage =>
  ({ id, role, content, citations: [], meta, created_at: "2026-09-11T01:00:00+00:00" }) as ChatMessage;

const QUESTION = msg("u1", "user", "Как оформить налоговый вычет?");
const REFUSAL = msg("a1", "assistant", "В базе знаний нет ответа на этот вопрос. Могу позвать оператора.",
  { answered: false, offer_operator: true });
const ASK = msg("a2", "assistant", "Передать обращение специалисту?",
  { kind: "operator_offer", offer_operator: true, answered: false });

const CALL = /^Позвать оператора/;
const YES = /^Да, передать специалисту/;
const NO = /^Нет, спрошу иначе/;

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

/** Новый диалог, в котором на вопрос пришёл ответ `answer`. */
async function answered(answer: ChatMessage) {
  serve();
  render(<ChatPage />);
  await screen.findByText(CREATED.greeting);
  emit({ type: "message", message: QUESTION });
  emit({ type: "message", message: answer });
}

const sentTexts = () => wsSend.mock.calls.map(([frame]) => (frame as { text?: string }).text);

beforeEach(() => {
  // jsdom не умеет прокручивать, а лента прокручивается к последнему сообщению
  Element.prototype.scrollIntoView = vi.fn();
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  sockets.length = 0;
  saved.current = null;
});

describe("operatorOffer", () => {
  const idle = { closed: false, escalated: false, busy: false };

  it("вид предложения читается из meta строго, и только у ответа Валерчика", () => {
    expect(operatorOfferOf(REFUSAL)).toBe("call");
    expect(operatorOfferOf(ASK)).toBe("confirm");
    expect(operatorOfferOf(msg("a3", "assistant", "Вот ответ.", { answered: true }))).toBeNull();
    expect(operatorOfferOf(msg("a4", "assistant", "Вот ответ.", { offer_operator: "true" }))).toBeNull();
    expect(operatorOfferOf(msg("u2", "user", "Позовите", { offer_operator: true }))).toBeNull();
  });

  it("живо только в последней реплике и пока диалог ведёт Валерчик", () => {
    expect(liveOperatorOffer([QUESTION, REFUSAL], idle)).toBe("a1");
    // системная строка после ответа реплику не заменяет
    expect(liveOperatorOffer([REFUSAL, msg("s1", "system", "Оцените ответ", { kind: "feedback_request" })], idle))
      .toBe("a1");
    expect(liveOperatorOffer([REFUSAL, msg("u2", "user", "Ладно, другой вопрос")], idle)).toBeNull();
    expect(liveOperatorOffer([REFUSAL], { ...idle, escalated: true })).toBeNull();
    expect(liveOperatorOffer([REFUSAL], { ...idle, closed: true })).toBeNull();
    expect(liveOperatorOffer([REFUSAL], { ...idle, busy: true })).toBeNull();
    expect(liveOperatorOffer([], idle)).toBeNull();
  });
});

describe("ChatPage: кнопка «Позвать оператора»", () => {
  it("под отказом: реплика уходит обычной отправкой, черновик цел, кнопка гаснет и после эскалации не возвращается", async () => {
    await answered(REFUSAL);
    const field = screen.getByRole("textbox", { name: "Сообщение" });
    // Черновик — одним изменением, а не посимвольным набором: userEvent.type
    // перерисовывал всю страницу на каждую букву и под нагрузкой полного прогона
    // не укладывался в 5 с. Проверяется то же — что кнопка черновик не трогает.
    fireEvent.change(field, { target: { value: "номер заявки 123" } });
    expect((field as HTMLTextAreaElement).value).toBe("номер заявки 123");

    await userEvent.click(await screen.findByRole("button", { name: CALL }));

    expect(wsSend).toHaveBeenCalledTimes(1);
    expect(wsSend).toHaveBeenCalledWith(expect.objectContaining({ type: "message", text: "Позовите оператора" }));
    expect((field as HTMLTextAreaElement).value).toBe("номер заявки 123");
    // ждём ответа — повторное нажатие позвало бы оператора дважды
    expect(screen.queryByRole("button", { name: CALL })).toBeNull();

    emit({ type: "message", message: msg("u2", "user", "Позовите оператора") });
    emit({ type: "message", message: msg("a9", "assistant", "Передаю обращение оператору.", { escalated: true }) });
    emit({ type: "escalated", line: 1 });

    expect(screen.getByText(/Обращение передано в направление «Поддержка»/)).toBeTruthy();
    expect(screen.queryByRole("button", { name: CALL })).toBeNull();
    // Самый длинный сценарий файла: вся страница чата и пять событий сокета.
    // Один он укладывается в полторы секунды, но в параллельном прогоне всего
    // сьюта jsdom делит процессор с соседями — запас, как у соседних медленных тестов.
  }, 10000);

  it("эскалация снимает кнопку и без новых реплик", async () => {
    await answered(REFUSAL);
    expect(screen.getByRole("button", { name: CALL })).toBeTruthy();

    emit({ type: "escalated", line: 2 });

    expect(screen.queryByRole("button", { name: CALL })).toBeNull();
  });

  it("вопрос «передать специалисту?»: «нет» ведёт в поле ввода, «да» зовёт оператора", async () => {
    await answered(ASK);
    // у вопроса-предложения нет оценки «помог / не помог»: это не ответ
    expect(screen.queryByRole("button", { name: "Ответ помог" })).toBeNull();

    await userEvent.click(screen.getByRole("button", { name: NO }));
    expect(document.activeElement).toBe(screen.getByRole("textbox", { name: "Сообщение" }));
    expect(wsSend).not.toHaveBeenCalled();

    await userEvent.click(screen.getByRole("button", { name: YES }));
    expect(sentTexts()).toEqual(["Да, передайте оператору"]);
    expect(screen.queryByRole("button", { name: YES })).toBeNull();
  });

  it("кнопки клавиатурой: настоящая кнопка в фокусе нажимается по Enter", async () => {
    await answered(ASK);

    const yes = screen.getByRole("button", { name: YES });
    yes.focus();
    await userEvent.keyboard("{Enter}");

    expect(sentTexts()).toEqual(["Да, передайте оператору"]);
  });

  it("старое предложение гаснет, как только человек написал что-то ещё", async () => {
    await answered(REFUSAL);

    emit({ type: "message", message: msg("u2", "user", "Ладно, как подать заявку?") });
    emit({ type: "message", message: msg("a3", "assistant", "Откройте раздел «Заявки».", { answered: true }) });

    expect(screen.getByText(REFUSAL.content)).toBeTruthy();
    expect(screen.queryByRole("button", { name: CALL })).toBeNull();
  });

  it.each([
    ["resolved", "закрытый диалог"],
    ["waiting_operator", "диалог уже в очереди к оператору"],
  ])("восстановленный диалог (%s): %s — кнопок нет", async (status) => {
    saved.current = { sessionId: "s9", owner: "o9" };
    serve({
      state: async () => ({ session_id: "s9", status, line: 1, chips: [], audience: null }),
      history: [QUESTION, REFUSAL],
    });
    render(<ChatPage />);

    expect(await screen.findByText(REFUSAL.content)).toBeTruthy();
    expect(screen.queryByRole("button", { name: CALL })).toBeNull();
  });
});
