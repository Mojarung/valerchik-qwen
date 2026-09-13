import { useEffect, useRef, useState } from "react";
import { ChevronDown, Loader2, Paperclip, Send, Sparkles } from "lucide-react";
import type { Draft } from "./useConversation";

/** Поле ответа оператора: ошибка действия, черновик от ИИ, файл и отправка. */
export function ReplyComposer({
  input, onInput, onSend, onFile, uploading, actionError, draft, draftLoading, onRequestDraft,
  onDraftAction,
}: {
  input: string;
  onInput: (text: string) => void;
  onSend: () => void;
  onFile: (file: File) => void;
  uploading: boolean;
  actionError: string;
  draft: Draft | null;
  draftLoading: boolean;
  onRequestDraft: () => void;
  onDraftAction?: (action: "inserted" | "discarded") => void;
}) {
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const [assistantOpen, setAssistantOpen] = useState(false);

  // Высота поля ответа под содержимое: сбрасываем и меряем реальный scrollHeight.
  // Порог задан классом max-h-56 — после него текст прокручивается внутри поля.
  useEffect(() => {
    const el = inputRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${el.scrollHeight}px`;
  }, [input]);

  const insertDraft = () => {
    if (!draft || draft.status === "stale" || draft.status === "error") return;
    onInput(draft.text);
    onDraftAction?.("inserted");
    inputRef.current?.focus();
  };

  const requestDraft = () => {
    setAssistantOpen(true);
    onRequestDraft();
  };

  return (
    <div className="shrink-0 border-t border-(--color-line) p-3">
      {actionError && (
        <div role="alert" className="mb-1.5 rounded-lg border-l-4 border-l-(--color-brand) bg-(--color-surface-2) px-2 py-1 text-xs">
          {actionError}
        </div>
      )}
      <div className="mb-2 rounded-xl border border-(--color-line) bg-(--color-surface-2)">
        <div className="flex min-h-10 items-center gap-1.5 px-3 text-xs font-semibold text-(--color-ink-soft)">
          <Sparkles size={12} className="text-(--color-brand-ink)" />
          ИИ-помощник
          <span className="font-normal">
            {draftLoading ? "готовит ответ…" : draft ? "вариант готов" : "может подготовить ответ"}
          </span>
          {draft && <button className="ml-auto flex min-h-10 items-center gap-1 px-1 font-medium text-(--color-brand-ink)"
            onClick={() => setAssistantOpen(value => !value)} aria-expanded={assistantOpen}>
            {assistantOpen ? "Скрыть" : "Посмотреть"} <ChevronDown size={13} className={assistantOpen ? "rotate-180" : ""} />
          </button>}
          {!draft && !draftLoading && <button className="ml-auto min-h-10 px-1 font-medium text-(--color-brand-ink) hover:underline"
            onClick={requestDraft}>Подготовить</button>}
        </div>
        {draftLoading && (
          <div className="flex items-center gap-1.5 border-t border-(--color-line) px-3 py-2 text-xs text-(--color-ink-soft)">
            <Loader2 size={12} className="animate-spin" /> Это обычно занимает несколько секунд
          </div>
        )}
        {!draftLoading && draft && assistantOpen && (
          <div className="max-h-64 overflow-y-auto border-t border-(--color-line) p-3 text-xs">
            {draft.status === "stale" && <p role="status">Подсказка устарела: в диалоге есть новые сообщения.</p>}
            {draft.error && <p role="alert">{draft.error}</p>}
            <div className="mb-2 whitespace-pre-wrap leading-relaxed">
              {draft.text}
            </div>
            <div className="flex flex-wrap items-center gap-3">
              <button disabled={!draft.text || draft.status === "stale" || draft.status === "error"}
                className="font-semibold text-(--color-brand-ink) hover:underline disabled:opacity-50"
                onClick={insertDraft}>Вставить в ответ</button>
              <button className="hover:underline" onClick={requestDraft}>Другой вариант</button>
              <button className="hover:underline" onClick={() => { onDraftAction?.("discarded"); setAssistantOpen(false); }}>Отклонить</button>
            </div>
            {(draft.summary || draft.checks?.length || draft.missing?.length || draft.warnings?.length) && (
              <details className="mt-3 border-t border-(--color-line) pt-2">
                <summary className="cursor-pointer font-semibold">Сводка и проверки</summary>
                {draft.summary && <p className="mt-2">{draft.summary}</p>}
                {!!draft.checks?.length && <ul className="mt-2 list-disc pl-4">{draft.checks.map((item, i) => <li key={i}>{item}</li>)}</ul>}
                {!!draft.missing?.length && <p className="mt-2"><b>Уточнить:</b> {draft.missing.join("; ")}</p>}
                {!!draft.warnings?.length && <p role="alert" className="mt-2 text-(--color-warn-ink)">{draft.warnings.join("; ")}</p>}
              </details>
            )}
          </div>
        )}
      </div>

      <div className="flex items-end gap-1.5">
        {/* Поле растёт под текст до порога, дальше прокручивается:
            оператор пишет развёрнутые ответы, и набирать их в щель
            высотой в две строки — значит не видеть, что написал. */}
        <textarea ref={inputRef}
          className="input max-h-56 min-h-16 flex-1 resize-none !py-2 text-xs"
          value={input} placeholder="Ответ пользователю…"
          onChange={(e) => onInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); onSend(); }
            // Tab подставляет черновик только в пустое поле: если оператор уже
            // начал печатать свой ответ, затирать его нельзя, и Tab остаётся
            // обычным переходом по фокусу.
            if (e.key === "Tab" && draft && !input.trim()) {
              e.preventDefault();
              insertDraft();
            }
          }} />
        <input ref={fileRef} type="file" className="hidden"
          accept=".png,.jpg,.jpeg,.gif,.webp,.pdf,.txt,.csv,.doc,.docx,.xls,.xlsx,.zip"
          onChange={(e) => {
            const picked = e.target.files?.[0];
            e.target.value = "";       // тот же файл можно выбрать повторно
            if (picked) onFile(picked);
          }} />
        <button className="btn btn-ghost !p-2.5" disabled={uploading}
          title="Прикрепить скриншот или документ"
          onClick={() => fileRef.current?.click()}>
          {uploading ? <Loader2 size={15} className="animate-spin" /> : <Paperclip size={15} />}
        </button>
        <button className="btn btn-primary !p-2.5" onClick={onSend} disabled={!input.trim()}>
          <Send size={15} />
        </button>
      </div>
    </div>
  );
}
