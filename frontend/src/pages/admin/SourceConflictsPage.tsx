import { useState } from "react";
import { CircleCheckBig, GitCompareArrows, RefreshCw } from "lucide-react";
import { LoadError } from "@/components/LoadError";
import { useAuth } from "@/lib/store";
import { useApiData } from "@/lib/useApiData";
import { ConflictReview } from "./source-conflicts/ConflictReview";
import { REVIEW_LABEL, type ConflictGroup } from "./source-conflicts/types";

type View = "new" | "resolved";

function variantsLabel(count: number) {
  if (count % 10 === 1 && count % 100 !== 11) return `${count} вариант`;
  if (count % 10 >= 2 && count % 10 <= 4 && (count % 100 < 12 || count % 100 > 14)) {
    return `${count} варианта`;
  }
  return `${count} вариантов`;
}

function ResultCard({ group, onOpen }: { group: ConflictGroup; onOpen: () => void }) {
  const preferred = group.sources.find(
    ({ source }) => source.article_id === group.preferred_article_id,
  );
  const evidence = group.sources.slice(0, 2);
  return (
    <button
      type="button"
      onClick={onOpen}
      className="group block min-h-11 w-full rounded-xl border border-(--color-line) bg-(--color-surface) p-4 text-left transition-[border-color,background-color] hover:border-(--color-brand) hover:bg-(--color-surface-2)"
    >
      <span className="flex items-center gap-2 text-xs text-(--color-ink-soft)">
        {group.review_status === "confirmed" && <CircleCheckBig size={14} className="text-(--color-ok-ink)" aria-hidden />}
        <span>{REVIEW_LABEL[group.review_status ?? "unreviewed"]}</span>
        <span>· {variantsLabel(group.sources.length)}</span>
        <span className="ml-auto text-(--color-brand-ink)">Открыть →</span>
      </span>
      <strong className="mt-2 block text-sm leading-snug">
        {group.fact || group.question}
      </strong>
      {preferred && (
        <span className="mt-2 block text-xs text-(--color-ok-ink)">
          Выбран ответ: {preferred.quote || preferred.source.answer}
        </span>
      )}
      <span className="mt-3 grid gap-2 sm:grid-cols-2">
        {evidence.map(({ source, quote }) => (
          <span key={source.id} className="min-w-0 rounded-lg bg-(--color-surface-2) px-3 py-2">
            <span className="line-clamp-2 break-words text-xs font-medium">«{quote || "Цитата не выделена"}»</span>
          </span>
        ))}
      </span>
      {group.sources.length > 2 && (
        <span className="mt-2 block text-xs text-(--color-ink-soft)">Ещё {group.sources.length - 2}</span>
      )}
    </button>
  );
}

export default function SourceConflictsPage() {
  const canEdit = useAuth((state) => state.role) === "admin";
  const [view, setView] = useState<View>("new");
  const [reviewId, setReviewId] = useState<string | null>(null);
  const [message, setMessage] = useState("");
  const pending = useApiData<ConflictGroup[]>(
    "/api/source-conflicts/groups?limit=200&offset=0&reviewed=false",
  );
  const resolved = useApiData<ConflictGroup[]>(
    "/api/source-conflicts/groups?limit=200&offset=0&reviewed=true",
  );
  const current = view === "new" ? pending : resolved;
  const refresh = () => {
    pending.reload();
    resolved.reload();
  };

  return (
    <div className="space-y-3">
      <header className="card flex flex-wrap items-center gap-3 p-4">
        <GitCompareArrows size={18} className="text-(--color-brand-ink)" aria-hidden />
        <div className="mr-auto">
          <h2 className="text-sm font-semibold">Расхождения в статьях</h2>
          <p className="mt-1 text-xs text-(--color-ink-soft)">
            Новые и изменённые статьи проверяются автоматически.
          </p>
        </div>
        <button type="button" className="btn btn-ghost min-h-10 text-xs" onClick={refresh}>
          <RefreshCw size={14} aria-hidden /> Обновить
        </button>
      </header>

      {message && <p role="status" className="card p-3 text-xs text-(--color-ok-ink)">{message}</p>}

      <section className="card p-4">
        <div className="mb-4 flex gap-1 rounded-xl bg-(--color-surface-2) p-1" role="tablist" aria-label="Статус расхождений">
          <button
            type="button"
            role="tab"
            aria-selected={view === "new"}
            className={`min-h-10 flex-1 rounded-lg px-3 text-xs font-medium transition-colors ${view === "new" ? "bg-(--color-surface) text-(--color-ink) shadow-sm" : "text-(--color-ink-soft)"}`}
            onClick={() => setView("new")}
          >
            Требуют решения{pending.data ? ` · ${pending.data.length}` : ""}
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={view === "resolved"}
            className={`min-h-10 flex-1 rounded-lg px-3 text-xs font-medium transition-colors ${view === "resolved" ? "bg-(--color-surface) text-(--color-ink) shadow-sm" : "text-(--color-ink-soft)"}`}
            onClick={() => setView("resolved")}
          >
            Решённые{resolved.data ? ` · ${resolved.data.length}` : ""}
          </button>
        </div>

        {current.error && <LoadError message={current.error} onRetry={current.reload} />}
        {current.loading && !current.data && <p role="status" className="py-6 text-center text-xs">Загружаем расхождения…</p>}
        {current.data?.length === 0 && (
          <p className="py-8 text-center text-xs text-(--color-ink-soft)">
            {view === "new" ? "Сейчас нет расхождений, требующих решения." : "Решений пока нет."}
          </p>
        )}
        <div className="space-y-2">
          {current.data?.map((group) => (
            <ResultCard key={group.id} group={group} onOpen={() => setReviewId(group.id)} />
          ))}
        </div>
      </section>

      {reviewId && (
        <ConflictReview
          id={reviewId}
          canEdit={canEdit}
          onClose={() => setReviewId(null)}
          onSaved={() => {
            setReviewId(null);
            setMessage("Решение сохранено и будет использоваться в ответах.");
            refresh();
          }}
        />
      )}
    </div>
  );
}
