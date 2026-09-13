import { useEffect } from "react";
import { BellRing } from "lucide-react";
import type { Assignment } from "./useConversation";

/** Попап о назначенном обращении. Не модалка: перекрывать работу с текущим
 *  диалогом нельзя, поэтому карточка висит в углу, пока её не разберут. */
export function AssignmentPopup({ assignment, direction, onOpen, onLater }: {
  assignment: Assignment;
  direction?: string;
  onOpen: () => void;
  onLater: () => void;
}) {
  // Вкладка в фоне — попапа не видно, а системных уведомлений мы не спрашиваем.
  // Заголовок вкладки виден в панели браузера всегда: этого хватает, чтобы
  // назначение не потерялось, и не требует ни разрешений, ни service worker.
  useEffect(() => {
    const original = document.title;
    document.title = "🔴 Вам назначено обращение";
    return () => { document.title = original; };
  }, [assignment]);

  return (
    <div className="animate-(--animate-pop) fixed right-4 bottom-4 z-50 w-[min(22rem,calc(100vw-2rem))]">
      <div className="card border-l-4 border-l-(--color-brand) p-4 shadow-lg">
        <div className="mb-1 flex items-center gap-1.5 text-xs font-bold text-(--color-brand-ink)">
          <BellRing size={13} /> Вам назначено обращение
        </div>
        <p className="mb-1 text-xs text-(--color-ink-soft)">
          Ждёт в очереди {Math.round(assignment.waiting_seconds / 60) || 1} мин
          {direction ? ` · ${direction}` : ""}
        </p>
        {(assignment.question || assignment.reason) && (
          <p className="mb-3 line-clamp-3 text-xs">{assignment.question || assignment.reason}</p>
        )}
        <div className="flex gap-2">
          <button className="btn btn-primary flex-1 !py-1.5 text-xs" onClick={onOpen}>
            Открыть диалог
          </button>
          <button className="btn btn-ghost !py-1.5 text-xs" onClick={onLater}>
            Позже
          </button>
        </div>
      </div>
    </div>
  );
}
