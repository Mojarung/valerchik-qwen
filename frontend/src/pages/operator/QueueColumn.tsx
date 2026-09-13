import { CheckCheck, Headset } from "lucide-react";
import { LoadError } from "@/components/LoadError";
import type { QueueThresholds } from "@/lib/store";
import type { OperatorSession } from "@/lib/types";
import { directionName, escalationSummary, supportStatusLabel, type SupportLines } from "./presentation";
import type { QueueFilter } from "./useOperatorQueue";

/** Колонка очереди: фильтр, список обращений со временем ожидания. */
export function QueueColumn({
  filter, onFilter, sessions, error, onRetry, selected, onOpen, waitingOf, thresholds, lines, myLine,
}: {
  filter: QueueFilter;
  onFilter: (filter: QueueFilter) => void;
  sessions: OperatorSession[];
  error: string | null;
  onRetry: () => void;
  /** Открытое обращение: на телефоне список уступает место его переписке. */
  selected: string | null;
  onOpen: (sessionId: string) => void;
  waitingOf: (session: OperatorSession) => number | null;
  thresholds: QueueThresholds;
  lines: SupportLines;
  /** За какую линию отвечает оператор; undefined — сервер ещё не ответил. */
  myLine?: string;
}) {
  return (
    <aside className={`card flex-col overflow-hidden ${selected ? "hidden lg:flex" : "flex"}`}>
      <div className="border-b border-(--color-line) p-2.5">
        <div className="mb-2 flex items-center gap-2">
          <Headset size={17} className="text-(--color-brand-ink)" />
          <h2 className="text-sm font-semibold">Обращения</h2>
          <span className="ml-auto text-xs tabular-nums text-(--color-ink-soft)">{sessions.length}</span>
        </div>
        {myLine && (
          <p className="mb-2 truncate rounded-lg bg-(--color-brand-soft) px-2.5 py-1 text-xs font-medium text-(--color-brand-ink)"
            title={myLine}>
            {myLine}
          </p>
        )}
        <label className="sr-only" htmlFor="queue-filter">Какие обращения показать</label>
        <select id="queue-filter" className="input !min-h-10 !rounded-lg !px-2.5 !py-1.5 text-xs"
          value={filter} onChange={(event) => onFilter(event.target.value as QueueFilter)}>
          <option value="queue">Все активные</option>
          <option value="waiting_operator">Новые</option>
          <option value="operator_assigned">Назначены мне</option>
          <option value="operator_active">В работе</option>
          <option value="waiting_user">Ждём клиента</option>
          <option value="resolved">Завершённые</option>
        </select>
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto">
        {error && (
          <div className="p-3">
            <LoadError message={`Очередь не загрузилась: ${error}`} onRetry={onRetry} />
          </div>
        )}
        {!error && sessions.length === 0 && (
          <div className="flex flex-col items-center gap-1.5 px-4 py-10 text-center">
            <CheckCheck size={22} className="text-(--color-ok-ink)" />
            <div className="text-xs font-semibold">
              {filter === "queue" ? "Очередь пуста" : "Обращений пока нет"}
            </div>
            <p className="max-w-[15rem] text-xs leading-relaxed text-balance text-(--color-ink-soft)">
              {filter === "queue"
                ? "Валерчик отвечает сам. Новое обращение появится здесь, как только он передаст его человеку."
                : "Здесь будут все диалоги, дошедшие до операторов, — и открытые, и завершённые."}
            </p>
          </div>
        )}
        {sessions.map((s) => (
          <button key={s.session_id} onClick={() => onOpen(s.session_id)} title={s.reclaimed_count > 0 ? `Возвращалось в очередь: ${s.reclaimed_count}` : undefined}
            className={`block w-full border-b border-l-2 border-(--color-line) border-l-transparent px-3 py-2 text-left transition-colors active:bg-(--color-surface-2) hover:bg-(--color-surface-2) ${selected === s.session_id ? "border-l-(--color-brand) bg-(--color-brand-soft)" : ""}`}>
            <div className="flex items-center gap-2">
              <span className="min-w-0 flex-1 truncate text-sm font-medium text-pretty">{escalationSummary(s.reason).question || s.last_message || "Нет текста"}</span>
              <WaitBadge seconds={waitingOf(s)} thresholds={thresholds} />
            </div>
            <div className="mt-0.5 flex min-w-0 items-center gap-1.5 text-[11px] text-(--color-ink-soft)">
              <span>{supportStatusLabel(s.status)}</span>
              <span aria-hidden="true">·</span>
              <span className="truncate">{directionName(lines, s.line)}</span>
            </div>
          </button>
        ))}
      </div>
    </aside>
  );
}

/** Сколько человек ждёт. Цвет — единственное, что различает заявки в списке
 *  издалека: минуты мелким шрифтом взглядом по очереди не считываются. */
function WaitBadge({ seconds, thresholds }: { seconds: number | null; thresholds: QueueThresholds }) {
  if (seconds === null || seconds < 60) return null;
  const alert = seconds >= thresholds.alert_seconds;
  const warn = !alert && seconds >= thresholds.warn_seconds;
  const tone = alert ? "text-(--color-brand)" : warn ? "text-(--color-warn-ink)" : "text-(--color-ink-soft)";
  return (
    <span className={`shrink-0 text-[11px] font-semibold tabular-nums ${tone}`}
      title="Столько времени обращение ждёт ответа">
      {formatWait(seconds)}
    </span>
  );
}

function formatWait(seconds: number): string {
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes} мин`;
  const hours = Math.floor(minutes / 60);
  return hours < 24 ? `${hours} ч ${minutes % 60} мин` : `${Math.floor(hours / 24)} сут`;
}
