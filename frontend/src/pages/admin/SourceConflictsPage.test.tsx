import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { api } from "@/lib/api";
import SourceConflictsPage from "./SourceConflictsPage";
import type { ConflictGroup, SourceSnapshot } from "./source-conflicts/types";

const auth = vi.hoisted(() => ({ role: "admin" }));
vi.mock("@/lib/store", () => ({ useAuth: (selector: (state: typeof auth) => unknown) => selector(auth) }));
vi.mock("@/lib/api", async (original) => {
  const actual = await original<typeof import("@/lib/api")>();
  return { ...actual, api: { ...actual.api, get: vi.fn(), patch: vi.fn() } };
});

const source = (id: string, amount: number): SourceSnapshot => ({
  id: `snapshot-${id}`,
  article_id: id,
  source_version: `version-${id}`,
  content_hash: `hash-${id}`,
  question: "Какой максимальный размер файла?",
  answer: `Файл до ${amount} МБ.`,
  audience: "supplier",
  kind: "article",
  category: "Файлы",
  source: "import",
  doc_key: `document-${id}`,
  origin: { file: `Инструкция ${id}.pdf`, locator: "стр. 3" },
  created_at: "2026-09-12T12:00:00Z",
});

const finding: ConflictGroup = {
  id: "group-1",
  result_ids: ["result-1", "result-2"],
  question: "Какой максимальный размер файла?",
  fact: "Максимальный размер файла",
  explanation: "В статьях указаны разные лимиты.",
  sources: [
    { source: source("a", 10), quote: "до 10 МБ" },
    { source: source("b", 20), quote: "до 20 МБ" },
    { source: source("c", 30), quote: "до 30 МБ" },
  ],
  review_status: "unreviewed",
  preferred_article_id: null,
  review_note: "",
  reviewed_by: "",
  reviewed_at: null,
  created_at: "2026-09-12T12:00:00Z",
};

function mockGet(options?: { detail?: ConflictGroup; pending?: ConflictGroup[]; resolved?: ConflictGroup[] }) {
  vi.mocked(api.get).mockImplementation(async (path) => {
    if (path.includes("/groups/group-1")) return options?.detail ?? finding;
    if (path.includes("reviewed=false")) return options?.pending ?? [finding];
    if (path.includes("reviewed=true")) return options?.resolved ?? [];
    return [];
  });
}

beforeEach(() => {
  auth.role = "admin";
  mockGet();
});

afterEach(() => {
  cleanup();
  vi.resetAllMocks();
});

describe("Расхождения в статьях", () => {
  it("показывает автоматическую находку как одну компактную группу", async () => {
    render(<SourceConflictsPage />);
    expect(await screen.findByText("Максимальный размер файла")).toBeTruthy();
    expect(screen.getByText(/3 варианта/)).toBeTruthy();
    expect(screen.getByText("Ещё 1")).toBeTruthy();
    expect(screen.getByText("Новые и изменённые статьи проверяются автоматически.")).toBeTruthy();
    expect(screen.queryByText(/очеред/i)).toBeNull();
    expect(screen.queryByText(/проверить статьи вручную/i)).toBeNull();
    expect(screen.queryByText(/журнал проверок/i)).toBeNull();
    expect(api.get).toHaveBeenCalledWith(expect.stringContaining("/groups?"), expect.anything());
  });

  it("сохраняет третий вариант для всей группы", async () => {
    const user = userEvent.setup();
    vi.mocked(api.patch).mockResolvedValue({ ...finding, review_status: "confirmed", preferred_article_id: "c" });
    render(<SourceConflictsPage />);
    await user.click(await screen.findByRole("button", { name: /Максимальный размер файла/ }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getAllByRole("radio")).toHaveLength(3);
    const save = within(dialog).getByRole("button", { name: "Оставить выбранный ответ" });
    expect((save as HTMLButtonElement).disabled).toBe(true);
    await user.click(within(dialog).getByRole("radio", { name: /Инструкция c\.pdf/ }));
    await user.click(save);
    await waitFor(() => expect(api.patch).toHaveBeenCalledWith(
      "/api/source-conflicts/groups/group-1/review",
      { status: "confirmed", preferred_article_id: "c", note: "" },
    ));
    expect(await screen.findByText("Решение сохранено и будет использоваться в ответах.")).toBeTruthy();
  });

  it("позволяет отметить всю группу ложным срабатыванием", async () => {
    const user = userEvent.setup();
    vi.mocked(api.patch).mockResolvedValue({ ...finding, review_status: "dismissed" });
    render(<SourceConflictsPage />);
    await user.click(await screen.findByRole("button", { name: /Максимальный размер файла/ }));
    await user.click(await screen.findByRole("button", { name: "Это не противоречие" }));
    await waitFor(() => expect(api.patch).toHaveBeenCalledWith(
      "/api/source-conflicts/groups/group-1/review",
      { status: "dismissed", preferred_article_id: null, note: "" },
    ));
  });

  it("показывает выбранный вариант среди решённых и позволяет его изменить", async () => {
    const user = userEvent.setup();
    const resolved = { ...finding, review_status: "confirmed", preferred_article_id: "c" };
    mockGet({ detail: resolved, pending: [], resolved: [resolved] });
    render(<SourceConflictsPage />);
    await user.click(await screen.findByRole("tab", { name: /Решённые/ }));
    expect(await screen.findByText("Выбран ответ: до 30 МБ")).toBeTruthy();
    await user.click(screen.getByRole("button", { name: /Максимальный размер файла/ }));
    expect((await screen.findByRole("radio", { name: /Инструкция c\.pdf/ }) as HTMLInputElement).checked).toBe(true);
  });

  it("сотруднику оставляет просмотр всех вариантов без кнопок решения", async () => {
    auth.role = "operator";
    const user = userEvent.setup();
    render(<SourceConflictsPage />);
    await user.click(await screen.findByRole("button", { name: /Максимальный размер файла/ }));
    expect(await screen.findByText("Какой ответ оставить?")).toBeTruthy();
    expect(screen.queryByRole("radio")).toBeNull();
    expect(screen.queryByRole("button", { name: "Оставить выбранный ответ" })).toBeNull();
  });

  it("форматирует статьи тем же Markdown-компонентом, что и чат", async () => {
    const user = userEvent.setup();
    const markdown = {
      ...finding,
      sources: [{
        source: {
          ...source("md", 10),
          answer: "**Важно**\n\n1. Откройте раздел.\n2. Перейдите по [ссылке](https://example.com).",
        },
        quote: "Лимит — **10 МБ**.",
      }],
      explanation: "Различается **лимит**.",
    };
    mockGet({ detail: markdown });
    render(<SourceConflictsPage />);
    await user.click(await screen.findByRole("button", { name: /Максимальный размер файла/ }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText("10 МБ", { selector: "strong" })).toBeTruthy();
    await user.click(within(dialog).getByText("Вся статья"));
    expect(within(dialog).getByText("Важно", { selector: "strong" })).toBeTruthy();
    expect(within(dialog).getByRole("list").tagName).toBe("OL");
    expect(within(dialog).getByRole<HTMLAnchorElement>("link", { name: "ссылке" }).target).toBe("_blank");
  });
});
