import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ChatMessage } from "@/lib/types";
import { Bubble } from "./Bubble";

vi.mock("@/components/SourceModal", () => ({
  SourceModal: ({ citation, evidenceMode }: { citation: { question: string }; evidenceMode?: boolean }) => <div role="dialog" aria-label="Источник" data-evidence-mode={evidenceMode}>{citation.question}</div>,
}));

afterEach(cleanup);

const message: ChatMessage = {
  id: "conflict-warning", role: "assistant", content: "В источниках найдены разные ответы. Требуется проверка.",
  citations: [
    { article_id: "a", question: "Первый источник", score: 0, category: "Файлы", images: [] },
    { article_id: "b", question: "Второй источник", score: 0, category: "Файлы", images: [] },
  ],
  meta: { retrieval_outcome: "source_conflict", confidence: 0, offer_operator: true },
  created_at: "2026-09-12T12:00:00Z",
};

describe("Источники спорного ответа", () => {
  it("показывает расхождение вместо нулевой уверенности и оставляет источники и оператора", async () => {
    const user = userEvent.setup();
    const onOperator = vi.fn();
    render(<Bubble msg={message} onOperator={onOperator} />);
    expect(screen.getByText("Источники расходятся — требуется проверка")).toBeTruthy();
    expect(screen.queryByText(/Ответ из базы знаний, уверенность/)).toBeNull();
    expect(screen.queryAllByText(/\d+%/)).toHaveLength(0);
    await user.click(screen.getByRole("button", { name: "Источники · 2" }));
    await user.click(screen.getByRole("button", { name: "Первый источник" }));
    expect(screen.getByRole("dialog", { name: "Источник" }).getAttribute("data-evidence-mode")).toBe("true");
    expect(screen.getByRole("button", { name: "Второй источник" })).toBeTruthy();
    await user.click(screen.getByRole("button", { name: /Позвать оператора:/ }));
    expect(onOperator).toHaveBeenCalledWith("Позовите оператора");
  });

  it("сохраняет проценты для обычного ответа", async () => {
    const user = userEvent.setup();
    render(<Bubble msg={{ ...message, meta: { confidence: 0.8 }, citations: [{ ...message.citations[0]!, score: 0.7 }] }} />);
    expect(screen.getByText("80%")).toBeTruthy();
    await user.click(screen.getByRole("button", { name: "Источники · 1" }));
    expect(screen.getByText("70%")).toBeTruthy();
    expect(screen.queryByText("Источники расходятся — требуется проверка")).toBeNull();
  });
});
