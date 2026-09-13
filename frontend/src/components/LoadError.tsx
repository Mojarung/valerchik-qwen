import { CircleAlert, RotateCw } from "lucide-react";

/** Ошибка загрузки с повтором: сказать, что пошло не так, и дать попробовать ещё раз. */
export function LoadError({ message, onRetry }: { message: string; onRetry: () => void }) {
  return (
    <div
      role="alert"
      className="flex flex-wrap items-center gap-2 rounded-xl border border-(--color-brand) bg-(--color-brand-soft) px-3 py-2 text-xs text-(--color-brand-ink)"
    >
      <CircleAlert size={14} className="shrink-0" aria-hidden />
      <span className="mr-auto">{message}</span>
      <button type="button" className="btn btn-ghost !min-h-0 !py-1 text-xs" onClick={onRetry}>
        <RotateCw size={12} aria-hidden /> Повторить
      </button>
    </div>
  );
}
