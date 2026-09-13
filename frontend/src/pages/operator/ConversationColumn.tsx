import { useEffect, useRef, type ReactNode } from "react";
import { ArrowLeft, BookOpenText, CircleAlert, Headset, Loader2, RotateCw } from "lucide-react";
import { Attachment, attachmentOf } from "@/components/Attachment";
import type { OperatorSession } from "@/lib/types";
import { supportStatusLabel } from "./presentation";
import type { Conversation } from "./useConversation";

/** Колонка переписки: шапка обращения с действиями, лента и поле ответа (children). */
export function ConversationColumn({
  current, visible, lineLabel, isMine, conversation, onBack, onShowHints, onTake, children, actions,
}: {
  current: OperatorSession | undefined;
  /** На телефоне колонка видна, когда обращение открыто и подсказки свёрнуты. */
  visible: boolean;
  lineLabel: string | undefined;
  /** Обращение ведёт этот оператор — можно отвечать. */
  isMine: boolean;
  conversation: Conversation;
  onBack: () => void;
  onShowHints: () => void;
  onTake: () => void;
  /** Поле ответа: показывается, только когда обращение взято в работу. */
  children: ReactNode;
  actions?: ReactNode;
}) {
  const { messages, historyState, historyError, userTyping, assistantTyping, actionError, suggestions, retryHistory } = conversation;
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ block: "end" });
  }, [messages, assistantTyping]);

  return (
    <section className={`card flex-col overflow-hidden ${visible ? "flex" : "hidden lg:flex"}`}>
      {!current ? (
        <div className="flex flex-1 flex-col items-center justify-center gap-2 px-6 text-center">
          <Headset size={28} className="text-(--color-ink-soft) opacity-50" />
          <div className="text-sm font-semibold">Обращение не выбрано</div>
          <p className="max-w-xs text-xs leading-relaxed text-balance text-(--color-ink-soft)">
            Откройте диалог слева — переписка с Валерчиком и подсказки
            по обращению появятся здесь.
          </p>
        </div>
      ) : (
        <>
          <div className="flex items-center gap-2 border-b border-(--color-line) px-3 py-2.5">
            <button className="flex size-10 items-center justify-center rounded-full lg:hidden" aria-label="К списку обращений" onClick={onBack}>
              <ArrowLeft size={16} />
            </button>
            <div className="mr-auto min-w-0">
              <div className="truncate text-sm font-semibold">{lineLabel || "Обращение в поддержку"}</div>
              <div className="text-xs text-(--color-ink-soft)">{supportStatusLabel(current.status)}</div>
            </div>
            <button className="btn btn-ghost !min-h-10 !px-3 !py-2 text-xs"
              onClick={onShowHints} title="Открыть базу знаний">
              <BookOpenText size={13} />
              <span className="hidden sm:inline">База знаний</span>
              {suggestions.length > 0 && <span className="tabular-nums">{suggestions.length}</span>}
            </button>
            {!isMine && current?.available_actions?.includes("take") && (
              <button className="btn btn-primary !py-1.5 text-xs" onClick={onTake}>
                Взять в работу
              </button>
            )}
          </div>
          {actions}
          {/* До «Взять в работу» поля ответа нет — ошибке взятия нужно своё место */}
          {!isMine && actionError && (
            <div className="px-3 pt-2">
              <div role="alert" className="rounded-lg border-l-4 border-l-(--color-brand) bg-(--color-surface-2) px-2 py-1 text-xs">
                {actionError}
              </div>
            </div>
          )}
          <div className="min-h-0 flex-1 space-y-2 overflow-y-auto p-3">
            {historyState === "loading" && messages.length === 0 && (
              <div className="flex items-center gap-2 text-xs text-(--color-ink-soft)" role="status">
                <Loader2 size={14} className="animate-spin" /> Загружаем переписку…
              </div>
            )}
            {historyState === "error" && (
              <div role="alert" className="flex flex-wrap items-center gap-2 rounded-xl border border-(--color-brand) bg-(--color-brand-soft) px-3 py-2 text-xs text-(--color-brand-ink)">
                <CircleAlert size={14} className="shrink-0" />
                <span className="mr-auto">Не удалось загрузить переписку: {historyError}</span>
                <button className="btn btn-ghost !min-h-0 !py-1 text-xs" onClick={retryHistory}>
                  <RotateCw size={12} /> Повторить
                </button>
              </div>
            )}
            {messages.map((m) => (
              <div key={m.id} className={`flex ${m.role === "operator" ? "justify-end" : "justify-start"}`}>
                <div className={`max-w-[85%] rounded-xl px-3 py-2 text-sm leading-relaxed whitespace-pre-wrap ${bubbleClass(m.role)}`}>
                  {m.role !== "user" && (
                    <div className="mb-0.5 text-xs font-semibold opacity-60">{roleLabel(m.role)}</div>
                  )}
                  {m.content}
                  {attachmentOf(m.meta) && (
                    <Attachment attachment={attachmentOf(m.meta)!} />
                  )}
                </div>
              </div>
            ))}
            {assistantTyping && (
              <div className="flex justify-start">
                <div className="flex items-center gap-1.5 rounded-xl border border-(--color-line) px-3 py-2">
                  <span className="typing-dot" /><span className="typing-dot" /><span className="typing-dot" />
                </div>
              </div>
            )}
            {userTyping && (
              <div className="text-xs text-(--color-ink-soft) italic">
                посетитель печатает: {userTyping}…
              </div>
            )}
            <div ref={bottomRef} />
          </div>
          {isMine && children}
        </>
      )}
    </section>
  );
}

function roleLabel(role: string): string {
  return { assistant: "Валерчик (ИИ)", operator: "Оператор", system: "Система" }[role] ?? role;
}

function bubbleClass(role: string): string {
  switch (role) {
    case "operator": return "bg-(--color-brand) text-white";
    case "user": return "bg-(--color-surface-2)";
    case "system": return "border border-dashed border-(--color-line) text-(--color-ink-soft)";
    default: return "border border-(--color-line)";
  }
}
