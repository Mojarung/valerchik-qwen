import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ChatMessage, OperatorSession } from "@/lib/types";

/**
 * Страница оператора обязана читать переписку через операторский эндпоинт.
 * Гостевой требует токен владельца, которого у сотрудника нет: на нём колонка
 * чата молча оставалась пустой, и оператор отвечал, не видя вопроса.
 */
const { get, post } = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn() }));

// Остальное из модуля настоящее: текст ошибки и таймауты страница берёт оттуда же.
vi.mock("@/lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api")>()),
  api: { get, post, upload: vi.fn(async () => ({})) },
  wsUrl: (path: string) => path,
}));
vi.mock("@/lib/ws", () => ({ connectWs: () => ({ send: () => {}, close: () => {} }) }));
vi.mock("@/lib/store", () => ({
  useAuth: (select: (s: { displayName: string }) => unknown) => select({ displayName: "Мария" }),
  useMeta: (select: (s: object) => unknown) =>
    select({ lines: { "2": { label: "Техническая поддержка" } }, operatorQueue: { warn_seconds: 180, alert_seconds: 600 } }),
}));

import OperatorPage from "./OperatorPage";

const SID = "a".repeat(32);
const REASON = "Хочу поговорить с оператором";
const QUESTION = "Как подписать контракт электронной подписью?";
const HINT = "Как подписать контракт на портале?";

const session = {
  session_id: SID,
  status: "waiting_operator",
  available_actions: ["take"],
  line: 1,
  operator_id: null,
  reason: REASON,
  created_at: "2026-09-11T01:05:00+00:00",
  escalated_at: "2026-09-11T01:05:00+00:00",
  waiting_seconds: 30,
  reclaimed_count: 0,
  last_message: "",
  last_role: "user",
} as OperatorSession;

const history: ChatMessage[] = [
  { id: "m1", role: "user", content: QUESTION, citations: [], meta: {}, created_at: "2026-09-11T01:04:00+00:00" },
  {
    id: "m2", role: "assistant", content: "Откройте карточку контракта и нажмите «Подписать».",
    citations: [], meta: {}, created_at: "2026-09-11T01:04:05+00:00",
  },
];

const hint = { article_id: "k1", question: HINT, answer: "Раздел «Контракты».", score: 0.8, category: "Контракты" };

/** Ответы сервера по адресу; всё незнакомое — отказ, как у гостевого эндпоинта без токена. */
function serve(overrides: Record<string, () => Promise<unknown>> = {}) {
  get.mockImplementation((path: string) => {
    const override = overrides[path];
    if (override) return override();
    if (path.startsWith("/api/operator/sessions?")) return Promise.resolve([session]);
    if (path === "/api/operator/quick-replies") return Promise.resolve([]);
    if (path === `/api/operator/sessions/${SID}/messages`) return Promise.resolve(history);
    if (path === `/api/operator/sessions/${SID}/suggestions`) return Promise.resolve([hint]);
    return Promise.reject(new Error(`Этот диалог принадлежит другому посетителю: ${path}`));
  });
}

async function openTicket() {
  render(<OperatorPage />);
  await userEvent.click(await screen.findByText(REASON));
}

beforeEach(() => {
  // jsdom не умеет прокручивать, а лента прокручивается к последнему сообщению
  Element.prototype.scrollIntoView = vi.fn();
  post.mockImplementation(async () => ({}));
});

afterEach(() => {
  cleanup();
  get.mockReset();
  post.mockReset();
});

describe("OperatorPage", () => {
  it("показывает, за какую линию отвечает оператор", async () => {
    serve({ "/api/auth/me": () => Promise.resolve({ role: "operator", line: 2 }) });
    render(<OperatorPage />);

    expect(await screen.findByText("Линия 2 · Техническая поддержка")).toBeTruthy();
  });

  it("показывает переписку открытого обращения", async () => {
    serve();
    await openTicket();

    expect(await screen.findByText(QUESTION)).toBeTruthy();
    expect(get).toHaveBeenCalledWith(`/api/operator/sessions/${SID}/messages`);
    expect(get).not.toHaveBeenCalledWith(`/api/chat/sessions/${SID}/messages`);
  });

  it("сразу подсказывает статьи к последнему вопросу клиента", async () => {
    serve();
    await openTicket();

    await userEvent.click(screen.getByRole("button", { name: /База знаний/ }));
    expect(await screen.findByText(HINT)).toBeTruthy();
  });

  it("говорит об ошибке загрузки и даёт повторить", async () => {
    serve({ [`/api/operator/sessions/${SID}/messages`]: () => Promise.reject(new Error("Сервис недоступен")) });
    await openTicket();

    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("Не удалось загрузить переписку");
    expect(screen.getByRole("button", { name: "Повторить" })).toBeTruthy();
  });

  it("очередь не загрузилась — говорит об этом и даёт повторить", async () => {
    let fail = true;
    serve({
      "/api/operator/sessions?status=queue": () =>
        fail ? Promise.reject(new Error("Сервер не ответил за 30 с")) : Promise.resolve([session]),
    });
    render(<OperatorPage />);

    expect((await screen.findByRole("alert")).textContent).toContain("Очередь не загрузилась: Сервер не ответил за 30 с");
    fail = false;
    await userEvent.click(screen.getByRole("button", { name: "Повторить" }));

    expect(await screen.findByText(REASON)).toBeTruthy();
  });

  it("сбой «Взять в работу» виден сразу и не переезжает в другое обращение", async () => {
    const TAKEN = "Обращение уже взял другой оператор";
    const other = { ...session, session_id: "b".repeat(32), reason: "Не приходит письмо с кодом" };
    serve({
      "/api/operator/sessions?status=queue": () => Promise.resolve([session, other]),
      [`/api/operator/sessions/${other.session_id}/messages`]: () => Promise.resolve([]),
      [`/api/operator/sessions/${other.session_id}/suggestions`]: () => Promise.resolve([]),
    });
    post.mockImplementation(async (path: string) => {
      if (path.endsWith("/take")) throw new Error(TAKEN);
      return {};
    });
    await openTicket();

    await userEvent.click(screen.getByRole("button", { name: "Взять в работу" }));
    expect(await screen.findByText(TAKEN)).toBeTruthy();

    await userEvent.click(screen.getByText(other.reason));
    await waitFor(() => expect(screen.queryByText(TAKEN)).toBeNull());
  });

  it("не показывает оператору редакторские действия", async () => {
    serve({ "/api/operator/sessions?status=queue": () => Promise.resolve([{ ...session, status: "operator_active", available_actions: ["message", "transfer", "wait", "resolve"] }]) });
    await openTicket();
    await screen.findByText(QUESTION);

    expect(screen.queryByRole("button", { name: "Создать статью из диалога" })).toBeNull();
    expect(screen.queryByText("Добавить в базу знаний (черновик)")).toBeNull();
  });
});
