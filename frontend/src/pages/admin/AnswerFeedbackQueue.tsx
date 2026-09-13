import { useState } from "react";
import {
  Check, ChevronDown, ChevronLeft, ChevronRight, MessageSquareWarning, RefreshCcw,
} from "lucide-react";
import { LoadError } from "@/components/LoadError";
import { Markdown } from "@/components/Markdown";
import { Modal } from "@/components/Modal";
import { api, errorText } from "@/lib/api";
import { useAuth } from "@/lib/store";
import type { Api, KbArticle } from "@/lib/types";
import { useApiData } from "@/lib/useApiData";
import { FEEDBACK_REASONS } from "@/pages/chat/AnswerRating";
import { ArticleEditor } from "./kb/ArticleEditor";

type Review = Api["AnswerReviewItem"];
type Status = Api["AnswerReviewUpdate"]["status"];
interface Snapshot {
  question?: string; answer?: string; audience?: string;
  sources?: { id: string; question: string; answer: string; updated_at: string }[];
}
const STATUS: Record<string, string> = {
  open: "Новое", in_progress: "В работе", fixed: "Исправлено", verified: "Проверено",
  dismissed: "Отклонено", withdrawn: "Оценка изменена",
};
const reasonLabel = (reason: string) => FEEDBACK_REASONS.find(([key]) => key === reason)?.[1] ?? "Причина не указана";

const AUDIENCE: Record<string, string> = {
  customer: "Заказчик",
  supplier: "Поставщик",
};

const STATUS_HINT: Record<string, string> = {
  open: "Возьмите замечание в работу, чтобы зафиксировать следующий шаг.",
  in_progress: "Сохранение статьи переведёт замечание в статус «Исправлено».",
  fixed: "Проверьте обновлённый ответ и подтвердите результат.",
  verified: "Ответ проверен. При необходимости замечание можно вернуть в работу.",
  dismissed: "Замечание отклонено. При необходимости его можно вернуть в работу.",
  withdrawn: "Пользователь изменил оценку ответа.",
};

function meaningfulSourceText(value?: string): string {
  const text = value?.trim() ?? "";
  const withoutEmptyMarkers = text.replace(/^\s*(?:[-*+]|\d+[.)])\s*$/gm, "").trim();
  return withoutEmptyMarkers ? text : "";
}

function MetaBadge({ label, value, tone = "neutral" }: {
  label: string;
  value: string;
  tone?: "neutral" | "brand" | "ok";
}) {
  const color = tone === "brand"
    ? "bg-(--color-brand-soft) text-(--color-brand-ink)"
    : tone === "ok"
      ? "bg-(--color-ok)/15 text-(--color-ok-ink)"
      : "bg-(--color-surface-2) text-(--color-ink)";
  return (
    <span className={`inline-flex min-h-8 items-center gap-1.5 rounded-full px-3 py-1 text-xs ${color}`}>
      <span className="opacity-70">{label}</span>
      <span className="font-semibold">{value}</span>
    </span>
  );
}

