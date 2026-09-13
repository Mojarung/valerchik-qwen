import { CircleAlert, Clock } from "lucide-react";

/** Что с репликой: ждёт открытия чата, уже ушла или открыть чат не вышло. */
export type PendingState = "waiting" | "sending" | "failed";

/** Реплика, которую сервер ещё не показал в ленте: набрана, пока чат открывался.
 *  Видна сразу, бледнее обычной и с подписью — иначе Enter выглядел бы как
 *  «ничего не произошло», а текст исчезал бы из поля без следа. */
export function PendingBubble({ text, state, onRetry }: {
  text: string;
  state: PendingState;
  onRetry: () => void;
}) {
  return (
    <div className="flex justify-end">
      <div className="flex max-w-[85%] flex-col items-end">
        <div className="rounded-3xl rounded-br-lg bg-(--color-brand) px-4 py-3 text-sm whitespace-pre-wrap text-white opacity-60">
          {text}
        </div>
        <div className="mt-1 flex items-center gap-1 text-xs text-(--color-ink-soft)">
          {state === "failed" ? (
            <>
              <CircleAlert size={11} className="text-(--color-brand-ink)" /> не отправлено ·
              <button type="button" className="font-medium text-(--color-brand-ink) underline" onClick={onRetry}>
                повторить
              </button>
            </>
          ) : (
            <>
              <Clock size={11} /> {state === "waiting" ? "отправлю, как только чат откроется" : "отправляю…"}
            </>
          )}
        </div>
      </div>
    </div>
  );
}
