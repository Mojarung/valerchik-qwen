import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

const { get, post } = vi.hoisted(() => {
  window.matchMedia = ((query: string) => ({
    matches: false, media: query, onchange: null,
    addEventListener: () => undefined, removeEventListener: () => undefined,
    addListener: () => undefined, removeListener: () => undefined, dispatchEvent: () => false,
  })) as unknown as typeof window.matchMedia;
  return { get: vi.fn(), post: vi.fn() };
});

vi.mock("@/lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api")>()),
  api: { get, post },
}));

import FeedbackPage from "./FeedbackPage";

const SPECIALIST = {
  id: "op_sergey", name: "Сергей Ефимов", kind: "operator", count: 7, avg_rating: 1.9, negative_share: 1,
  avg_sentiment: null, lines: { "2": 7 }, sample_negative: null, cases: 7, smoothed_rating: 3.3, csat: 0,
  solved_rate: 0.33, solved_total: 6, clear_rate: 0.17, clear_total: 6, median_first_reply_s: 960,
  median_duration_s: 2400, abandoned: 2, auto_resolved_rate: null, refusal_rate: null, thumbs_up: 0,
  thumbs_down: 0, thumbs_unclear: 0, thumbs_up_share: null, repeat_rate: null, repeat_checked: 0,
  components: { solved: 0.5, clear: 0.2, csat: 0.3, speed: 0.13 }, index: 33, result_index: 39,
  clarity_index: 20, low_data: false,
  weak_spots: [{ dimension: "clear", bad: 5, total: 6, text: "объясняет непонятно: 5 из 6 отзывов", advice: "" }],
};

const SUPPORT_EXPORT = {
  generated_at: "2026-09-12T00:00:00+00:00", source: "x.xlsx", total: 24960,
  answer_kinds: { instruction: 11712, status: 5220, short: 6207, redirect: 974, ask: 362, mass: 485 },
  explained_share: 0.47, brush_off_share: 0.51, mass_share: 0.02, incident_share: 0.01, median_answer_chars: 178,
  topics: [{ topic: "Работа с контрактами", count: 5503, share: 0.22, explained_share: 0.38, brush_off_share: 0.59,
    redirect_share: 0, ask_share: 0, status_share: 0, short_share: 0, mass_share: 0, incident_share: 0,
    median_answer_chars: 139, top_subtopics: [] }],
  problems: [], mass_incidents: [], mass_task_count: 19,
  conclusion: "Длинное заключение по выгрузке, которого не должно быть на главном экране.",
  notes: ["В выгрузке нет оценок."],
};

const REPORT = {
  exists: true,
  created_at: "2026-09-12T09:00:00+00:00",
  summary: {
    count: 55, avg_rating: 4.2, positive_share: 0.78, negative_count: 12, ai_count: 40, operator_count: 15,
    solved_rate: 0.81, solved_total: 47, clear_rate: 0.86, clear_total: 47,
  },
  specialists: [SPECIALIST], clusters: [],
  problems: [
    { kind: "topic", title: "Проблема 1", detail: "не решено", advice: "дописать статью", target: "тема" },
    { kind: "article", title: "Проблема 2", detail: "непонятно", advice: "упростить", target: "статья" },
    { kind: "specialist", title: "Проблема 3", detail: "просадка", advice: "разобрать", target: "специалист" },
    { kind: "trend", title: "Проблема 4", detail: "рост жалоб", advice: "проверить", target: "динамика" },
  ],
  methodology: { weights: { solved: 40, clear: 30, csat: 20, speed: 10 }, prior_weight: 5,
    baseline: { solved: 0.74, clear: 0.7, csat: 0.72 }, min_reviews: 5, reply_target_seconds: 120,
    repeat_window_hours: 48 },
  conclusion: "Длинный автоматический вывод, который дублирует проблемы.",
  support_export: SUPPORT_EXPORT,
};

function serve(report: object) {
  get.mockImplementation((path: string) => {
    if (path === "/api/admin/feedback/report") return Promise.resolve(report);
    if (path.startsWith("/api/admin/answer-feedback")) {
      return Promise.resolve({ positive: 3, negative: 1, open_count: 0, total: 0, items: [] });
    }
    return Promise.reject(new Error(`Unexpected request: ${path}`));
  });
}

afterEach(() => { cleanup(); vi.clearAllMocks(); });

describe("FeedbackPage", () => {
  it("оставляет на экране показатели, три главные проблемы и короткую таблицу команды", async () => {
    serve(REPORT);
    render(<FeedbackPage />);

    expect(await screen.findByText("Состояние и задачи")).toBeTruthy();
    expect(screen.getByText("78%")).toBeTruthy();
    expect(screen.getByText("Проблема 1")).toBeTruthy();
    expect(screen.getByText("Проблема 3")).toBeTruthy();
    expect(screen.queryByText("Проблема 4")).toBeNull();
    expect(screen.getByText("Показаны 3 главных из 4.")).toBeTruthy();
    expect(screen.getByText("объясняет непонятно: 5 из 6 отзывов")).toBeTruthy();
    expect(screen.getByText("33")).toBeTruthy();
  });

  it("не показывает сырьё, методику и длинные автоматические выводы", async () => {
    serve(REPORT);
    render(<FeedbackPage />);
    await screen.findByText("Состояние и задачи");

    expect(screen.queryByText("Как мы оцениваем")).toBeNull();
    expect(screen.queryByText("Последние отзывы")).toBeNull();
    expect(screen.queryByText(REPORT.conclusion)).toBeNull();
    expect(screen.queryByText(SUPPORT_EXPORT.conclusion)).toBeNull();
    expect(get).not.toHaveBeenCalledWith("/api/admin/feedback");
  });

  it("сводит большую выгрузку СТП к одной строке источников", async () => {
    serve(REPORT);
    render(<FeedbackPage />);
    const source = await screen.findByText((content, element) =>
      element?.tagName === "P" && content.includes("Основа отчёта:"));
    expect(source.textContent).toMatch(/24\s960 обращений СТП · без объяснения 51%/);
    expect(screen.queryByText("Работа с контрактами")).toBeNull();
  });

  it("обновляет только агрегированный отчёт", async () => {
    const user = userEvent.setup();
    serve(REPORT);
    post.mockResolvedValue({});
    render(<FeedbackPage />);

    await user.click(await screen.findByRole("button", { name: "Обновить данные" }));
    await waitFor(() => expect(post).toHaveBeenCalledWith("/api/admin/feedback/report"));
    expect(get).not.toHaveBeenCalledWith("/api/admin/feedback");
  });

  it("объясняет пустое состояние коротко", async () => {
    serve({ exists: false, support_export: SUPPORT_EXPORT });
    render(<FeedbackPage />);
    expect(await screen.findByText(/После первых оценок здесь появятся показатели и задачи/)).toBeTruthy();
    expect(screen.getByRole("button", { name: "Сформировать отчёт" })).toBeTruthy();
  });
});
