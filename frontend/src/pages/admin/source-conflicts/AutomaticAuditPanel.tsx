import { useEffect, useRef, useState } from "react";
import { RefreshCw } from "lucide-react";
import { LoadError } from "@/components/LoadError";
import { api, errorText } from "@/lib/api";
import type { Api } from "@/lib/types";
import { useApiData } from "@/lib/useApiData";

type Automation = Api["AutoAuditStatus"];
type QueueEntry = Api["AutoAuditEntry"];
const PAGE_SIZE = 20;
const STATUS_LABEL: Record<string, string> = {
  pending: "В очереди", running: "Проверяется", retryable: "Нужен повтор", blocked: "Ожидает запуска",
  done: "Проверено", skipped: "Пропущено", superseded: "Есть новая версия",
};

function queueActive(data: Automation | null): boolean {
  const counts = data?.counts ?? {};
  return (counts.pending ?? 0) + (counts.running ?? 0) + (counts.retryable ?? 0) + (counts.blocked ?? 0) > 0;
}

function errorHint(entry: QueueEntry): string {
  if (!entry.last_error) return "";
  if (entry.last_error === "no_candidates") return "Подходящих материалов для сравнения не найдено.";
  if (entry.last_error === "source_changed") return "Статья изменилась: проверка относится к предыдущей версии.";
  if (entry.last_error === "attempts_exhausted") return "Автоматические попытки исчерпаны. Проверьте доступность модели перед ручным повтором.";
  if (entry.last_error === "worker_stopped") return "Проверка была прервана и ожидает повторного запуска.";
  if (/timeout/i.test(entry.last_error)) return "Время ожидания истекло. Сохранённые сравнения не потеряны.";
  if (/budget|daily|limit/i.test(entry.last_error)) return "Проверка ожидает доступного лимита запросов.";
  if (/config|model_missing/i.test(entry.last_error)) return "Для проверки нужно настроить модель.";
  if (entry.status === "skipped") return "Статья не подходит для этой проверки или превышает ограничение размера.";
  if (entry.status === "superseded") return "После изменения статьи проверяется её новая версия.";
  return "Проверку не удалось завершить. Проверьте доступность модели перед повтором.";
}

function EntryRow({ entry, retryDisabled, onRetry }: {
  entry: QueueEntry; retryDisabled: boolean; onRetry?: (entry: QueueEntry) => void;
}) {
  const done = (entry.report?.evaluated_pairs ?? 0) + (entry.report?.cached_pairs ?? 0);
  const total = entry.report?.candidate_pairs ?? 0;
  const hint = errorHint(entry);
  const mayRetry = entry.status === "retryable" || entry.status === "blocked";
  return (
    <article className="border-b border-(--color-line) p-3 text-xs last:border-0">
      <div className="flex flex-wrap items-start gap-2">
        <h3 className="min-w-0 flex-1 font-medium">{entry.question || "Статья недоступна"}</h3>
        <span className="rounded-full bg-(--color-surface-2) px-2 py-0.5">{STATUS_LABEL[entry.status] ?? "Ожидает проверки"}</span>
      </div>
      <p className="mt-1 text-(--color-ink-soft)">Попыток: {entry.attempts} · {new Date(entry.updated_at).toLocaleString("ru")}</p>
      {entry.report && <p className="mt-1">Проверено пар: {done} из {total} · расхождений: {entry.report.conflicts ?? 0}</p>}
      {entry.status === "running" && <progress className="mt-2 w-full accent-(--color-brand)" aria-label={`Проверка: ${entry.question}`} value={done} max={Math.max(1, total)} />}
      {hint && <p className="mt-2 text-(--color-ink-soft)">{hint}</p>}
      {onRetry && mayRetry && <button type="button" className="btn btn-ghost mt-2 text-xs" disabled={retryDisabled}
        aria-label={`Повторить: ${entry.question}`} onClick={() => onRetry(entry)}>Повторить проверку</button>}
    </article>
  );
}

