import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { api } from "@/lib/api";
import { AnswerFeedbackQueue } from "./AnswerFeedbackQueue";

vi.mock("@/lib/store", () => ({ useAuth: (selector: (s: { role: string }) => unknown) => selector({ role: "admin" }) }));
vi.mock("@/lib/api", async (original) => {
  const actual = await original<typeof import("@/lib/api")>();
  return { ...actual, api: { ...actual.api, get: vi.fn(), post: vi.fn(), patch: vi.fn() } };
});

const row = {
  message_id: "answer-1", session_id: "session-1", value: -1, reason: "incorrect", comment: "Нет кнопки",
  snapshot: { question: "Как подписать контракт?", answer: "Старый ответ", audience: "supplier",
    sources: [{ id: "source-1", question: "Источник", answer: "Снимок источника", updated_at: "2026-09-12T00:00:00Z" }] },
  status: "open", resolution_note: "", article_id: null, recovery: {},
  created_at: "2026-09-12T00:00:00Z", updated_at: "2026-09-12T00:00:00Z",
};
const article = { id: "source-1", question: "Источник", answer: "Текущий текст", category: "Контракты",
  status: "active", audience: "supplier", line: 1, keywords: [], images: [], updated_at: "2026-09-12T01:00:00Z" };

beforeEach(() => {
  vi.mocked(api.get).mockImplementation(async (path) => path.includes("/articles/") ? article
    : { items: [row], total: 1, positive: 3, negative: 1, open_count: 1 });
});
afterEach(() => { cleanup(); vi.resetAllMocks(); });

async function openReview(review = row) {
  vi.mocked(api.get).mockImplementation(async (path) => path.includes("/articles/") ? article
    : { items: [review], total: 1, positive: 3, negative: 1, open_count: 1 });
  const user = userEvent.setup();
  render(<AnswerFeedbackQueue />);
  await user.click(await screen.findByRole("button", { name: new RegExp(review.snapshot.question) }));
  return user;
}

describe("Очередь замечаний", () => {
  it("сначала показывает только три замечания", async () => {
    const user = userEvent.setup();
    const rows = Array.from({ length: 7 }, (_, index) => ({
      ...row, message_id: `answer-${index}`, snapshot: { ...row.snapshot, question: `Вопрос ${index + 1}` },
    }));
    vi.mocked(api.get).mockResolvedValue({ items: rows, total: 7, positive: 3, negative: 1, open_count: 7 });
    render(<AnswerFeedbackQueue />);

    expect(await screen.findByRole("button", { name: /Вопрос 3/ })).toBeTruthy();
    expect(screen.queryByRole("button", { name: /Вопрос 4/ })).toBeNull();
    await user.click(screen.getByRole("button", { name: "Показать ещё 4" }));
    expect(screen.getByRole("button", { name: /Вопрос 7/ })).toBeTruthy();
  });

  it("показывает исходный ответ и передаёт текущую версию статьи для правки", async () => {
    const user = await openReview();
    expect(screen.getByText(/75% · оценок: 4/)).toBeTruthy();
    expect(screen.getByText("Старый ответ")).toBeTruthy();
    await user.click(screen.getByText("Источник"));
    await user.type(screen.getByLabelText("Что исправить / результат проверки"), "Уточняю шаг подписи");
    await user.click(screen.getByRole("button", { name: "Исправить эту статью" }));
    const answer = await screen.findByRole("textbox", { name: "Ответ" });
    expect((answer as HTMLTextAreaElement).value).toBe("Текущий текст");
    await user.clear(answer);
    await user.type(answer, "Исправленный шаг подписи");
    vi.mocked(api.post).mockResolvedValue({ article_id: "source-1", status: "fixed" });
    await user.click(screen.getByRole("button", { name: /Сохранить —/ }));
    await waitFor(() => expect(api.post).toHaveBeenCalledWith("/api/admin/answer-feedback/answer-1/resolve", {
      article_id: "source-1", expected_updated_at: article.updated_at, note: "Уточняю шаг подписи",
      article: { question: article.question, answer: "Исправленный шаг подписи", category: article.category,
        status: "active", audience: "supplier", line: 1, keywords: [], images: [] },
    }, { timeoutMs: 90_000 }));
  });

  it("не теряет правку при конфликте и запрещает отклонение без пояснения", async () => {
    const user = await openReview();
    expect((screen.getByRole("button", { name: "Отклонить с пояснением" }) as HTMLButtonElement).disabled).toBe(true);
    await user.type(screen.getByLabelText("Что исправить / результат проверки"), "Проверка конфликта");
    await user.click(screen.getByRole("button", { name: "Создать статью" }));
    const answer = await screen.findByRole("textbox", { name: "Ответ" });
    await user.type(answer, "Сохраняемый текст");
    vi.mocked(api.post).mockRejectedValue(new Error("Замечание уже изменилось"));
    await user.click(screen.getByRole("button", { name: /Сохранить —/ }));
    expect((await screen.findByRole("alert")).textContent).toContain("Замечание уже изменилось");
    expect((answer as HTMLTextAreaElement).value).toBe("Сохраняемый текст");
    expect(screen.getByRole("dialog", { name: "Новая статья" })).toBeTruthy();
  });

  it("сворачивает источники и сохраняет контекст замечания", async () => {
    const user = await openReview();
    const dialog = screen.getByRole("dialog", { name: "Разбор ответа" });
    const details = screen.getByText("Источник").closest("details") as HTMLDetailsElement;
    expect(details.open).toBe(false);
    expect(within(dialog).getByText("Как подписать контракт?")).toBeTruthy();
    expect(within(dialog).getByText("Старый ответ")).toBeTruthy();
    expect(within(dialog).getByText("Нет кнопки")).toBeTruthy();
    expect(within(dialog).getByText("Поставщик")).toBeTruthy();
    await user.click(screen.getByText("Источник"));
    expect(details.open).toBe(true);
    expect(screen.getByText("Снимок источника")).toBeTruthy();
  });

  it("показывает понятное состояние пустого источника", async () => {
    const emptySource = {
      ...row,
      snapshot: { ...row.snapshot, sources: [{ ...row.snapshot.sources[0]!, answer: "1." }] },
    };
    const user = await openReview(emptySource);
    await user.click(screen.getByText("Источник"));
    expect(screen.getByText("Текст источника отсутствует.")).toBeTruthy();
  });

  it.each([
    ["open", ["Взять в работу", "Создать статью", "Отклонить с пояснением"]],
    ["in_progress", ["Создать статью", "Отклонить с пояснением", "Вернуть в работу"]],
    ["fixed", ["Подтвердить проверку", "Вернуть в работу"]],
    ["verified", ["Вернуть в работу"]],
    ["dismissed", ["Вернуть в работу"]],
  ])("показывает корректные действия для статуса %s", async (status, shown) => {
    await openReview({ ...row, status });
    for (const label of shown) expect(screen.getByRole("button", { name: label })).toBeTruthy();
  });
});