export function AnswerFeedbackQueue() {
  const [filter, setFilter] = useState("pending");
  const [page, setPage] = useState(1);
  const [expanded, setExpanded] = useState(false);
  const [selected, setSelected] = useState<Review | null>(null);
  const { data, error, reload } = useApiData<Api["AnswerReviewPage"]>(`/api/admin/answer-feedback?status=${filter}&page=${page}`);
  const votes = (data?.positive ?? 0) + (data?.negative ?? 0);
  const items = data?.items ?? [];
  const visible = expanded ? items : items.slice(0, 3);
  return (
    <section className="card space-y-3 p-4" aria-label="Замечания к ответам">
      <div className="flex items-center justify-between gap-2">
        <h2 className="flex items-center gap-2 text-sm font-bold">
          <MessageSquareWarning size={17} className="text-(--color-brand-ink)" /> Замечания к ответам
          {(data?.open_count ?? 0) > 0 && (
            <span className="rounded-full bg-(--color-brand-soft) px-2 py-0.5 text-xs text-(--color-brand-ink) tabular-nums">
              {data?.open_count}
            </span>
          )}
        </h2>
        <button type="button" className="btn btn-ghost !min-h-0 !px-3 !py-2 text-xs" onClick={reload}>
          <RefreshCcw size={13} /> Обновить
        </button>
      </div>
      {error && <LoadError message={error} onRetry={reload} />}
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1 rounded-lg bg-(--color-surface-2) px-3 py-2 text-xs">
        <span className="text-(--color-ink-soft)">
          {votes ? `Полезность ${Math.round((data?.positive ?? 0) / votes * 100)}% · оценок: ${votes}`
            : "Пока нет оценок отдельных ответов"}
        </span>
      </div>
      {items.length > 0 && (
        <div className="flex items-center justify-between gap-2">
          <select aria-label="Статус замечаний" className="input w-auto text-xs" value={filter}
            onChange={(e) => { setFilter(e.target.value); setPage(1); setExpanded(false); }}>
            <option value="pending">Новые и в работе</option>
            <option value="all">Все замечания</option>
            {Object.entries(STATUS).filter(([key]) => key !== "withdrawn").map(([key, label]) => (
              <option key={key} value={key}>{label}</option>
            ))}
          </select>
          <span className="text-xs text-(--color-ink-soft)">{data?.total ?? 0} замечаний</span>
        </div>
      )}
      {data && items.length === 0 && (
        <p className="text-xs text-(--color-ink-soft)">Замечаний, которые нужно разобрать, нет.</p>
      )}
      <div className="divide-y divide-(--color-line) border-y border-(--color-line)">
        {visible.map((row) => {
          const context = row.snapshot as Snapshot;
          return (
            <button type="button" key={row.message_id} onClick={() => setSelected(row)}
              className="grid w-full grid-cols-[minmax(0,1fr)_auto] gap-3 py-2.5 text-left transition-colors hover:bg-(--color-surface-2)">
              <div className="min-w-0">
                <p className="truncate text-sm font-medium">{context.question || "Открыть ответ"}</p>
                <p className="mt-0.5 truncate text-xs text-(--color-ink-soft)">
                  <span className="text-(--color-brand-ink)">{reasonLabel(row.reason)}</span> · {STATUS[row.status] ?? row.status}
                </p>
              </div>
              <span className="pt-0.5 text-xs whitespace-nowrap text-(--color-ink-soft)">
                {new Date(row.updated_at).toLocaleDateString("ru")}
              </span>
            </button>
          );
        })}
      </div>
      {items.length > 3 && (
        <button type="button" className="text-xs font-semibold text-(--color-brand-ink)"
          onClick={() => setExpanded((value) => !value)}>
          {expanded ? "Свернуть" : `Показать ещё ${items.length - 3}`}
        </button>
      )}
      {data && data.total > 30 && (
        <div className="flex items-center justify-center gap-3 text-xs">
          <button type="button" className="btn btn-ghost" aria-label="Предыдущая страница" disabled={page === 1} onClick={() => setPage(page - 1)}><ChevronLeft size={14} /></button>
          {page} / {Math.ceil(data.total / 30)}
          <button type="button" className="btn btn-ghost" aria-label="Следующая страница" disabled={page * 30 >= data.total} onClick={() => setPage(page + 1)}><ChevronRight size={14} /></button>
        </div>
      )}
      {selected && <ReviewDialog key={selected.message_id} initial={selected}
        onClose={() => setSelected(null)} onDone={() => { setSelected(null); reload(); }} />}
    </section>
  );
}