export function AutomaticAuditPanel({ canEdit, onChanged, compact = false }: { canEdit: boolean; onChanged: () => void; compact?: boolean }) {
  const [page, setPage] = useState(0);
  const [busy, setBusy] = useState("");
  const [actionError, setActionError] = useState("");
  const [message, setMessage] = useState("");
  const state = useApiData<Automation>(`/api/source-conflicts/automation?limit=${PAGE_SIZE}&offset=${page * PAGE_SIZE}`, {
    pollMs: 2500, pollWhile: queueActive,
  });
  const onChangedRef = useRef(onChanged);
  useEffect(() => { onChangedRef.current = onChanged; });
  // Automatic work may exist before any manual job. Its own progress refreshes findings.
  useEffect(() => { if (state.loadedAt !== null) onChangedRef.current(); }, [state.loadedAt]);
  const data = state.data;
  const run = async (name: string, action: () => Promise<string>) => {
    if (busy) return;
    setBusy(name); setActionError(""); setMessage("");
    try { setMessage(await action()); state.reload(); onChangedRef.current(); }
    catch (e) { setActionError(errorText(e)); }
    finally { setBusy(""); }
  };
  const toggle = () => void run("toggle", async () => {
    await api.put("/api/admin/settings", { updates: { "source_conflicts.auto_enabled": !data?.enabled } });
    return "Настройка автоматической проверки сохранена.";
  });
  const backfill = () => void run("backfill", async () => {
    const result = await api.post<Api["AutoAuditBackfillResponse"]>("/api/source-conflicts/automation/backfill", { all: true });
    return `В очередь добавлено статей: ${result.enqueued}. До ${result.estimated_calls} запросов к модели; выполнение ограничено дневным лимитом.`;
  });
  const retry = (entry: QueueEntry) => void run(entry.id, async () => {
    await api.post(`/api/source-conflicts/automation/${encodeURIComponent(entry.id)}/retry`);
    return `Повторная проверка статьи «${entry.question}» поставлена в очередь.`;
  });
  const counts = data?.counts ?? {};
  const total = Object.entries(counts).filter(([status]) => status !== "superseded").reduce((sum, [, count]) => sum + count, 0);
  const completed = (counts.done ?? 0) + (counts.skipped ?? 0);
  const blocked = !data?.enabled || !data.configured;
  const providerPaused = data?.blocked_reason === "provider_unavailable";
  const panel = (
    <section className="card space-y-3 p-4">
      <div className="flex flex-wrap items-center gap-2"><h2 className="mr-auto text-sm font-semibold">Автоматическая проверка статей</h2>
        <button type="button" className="btn btn-ghost !py-1 text-xs" onClick={state.reload}><RefreshCw size={13} /> Обновить очередь</button>
      </div>
      <p className="text-xs text-(--color-ink-soft)">После загрузки, публикации или изменения ответа проверка сравнивает новую версию с похожими материалами. Сохранение статьи не ждёт ответа модели.</p>
      {state.error && <LoadError message={state.error} onRetry={state.reload} />}
      {!data && !state.error && <p role="status" className="text-xs">Загружаем состояние автоматической проверки…</p>}
      {data && <>
        <div role="status" className="rounded-xl bg-(--color-surface-2) p-3 text-xs">
          {!data.enabled ? "Автоматическая проверка выключена. Очередь и результаты сохраняются." : !data.configured
            ? "Модель не настроена. Статьи ждут проверки; укажите модель в настройках." : data.remaining_calls_today <= 0
              ? "Дневной лимит использован. Очередь продолжится после обновления лимита; его можно изменить в настройках."
              : providerPaused ? "Модель временно недоступна. После паузы проверка продолжится автоматически; очередь сохранена. Если ошибка повторяется, проверьте подключение в настройках."
              : "Автоматическая проверка включена. Новые и изменённые опубликованные ответы проверяются в фоне."}
          {!data.enabled && !data.configured && <p className="mt-1">Модель не настроена. Для начала проверки укажите модель в настройках.</p>}
        </div>
        <div className="flex flex-wrap items-center gap-2 text-xs">
          {canEdit && <button type="button" className="btn btn-ghost text-xs" disabled={Boolean(busy)} onClick={toggle}>
            {data.enabled ? "Выключить автоматическую проверку" : "Включить автоматическую проверку"}
          </button>}
          {canEdit && <a className="text-xs font-medium text-(--color-brand-ink) underline" href="/admin/settings">Настроить модель и лимиты</a>}
        </div>
        <div className="grid gap-3 sm:grid-cols-2">
          <div className="rounded-xl border border-(--color-line) p-3 text-xs">
            <h3 className="font-semibold">Дневной лимит запросов</h3>
            <p className="mt-1">Использовано {data.calls_used_today} из {data.daily_call_limit} · осталось {data.remaining_calls_today}</p>
            <progress className="mt-2 w-full accent-(--color-brand)" aria-label="Использование дневного лимита" value={data.calls_used_today ?? 0} max={Math.max(1, data.daily_call_limit ?? 0)} />
            <p className="mt-1 text-(--color-ink-soft)">Очередь может потребовать до {data.estimated_pending_calls} запросов. Повторные попытки тоже расходуют лимит.</p>
          </div>
          <div className="rounded-xl border border-(--color-line) p-3 text-xs">
            <h3 className="font-semibold">Ранее загруженные материалы</h3>
            <p className="mt-1">Подходящих опубликованных ответов: {data.eligible_articles}. Ещё не добавлены в очередь: {data.unchecked_articles}.</p>
            <p className="mt-1 text-(--color-ink-soft)">Верхняя оценка — до {data.estimated_backfill_calls} запросов к модели. Это поиск среди похожих материалов, без сравнения каждой пары базы.</p>
            {canEdit && <button type="button" className="btn btn-primary mt-3 text-xs" disabled={Boolean(busy) || blocked || !data.unchecked_articles} onClick={backfill}>
              {busy === "backfill" ? "Добавляем в очередь…" : "Проверить существующую базу"}
            </button>}
          </div>
        </div>
        <div className="text-xs">
          <h3 className="font-semibold">Очередь статей</h3>
          <p className="mt-1">В очереди: {counts.pending ?? 0} · в работе: {counts.running ?? 0} · проверено: {counts.done ?? 0} · ждут повтора: {counts.retryable ?? 0} · ожидают запуска: {counts.blocked ?? 0} · пропущено: {counts.skipped ?? 0}</p>
          {total > 0 && <progress className="mt-2 w-full accent-(--color-brand)" aria-label="Обработка очереди статей" value={completed} max={total} />}
        </div>
        <div className="max-h-96 overflow-y-auto rounded-xl border border-(--color-line)">
          {(data.entries ?? []).map((entry) => <EntryRow key={entry.id} entry={entry} onRetry={canEdit ? retry : undefined}
            retryDisabled={Boolean(busy) || blocked || providerPaused || data.remaining_calls_today <= 0} />)}
          {data.entries?.length === 0 && <p className="p-3 text-xs text-(--color-ink-soft)">На этой странице очереди нет статей.</p>}
        </div>
        <div className="flex items-center justify-end gap-2 text-xs">
          <button type="button" className="btn btn-ghost !py-1 text-xs" disabled={page === 0} onClick={() => setPage(page - 1)}>Предыдущие статьи очереди</button>
          <span>Страница {page + 1}</span>
          <button type="button" className="btn btn-ghost !py-1 text-xs" disabled={(data.entries?.length ?? 0) < PAGE_SIZE} onClick={() => setPage(page + 1)}>Следующие статьи очереди</button>
        </div>
      </>}
      {actionError && <p role="alert" className="text-xs text-(--color-brand-ink)">{actionError}</p>}
      {message && <p role="status" className="text-xs text-(--color-ok-ink)">{message}</p>}
    </section>
  );
  if (!compact) return panel;
  const label = state.error ? "Не удалось загрузить очередь" : !data ? "Загрузка очереди…"
    : !data.enabled ? "Автопроверка на паузе" : !data.configured ? "Модель не настроена"
      : providerPaused ? "Модель недоступна" : data.remaining_calls_today <= 0 ? "Дневной лимит исчерпан" : "Автопроверка включена";
  const waiting = (counts.pending ?? 0) + (counts.running ?? 0) + (counts.retryable ?? 0);
  return <details className="card p-3 text-xs">
    <summary className="cursor-pointer">
      <strong>{label}</strong>{data && <span className="text-(--color-ink-soft)"> · В очереди: {waiting}</span>}
      <span className="ml-3 text-(--color-brand-ink)">Управление проверкой</span>
    </summary>
    <div className="mt-3">{panel}</div>
  </details>;
}
