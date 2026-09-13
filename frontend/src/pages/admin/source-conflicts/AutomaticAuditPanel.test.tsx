import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { api } from "@/lib/api";
import { AutomaticAuditPanel } from "./AutomaticAuditPanel";

vi.mock("@/lib/api", async (original) => {
  const actual = await original<typeof import("@/lib/api")>();
  return { ...actual, api: { ...actual.api, get: vi.fn(), post: vi.fn(), put: vi.fn() } };
});
const entry = {
  id: "queue-a", article_id: "a", question: "Как подписать контракт?", source_version: "rev-a",
  status: "retryable", attempts: 1, job_id: "job-a", last_error: "TimeoutError",
  available_at: "2026-09-12T12:00:00Z", updated_at: "2026-09-12T12:00:00Z",
  report: { evaluated_pairs: 2, candidate_pairs: 5, cached_pairs: 0, conflicts: 1, attempted_calls: 2 },
};
const overview = {
  enabled: true, configured: true, blocked_reason: "", daily_call_limit: 100, calls_used_today: 20,
  remaining_calls_today: 80, eligible_articles: 120, unchecked_articles: 100,
  estimated_backfill_calls: 500, estimated_pending_calls: 25,
  counts: { pending: 2, running: 1, done: 7, retryable: 1, skipped: 3 }, entries: [entry],
};
beforeEach(() => { vi.mocked(api.get).mockResolvedValue(overview); });
afterEach(() => { cleanup(); vi.resetAllMocks(); vi.useRealTimers(); });

