import { useState } from "react";
import { ArrowLeft, BookOpenText, History, Search, X } from "lucide-react";
import type { OperatorCaseHint } from "@/lib/types";
import { useApiData } from "@/lib/useApiData";
import type { Suggestion } from "./useConversation";

/** Колонка подсказок: статьи к последнему вопросу клиента, похожие обращения и поиск по базе знаний. */
export function HintsColumn({ open, onBack, suggestions, cases = [], onInsert }: {
  /** На телефоне колонка развёрнута вместо переписки. */
  open: boolean;
  onBack: () => void;
  suggestions: Suggestion[];
  /** Похожие решённые обращения — отдельный список события copilot (см. caseHints.ts).
   *  Необязателен: страница, которая его ещё не передаёт, показывает одни статьи. */
  cases?: OperatorCaseHint[];
  onInsert: (answer: string) => void;
}) {
  const [kbQuery, setKbQuery] = useState("");
  // Поиск по мере набора: пауза, отмена прежнего запроса и видимая ошибка —
  // вместо брошенного промиса, который молча оставлял старую выдачу.
  const kbPath = kbQuery.trim().length >= 2 ? `/api/operator/kb-search?q=${encodeURIComponent(kbQuery)}` : null;
  const kbSearch = useApiData<Suggestion[]>(kbPath, { delayMs: 250 });
  const kbResults = kbPath ? (kbSearch.data ?? []) : [];

  return (
    <aside className={`card flex-col overflow-hidden ${open ? "flex" : "hidden"}`}>
      <div className="flex items-center gap-2 border-b border-(--color-line) px-3 py-2.5">
        <button className="flex size-10 items-center justify-center rounded-full lg:hidden" aria-label="Назад к переписке" onClick={onBack}>
          <ArrowLeft size={16} />
        </button>
        <span className="text-sm font-semibold">База знаний</span>
        <button className="ml-auto hidden size-10 items-center justify-center rounded-full transition-colors hover:bg-(--color-surface-2) lg:flex"
          aria-label="Закрыть базу знаний" onClick={onBack}>
          <X size={16} />
        </button>
      </div>
      <div className="min-h-0 flex-1 space-y-3 overflow-y-auto p-3">
        <div>
          <label className="label" htmlFor="operator-kb-search">Поиск</label>
          <div className="relative">
            <Search size={14} className="absolute top-3 left-3 text-(--color-ink-soft)" />
            <input id="operator-kb-search" className="input !pl-8" placeholder="Название или вопрос…" value={kbQuery}
              onChange={(e) => setKbQuery(e.target.value)} />
          </div>
          {kbPath && kbSearch.error && (
            <p role="alert" className="mt-2 px-1 text-xs text-(--color-brand-ink)">Поиск не ответил: {kbSearch.error}</p>
          )}
          <div className="mt-2 space-y-2">
            {kbResults.map((s) => (
              <SuggestionCard key={s.article_id} s={s} onInsert={() => onInsert(s.answer)} />
            ))}
          </div>
        </div>
        {suggestions.length > 0 && (
          <div>
            <div className="label">Подходит к вопросу</div>
            <div className="space-y-2">
              {suggestions.map((s) => (
                <SuggestionCard key={s.article_id} s={s} onInsert={() => onInsert(s.answer)} />
              ))}
            </div>
          </div>
        )}
        {/* Статьи и похожие обращения — разные вещи: статья отвечает на вопрос
            целиком, а обращение рассказывает, что оператор сделал в чужом случае
            («проверили заявку, направили заказчику»). Вперемешку отчёт о чужом
            обращении легко принять за готовый ответ и отправить клиенту как есть. */}
        {cases.length > 0 && (
          <details className="rounded-xl border border-(--color-line) p-2.5">
            <summary className="cursor-pointer text-xs font-semibold">Похожие обращения ({cases.length})</summary>
            <p className="my-2 text-xs leading-snug text-(--color-ink-soft)">
              Это опыт из других обращений, а не подтверждение фактов текущего случая.
            </p>
            <div className="space-y-2">
              {cases.map((c) => (
                <SuggestionCard key={c.article_id} s={c} isCase onInsert={() => onInsert(c.answer)} />
              ))}
            </div>
          </details>
        )}
        <div>
          {/* Пустая колонка под пустым полем поиска читалась как «ничего не
              нашлось». Говорим, что происходит на самом деле. */}
          {kbResults.length === 0 && suggestions.length === 0 && cases.length === 0 && (
            <p className="mt-3 text-xs leading-relaxed text-(--color-ink-soft)">
              {kbQuery.trim()
                ? "По этому запросу статей нет. Попробуйте другие слова."
                : "Откройте обращение — покажу материалы по последнему вопросу."}
            </p>
          )}
        </div>
      </div>
    </aside>
  );
}

function SuggestionCard({ s, isCase = false, onInsert }: {
  s: Suggestion;
  /** Похожее обращение, а не статья: свой значок и пометка для скринридера. */
  isCase?: boolean;
  onInsert: () => void;
}) {
  const Icon = isCase ? History : BookOpenText;
  return (
    <div className="rounded-xl border border-(--color-line) p-2.5 transition-colors hover:border-(--color-brand)">
      <div className="mb-1 flex items-start gap-1.5 text-xs font-medium">
        <Icon size={13} aria-hidden="true" className="mt-0.5 shrink-0 text-(--color-brand-ink)" />
        {isCase && <span className="sr-only">Похожее обращение:</span>}
        {s.question}
      </div>
      <div className="mb-1.5 line-clamp-3 text-xs text-(--color-ink-soft)">{s.answer}</div>
      <div className="flex items-center gap-2">
        {!isCase && <button className="text-xs font-medium text-(--color-brand-ink) hover:underline" onClick={onInsert}>
          Вставить в ответ
        </button>}
      </div>
    </div>
  );
}