function ReviewDialog({ initial, onClose, onDone }: { initial: Review; onClose: () => void; onDone: () => void }) {
  const [row, setRow] = useState(initial);
  const [note, setNote] = useState(initial.resolution_note);
  const [editing, setEditing] = useState<Partial<KbArticle> | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const isAdmin = useAuth((s) => s.role) === "admin";
  const context = row.snapshot as Snapshot;
  const base = `/api/admin/answer-feedback/${row.message_id}`;
  const changeStatus = async (status: Status) => {
    setBusy(true); setError("");
    try { setRow(await api.patch<Review>(base, { status, note })); }
    catch (e) { setError(errorText(e, "Статус не сохранился")); }
    finally { setBusy(false); }
  };
  const editArticle = async (id: string) => {
    setBusy(true); setError("");
    try { setEditing(await api.get<KbArticle>(`/api/admin/answer-feedback/articles/${id}`)); }
    catch (e) { setError(errorText(e, "Не удалось открыть статью")); }
    finally { setBusy(false); }
  };
  const saveArticle = async (article: Partial<KbArticle>) => {
    await api.post<Api["AnswerReviewResolved"]>(`${base}/resolve`, {
      article_id: article.id ?? null, expected_updated_at: editing?.updated_at ?? null,
      note, article: {
        question: article.question, answer: article.answer, category: article.category,
        keywords: article.keywords ?? [], line: article.line ?? 1, audience: article.audience ?? "all",
        images: article.images ?? [], status: article.status ?? "active",
      },
    }, { timeoutMs: 90_000 });
    onDone();
  };
  if (editing) return <ArticleEditor initial={editing} onSave={saveArticle} onClose={() => setEditing(null)} />;
  const editable = row.status === "open" || row.status === "in_progress";
  const noteLength = note.trim().length;
  const sources = context.sources ?? [];
  const audience = AUDIENCE[context.audience ?? ""] ?? "Роль не указана";
  const statusTone = row.status === "fixed" || row.status === "verified" ? "ok" : row.status === "open" ? "brand" : "neutral";
  return (
    <Modal title="Разбор ответа" size="xl" onClose={() => { onClose(); onDone(); }}>
      <div className="space-y-5 text-sm">
        <div className="flex flex-wrap gap-2" aria-label="Сведения о замечании">
          <MetaBadge label="Статус" value={STATUS[row.status] ?? row.status} tone={statusTone} />
          <MetaBadge label="Причина" value={reasonLabel(row.reason)} tone={row.reason ? "brand" : "neutral"} />
          <MetaBadge label="Роль" value={audience} />
          <MetaBadge label="Оценка" value={new Date(row.created_at).toLocaleString("ru")} />
        </div>

        <div className={`grid items-start gap-6 ${isAdmin ? "lg:grid-cols-[minmax(0,1fr)_20rem]" : ""}`}>
          <main className="min-w-0 space-y-5">
            <section aria-labelledby="review-question-title">
              <h3 id="review-question-title" className="mb-2 text-xs font-bold uppercase tracking-wide text-(--color-ink-soft)">
                Вопрос пользователя
              </h3>
              <p className="text-pretty whitespace-pre-wrap text-lg font-semibold leading-snug">
                {context.question || "Вопрос не сохранён"}
              </p>
            </section>

            <section className="rounded-2xl bg-(--color-surface-2) p-4 sm:p-5" aria-labelledby="review-answer-title">
              <h3 id="review-answer-title" className="mb-3 text-sm font-bold">
                Ответ, который получил пользователь
              </h3>
              {context.answer ? <Markdown>{context.answer}</Markdown> : (
                <p className="text-(--color-ink-soft)">Ответ не сохранён.</p>
              )}
            </section>

            {row.comment && (
              <section className="rounded-r-2xl border-l-4 border-(--color-warn) bg-(--color-warn-soft) px-4 py-3" aria-labelledby="review-comment-title">
                <h3 id="review-comment-title" className="mb-1 text-xs font-bold uppercase tracking-wide text-(--color-warn-ink)">
                  Комментарий пользователя
                </h3>
                <p className="text-pretty whitespace-pre-wrap">{row.comment}</p>
              </section>
            )}

            <section className="space-y-2" aria-labelledby="review-sources-title">
              <div className="flex items-center justify-between gap-3">
                <h3 id="review-sources-title" className="text-sm font-bold">Источники ответа</h3>
                <span className="rounded-full bg-(--color-surface-2) px-2.5 py-1 text-xs font-semibold tabular-nums text-(--color-ink-soft)">
                  {sources.length}
                </span>
              </div>
              {sources.map((source) => {
                const sourceText = meaningfulSourceText(source.answer);
                return (
                  <details key={source.id} className="group overflow-hidden rounded-2xl border border-(--color-line) bg-(--color-surface)">
                    <summary className="flex min-h-11 cursor-pointer list-none items-center gap-3 px-4 py-3 focus-visible:outline-2 focus-visible:outline-offset-[-2px] focus-visible:outline-(--color-brand) [&::-webkit-details-marker]:hidden">
                      <span className="min-w-0 flex-1">
                        <span className="block text-pretty font-semibold">{source.question || "Источник без названия"}</span>
                        <span className="mt-0.5 block text-xs text-(--color-ink-soft)">
                          Версия при оценке: {new Date(source.updated_at).toLocaleString("ru")}
                        </span>
                      </span>
                      <ChevronDown size={18} aria-hidden="true" className="shrink-0 text-(--color-ink-soft) transition-transform duration-200 group-open:rotate-180" />
                    </summary>
                    <div className="border-t border-(--color-line) px-4 py-4">
                      <h4 className="mb-2 text-xs font-bold uppercase tracking-wide text-(--color-ink-soft)">
                        Текст источника при оценке
                      </h4>
                      {sourceText ? <Markdown>{sourceText}</Markdown> : (
                        <p className="text-sm text-(--color-ink-soft)">Текст источника отсутствует.</p>
                      )}
                      {isAdmin && editable && (
                        <button type="button" className="btn btn-ghost mt-4 text-xs" disabled={busy || noteLength < 3}
                          onClick={() => void editArticle(source.id)}>Исправить эту статью</button>
                      )}
                    </div>
                  </details>
                );
              })}
              {!sources.length && (
                <p className="rounded-2xl border border-dashed border-(--color-line) px-4 py-5 text-center text-xs text-(--color-ink-soft)">
                  В ответе нет статей базы знаний.
                </p>
              )}
            </section>
          </main>

          {isAdmin && (
            <aside className="space-y-4 rounded-2xl border border-(--color-line) bg-(--color-surface-2) p-4 lg:sticky lg:top-0" aria-labelledby="review-action-title">
              <div>
                <h3 id="review-action-title" className="text-base font-bold">Решение</h3>
                <p className="mt-1 text-xs leading-relaxed text-(--color-ink-soft)">
                  {STATUS_HINT[row.status] ?? "Зафиксируйте результат проверки."}
                </p>
              </div>

              {row.article_id && (
                <p className="flex items-start gap-2 rounded-xl bg-(--color-ok)/15 px-3 py-2 text-xs text-(--color-ok-ink)">
                  <Check size={15} aria-hidden="true" className="mt-0.5 shrink-0" />
                  Исправление связано со статьёй базы знаний.
                </p>
              )}

              <div>
                <label className="block text-xs font-semibold" htmlFor="review-note">Что исправить / результат проверки</label>
                <textarea id="review-note" className="input mt-2 resize-y" rows={5} maxLength={2000} value={note}
                  aria-describedby="review-note-help"
                  placeholder="Опишите правку или результат ручной проверки"
                  onChange={(e) => setNote(e.target.value)} />
                <div id="review-note-help" className="mt-1.5 flex gap-2 text-xs text-(--color-ink-soft)">
                  <span className="min-w-0 flex-1">
                    {editable && noteLength < 3
                      ? "Добавьте минимум 3 символа, чтобы изменить статью."
                      : "Пояснение сохранится вместе с решением."}
                  </span>
                  <span className="shrink-0 tabular-nums">{note.length}/2000</span>
                </div>
              </div>

              <div className="space-y-2">
                {row.status === "open" && (
                  <button type="button" className="btn btn-primary w-full text-xs" disabled={busy}
                    onClick={() => void changeStatus("in_progress")}>Взять в работу</button>
                )}
                {row.status === "fixed" && (
                  <button type="button" className="btn btn-primary w-full text-xs" disabled={busy || !note.trim()}
                    onClick={() => void changeStatus("verified")}>Подтвердить проверку</button>
                )}
                {editable && (
                  <button type="button" className="btn btn-ghost w-full text-xs" disabled={busy || noteLength < 3}
                    onClick={() => setEditing({ question: context.question ?? "", answer: "", category: "Консультации", line: 1, status: "active",
                      audience: context.audience === "customer" ? "customer" : context.audience === "supplier" ? "supplier" : "all" })}>Создать статью</button>
                )}
                {row.status !== "open" && (
                  <button type="button" className="btn btn-ghost w-full text-xs" disabled={busy}
                    onClick={() => void changeStatus("open")}>Вернуть в работу</button>
                )}
                {editable && (
                  <div className="border-t border-(--color-line) pt-2">
                    <button type="button"
                      className="btn btn-ghost w-full border-(--color-brand)/40 text-xs text-(--color-brand-ink) hover:bg-(--color-brand-soft)"
                      disabled={busy || !note.trim()} onClick={() => void changeStatus("dismissed")}>Отклонить с пояснением</button>
                  </div>
                )}
              </div>

              {error && <p role="alert" className="text-xs text-(--color-brand-ink)">{error}</p>}
            </aside>
          )}
          {!isAdmin && error && <p role="alert" className="text-xs text-(--color-brand-ink)">{error}</p>}
        </div>
      </div>
    </Modal>
  );
}
