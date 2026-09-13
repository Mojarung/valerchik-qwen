import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { api } from "@/lib/api";
import type { ChatMessage } from "@/lib/types";
import { AnswerRating } from "./AnswerRating";

vi.mock("@/lib/api", () => ({
  api: { post: vi.fn() },
  errorText: (error: Error) => error.message,
}));

const msg: ChatMessage = {
  id: "answer", role: "assistant", content: "Откройте кабинет", citations: [], meta: {},
  created_at: "2026-09-12T00:00:00Z",
};
const saved = { status: "ok", value: -1, reason: "", comment: "" };

beforeEach(() => vi.clearAllMocks());
afterEach(cleanup);

describe("Оценка отдельного ответа", () => {
  it("сохраняет причину и комментарий после дизлайка", async () => {
    vi.mocked(api.post).mockResolvedValue(saved);
    const user = userEvent.setup();
    render(<AnswerRating msg={msg} sessionId="session" canRecover />);
    await user.click(screen.getByRole("button", { name: "Ответ не помог" }));
    await screen.findByRole("group", { name: "Причина оценки" });
    await user.click(screen.getByRole("button", { name: "Ошибка / устарело" }));
    await user.type(screen.getByRole("textbox", { name: "Комментарий к ответу" }), "Нет кнопки");
    await user.click(screen.getByRole("button", { name: "Сохранить уточнение" }));
    expect(api.post).toHaveBeenLastCalledWith("/api/chat/sessions/session/messages/answer/rating", {
      value: -1, reason: "incorrect", comment: "Нет кнопки",
    });
  });

  it("показывает сбой сохранения и позволяет повторить", async () => {
    vi.mocked(api.post).mockRejectedValueOnce(new Error("Нет связи"));
    const user = userEvent.setup();
    render(<AnswerRating msg={msg} sessionId="session" canRecover />);
    await user.click(screen.getByRole("button", { name: "Ответ не помог" }));
    expect((await screen.findByRole("alert")).textContent).toContain("Нет связи");
    expect(screen.getByRole("button", { name: "Ответ не помог" }).getAttribute("aria-pressed")).toBe("false");
    vi.mocked(api.post).mockResolvedValueOnce(saved);
    await user.click(screen.getByRole("button", { name: "Ответ не помог" }));
    await screen.findByRole("group", { name: "Причина оценки" });
  });

  it("продолжение запускается отдельным действием и учитывает причину", async () => {
    vi.mocked(api.post).mockResolvedValue(saved);
    const user = userEvent.setup();
    render(<AnswerRating msg={msg} sessionId="session" canRecover />);
    await user.click(screen.getByRole("button", { name: "Ответ не помог" }));
    await user.click(await screen.findByRole("button", { name: "Непонятно" }));
    expect(api.post).toHaveBeenCalledTimes(1);
    await user.click(screen.getByRole("button", { name: "Объяснить по шагам" }));
    await waitFor(() => expect(api.post).toHaveBeenCalledTimes(3));
    expect(api.post).toHaveBeenNthCalledWith(2, "/api/chat/sessions/session/messages/answer/rating", {
      value: -1, reason: "unclear", comment: "",
    });
    expect(api.post).toHaveBeenLastCalledWith("/api/chat/sessions/session/messages/answer/recovery", {
      action: "explain", detail: "",
    }, { timeoutMs: 150_000 });
  });

  it("оценку после перезагрузки можно снять, закрытый диалог не продолжает", async () => {
    vi.mocked(api.post).mockResolvedValue({ status: "ok", value: 0, reason: "", comment: "" });
    const user = userEvent.setup();
    render(<AnswerRating msg={{ ...msg, meta: { rating: -1, rating_reason: "unclear" } }} sessionId="session" canRecover={false} />);
    await user.click(screen.getByRole("button", { name: "Уточнить причину" }));
    expect(screen.queryByRole("button", { name: "Объяснить по шагам" })).toBeNull();
    await user.click(screen.getByRole("button", { name: "Ответ не помог" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "Ответ не помог" }).getAttribute("aria-pressed")).toBe("false"));
  });

  it("сообщает о переводе оператору, если повторная генерация не нашла ответа", async () => {
    vi.mocked(api.post).mockResolvedValueOnce(saved).mockResolvedValueOnce(saved)
      .mockResolvedValueOnce({ status: "no_answer", escalated: true });
    const user = userEvent.setup();
    render(<AnswerRating msg={msg} sessionId="session" canRecover />);
    await user.click(screen.getByRole("button", { name: "Ответ не помог" }));
    await user.click(await screen.findByRole("button", { name: "Объяснить по шагам" }));
    expect((await screen.findByRole("status")).textContent).toContain("Диалог передан оператору");
  });
});
