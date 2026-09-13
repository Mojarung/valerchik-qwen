import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import type { Citation } from "@/lib/types";
import { SourceModal } from "./SourceModal";

vi.mock("@/lib/useApiData", () => ({ useApiData: () => ({
  data: { question: "Размер файла", answer: "Файл не более 10 МБ.", category: "Файлы", images: [], updated_at: "2026-09-12T12:00:00Z" },
  error: null, reload: vi.fn(),
}) }));
afterEach(cleanup);
const citation: Citation = { article_id: "a", question: "Размер файла", score: 0, category: "Файлы", images: [] };

describe("Источник при расхождении", () => {
  it("показывает полный текст и подсветку без фиктивной релевантности", () => {
    render(<SourceModal citation={citation} answerText="Файл не более 10 МБ." evidenceMode onClose={vi.fn()} />);
    expect(screen.queryByText(/релевантность/)).toBeNull();
    expect(screen.queryByText(/0%/)).toBeNull();
    expect(screen.getByText("Файл не более 10 МБ.").tagName).toBe("MARK");
    expect(screen.getByRole("button", { name: "Закрыть" })).toBeTruthy();
  });
  it("сохраняет релевантность обычного источника", () => {
    render(<SourceModal citation={{ ...citation, score: 0.7 }} answerText="Файл не более 10 МБ." onClose={vi.fn()} />);
    expect(screen.getByText("релевантность 70%")).toBeTruthy();
  });
});
