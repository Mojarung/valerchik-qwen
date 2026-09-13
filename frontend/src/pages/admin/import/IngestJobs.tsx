import { Loader2, RefreshCcw } from "lucide-react";
import { LoadError } from "@/components/LoadError";
import type { Api } from "@/lib/types";

type IngestJob = Api["IngestJobOut"];

/** Задача ещё идёт — список стоит переспросить. */
export const isActive = (job: IngestJob) => job.status === "pending" || job.status === "running";

const STATUS_LABEL: Record<IngestJob["status"], string> = {
  pending: "в очереди",
  running: "разбирается",
  done: "готово",
  error: "ошибка",
};

function formatWhen(iso: string): string {
  return new Date(iso).toLocaleString("ru", { day: "2-digit", month: "2-digit",
    hour: "2-digit", minute: "2-digit" });
}

/** Длительность задачи: разбор PDF идёт минутами, и это стоит видеть. */
function duration(job: IngestJob): string {
  if (!job.finished_at) return "";
  const seconds = Math.max(
    0, Math.round((Date.parse(job.finished_at) - Date.parse(job.created_at)) / 1000),
  );
  return seconds < 60 ? `${seconds} с` : `${Math.floor(seconds / 60)} мин ${seconds % 60} с`;
}

/** Задачи импорта с прогрессом. Ошибку загрузки списка не глотаем: пустой
 *  список читается как «документов не было». */
export function IngestJobs({ jobs, error, onReload }: {
  jobs: IngestJob[];
  error: string | null;
  onReload: () => void;
}) {
  const busy = jobs.some(isActive);
  return (
    <div className="card divide-y divide-(--color-line)">
      <div className="flex items-center gap-2 px-4 py-2.5">
        <div className="mr-auto text-xs font-bold">Задачи импорта</div>
        {busy && <Loader2 size={13} className="animate-spin text-(--color-brand-ink)" />}
        <button className="btn btn-ghost !p-1.5" title="Обновить" onClick={onReload}>
          <RefreshCcw size={13} />
        </button>
      </div>
      {error && (
        <div className="px-4 py-3">
          <LoadError message={`Список задач не загрузился: ${error}`} onRetry={onReload} />
        </div>
      )}
      {!error && jobs.length === 0 && (
        <div className="p-6 text-center text-xs text-(--color-ink-soft)">
          Документы ещё не загружали
        </div>
      )}
      {jobs.map((job) => {
        const percent = job.total > 0
          ? Math.min(100, Math.round((job.processed / job.total) * 100))
          : 0;
        return (
          <div key={job.id} className="px-4 py-3">
            <div className="flex flex-wrap items-center gap-2">
              <div className="min-w-0 flex-1">
                <div className="truncate text-xs font-medium">{job.title}</div>
                <div className="truncate text-xs text-(--color-ink-soft)">
                  {job.filename || "текстом"} · {job.actor} · {formatWhen(job.created_at)}
                  {duration(job) && ` · ${duration(job)}`}
                </div>
              </div>
              <StatusChip status={job.status} />
              {job.status === "done" && (
                <span className="text-xs text-(--color-ink-soft)">
                  статей: <b>{job.created_articles}</b>
                </span>
              )}
            </div>

            {isActive(job) && (
              <div className="mt-2">
                <div className="h-1.5 overflow-hidden rounded-full bg-(--color-surface-2)">
                  <div className="h-full rounded-full bg-(--color-brand) transition-[width] duration-500"
                    style={{ width: `${percent || 3}%` }} />
                </div>
                <div className="mt-1 text-xs text-(--color-ink-soft)">
                  {job.total > 0
                    ? `${job.processed} из ${job.total} фрагментов`
                    : "разбираем документ — на PDF это занимает минуты"}
                </div>
              </div>
            )}
            {job.status === "error" && job.error && (
              <div className="mt-2 rounded-lg bg-(--color-surface-2) px-3 py-1.5 text-xs text-(--color-ink-soft)">
                {job.error}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}

function StatusChip({ status }: { status: IngestJob["status"] }) {
  const tone = {
    pending: "bg-(--color-surface-2) text-(--color-ink-soft)",
    running: "bg-(--color-brand-soft) text-(--color-brand-ink)",
    done: "bg-(--color-ok)/15 text-(--color-ok-ink)",
    error: "bg-(--color-brand-soft) text-(--color-brand-ink)",
  }[status];
  return (
    <span className={`shrink-0 rounded-full px-2 py-0.5 text-xs font-bold ${tone}`}>
      {STATUS_LABEL[status]}
    </span>
  );
}