describe("Автоматическая проверка базы знаний", () => {
  it("показывает очередь по названиям, прогресс и дневной лимит", async () => {
    render(<AutomaticAuditPanel canEdit onChanged={vi.fn()} />);
    expect(await screen.findByText("Как подписать контракт?")).toBeTruthy();
    expect(screen.getByText(/Использовано 20 из 100/)).toBeTruthy();
    expect(screen.getByText(/Проверено пар: 2 из 5/)).toBeTruthy();
    expect(screen.getByText(/Ещё не добавлены в очередь: 100/)).toBeTruthy();
    expect(screen.getByText(/до 500 запросов/)).toBeTruthy();
  });

  it("запускает проверку существующей базы с показанной оценкой без лишнего подтверждения", async () => {
    const user = userEvent.setup();
    vi.mocked(api.post).mockResolvedValue({ scanned: 120, eligible: 100, enqueued: 100, estimated_calls: 500 });
    const changed = vi.fn();
    render(<AutomaticAuditPanel canEdit onChanged={changed} />);
    await user.click(await screen.findByRole("button", { name: "Проверить существующую базу" }));
    await waitFor(() => expect(api.post).toHaveBeenCalledWith("/api/source-conflicts/automation/backfill", { all: true }));
    expect(await screen.findByText(/В очередь добавлено статей: 100/)).toBeTruthy();
    expect(changed).toHaveBeenCalled();
  });

  it("ошибка повтора остаётся возле очереди и не убирает статью", async () => {
    const user = userEvent.setup();
    vi.mocked(api.post).mockRejectedValue(new Error("Дневной лимит исчерпан"));
    render(<AutomaticAuditPanel canEdit onChanged={vi.fn()} />);
    await user.click(await screen.findByRole("button", { name: "Повторить: Как подписать контракт?" }));
    await waitFor(() => expect(api.post).toHaveBeenCalledWith("/api/source-conflicts/automation/queue-a/retry"));
    expect(await screen.findByText("Дневной лимит исчерпан")).toBeTruthy();
    expect(screen.getByText("Как подписать контракт?")).toBeTruthy();
  });

  it("объясняет отсутствие модели и не обещает работающую проверку", async () => {
    vi.mocked(api.get).mockResolvedValue({ ...overview, configured: false, blocked_reason: "model_not_configured" });
    render(<AutomaticAuditPanel canEdit onChanged={vi.fn()} />);
    expect(await screen.findByText(/Модель не настроена/)).toBeTruthy();
    expect((screen.getByRole("button", { name: "Проверить существующую базу" }) as HTMLButtonElement).disabled).toBe(true);
    expect(screen.getByRole("link", { name: "Настроить модель и лимиты" }).getAttribute("href")).toBe("/admin/settings");
  });

  it("показывает сбой запуска всей базы без ложного сообщения об успехе", async () => {
    const user = userEvent.setup();
    vi.mocked(api.post).mockRejectedValue(new Error("Не удалось добавить статьи в очередь"));
    render(<AutomaticAuditPanel canEdit onChanged={vi.fn()} />);
    await user.click(await screen.findByRole("button", { name: "Проверить существующую базу" }));
    expect(await screen.findByRole("alert")).toHaveProperty("textContent", "Не удалось добавить статьи в очередь");
    expect(screen.queryByText(/В очередь добавлено статей:/)).toBeNull();
  });

  it("повтор ставит только выбранную статью и обновляет состояние", async () => {
    const user = userEvent.setup();
    vi.mocked(api.post).mockResolvedValue({ ...entry, status: "pending" });
    render(<AutomaticAuditPanel canEdit onChanged={vi.fn()} />);
    await user.click(await screen.findByRole("button", { name: "Повторить: Как подписать контракт?" }));
    expect(await screen.findByText(/Повторная проверка статьи .* поставлена в очередь/)).toBeTruthy();
    expect(api.post).toHaveBeenCalledExactlyOnceWith("/api/source-conflicts/automation/queue-a/retry");
    await waitFor(() => expect(vi.mocked(api.get).mock.calls.length).toBeGreaterThan(1));
  });

  it("включает автоматический режим существующей настройкой", async () => {
    const user = userEvent.setup();
    vi.mocked(api.get).mockResolvedValue({ ...overview, enabled: false });
    vi.mocked(api.put).mockResolvedValue({ changed: ["source_conflicts.auto_enabled"] });
    render(<AutomaticAuditPanel canEdit onChanged={vi.fn()} />);
    await user.click(await screen.findByRole("button", { name: "Включить автоматическую проверку" }));
    await waitFor(() => expect(api.put).toHaveBeenCalledWith("/api/admin/settings", {
      updates: { "source_conflicts.auto_enabled": true },
    }));
  });

  it("при исчерпанном дневном бюджете оставляет очередь и запрещает ручной повтор", async () => {
    vi.mocked(api.get).mockResolvedValue({ ...overview, remaining_calls_today: 0, calls_used_today: 100, blocked_reason: "daily_budget" });
    render(<AutomaticAuditPanel canEdit onChanged={vi.fn()} />);
    expect(await screen.findByText(/Дневной лимит использован/)).toBeTruthy();
    expect((screen.getByRole("button", { name: "Повторить: Как подписать контракт?" }) as HTMLButtonElement).disabled).toBe(true);
  });

  it("объясняет паузу провайдера, запрещает повтор и продолжает обновлять очередь", async () => {
    vi.useFakeTimers();
    vi.mocked(api.get).mockResolvedValue({ ...overview, blocked_reason: "provider_unavailable" });
    render(<AutomaticAuditPanel canEdit onChanged={vi.fn()} />);
    await act(async () => { await vi.advanceTimersByTimeAsync(0); });
    expect(screen.getByText(/Модель временно недоступна/)).toBeTruthy();
    expect(screen.getByText(/После паузы проверка продолжится автоматически/)).toBeTruthy();
    expect((screen.getByRole("button", { name: "Повторить: Как подписать контракт?" }) as HTMLButtonElement).disabled).toBe(true);
    expect((screen.getByRole("button", { name: "Проверить существующую базу" }) as HTMLButtonElement).disabled).toBe(false);
    const calls = vi.mocked(api.get).mock.calls.length;
    await act(async () => { await vi.advanceTimersByTimeAsync(2600); });
    expect(vi.mocked(api.get).mock.calls.length).toBeGreaterThan(calls);
  });

  it("обновляет активную очередь независимо от наличия ручных заданий", async () => {
    vi.useFakeTimers();
    const changed = vi.fn();
    render(<AutomaticAuditPanel canEdit onChanged={changed} />);
    await act(async () => { await vi.advanceTimersByTimeAsync(0); });
    const initialRequests = vi.mocked(api.get).mock.calls.length;
    await act(async () => { await vi.advanceTimersByTimeAsync(2600); });
    expect(vi.mocked(api.get).mock.calls.length).toBeGreaterThan(initialRequests);
    expect(changed.mock.calls.length).toBeGreaterThan(1);
  });

  it("не показывает управляющие действия сотруднику без прав администратора", async () => {
    render(<AutomaticAuditPanel canEdit={false} onChanged={vi.fn()} />);
    expect(await screen.findByText("Как подписать контракт?")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Проверить существующую базу" })).toBeNull();
    expect(screen.queryByRole("button", { name: /Повторить:/ })).toBeNull();
  });
});
