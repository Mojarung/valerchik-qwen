import { useState } from "react";
import { Check, X } from "lucide-react";
import { LoadError } from "@/components/LoadError";
import { Markdown } from "@/components/Markdown";
import { Modal } from "@/components/Modal";
import { api, errorText } from "@/lib/api";
import { useApiData } from "@/lib/useApiData";
import { REVIEW_LABEL, ROLE_LABEL, type ConflictGroup, type SourceSnapshot } from "./types";

function sourceName(source: SourceSnapshot) {
  const file = typeof source.origin.file === "string" ? source.origin.file : "";
  const locator = typeof source.origin.locator === "string" ? source.origin.locator : "";
  return [file || source.category || "Статья базы знаний", locator].filter(Boolean).join(" · ");
}

function AnswerChoice({ source, quote, selected, canSelect, onSelect }: {
  source: SourceSnapshot; quote: string; selected: boolean; canSelect: boolean; onSelect: () => void;
}) {
  return (
    <section className={`min-w-0 rounded-xl border p-3 text-xs transition-[border-color,background-color] ${selected ? "border-(--color-brand) bg-(--color-brand-soft)" : "border-(--color-line)"}`}>
      <label className={canSelect ? "block cursor-pointer" : "block"}>
        <span className="flex items-start gap-2">
          {canSelect && (
            <input
              type="radio"
              name="preferred-article"
              value={source.article_id}
              checked={selected}
              onChange={onSelect}
              className="mt-0.5 accent-(--color-brand)"
            />
          )}
          <span className="min-w-0">
            <span className="block text-(--color-ink-soft)">{sourceName(source)} · {ROLE_LABEL[source.audience] ?? "Роль не указана"}</span>
            <strong className="mt-1 block text-sm leading-snug">{source.question}</strong>
          </span>
        </span>
        <blockquote className="mt-3 rounded-lg bg-(--color-surface-2) p-3 font-medium leading-relaxed">
          <Markdown>{quote || "Цитата не выделена"}</Markdown>
        </blockquote>
      </label>
      <details className="mt-3">
        <summary className="w-fit cursor-pointer font-medium text-(--color-brand-ink)">Вся статья</summary>
        <div className="mt-2 break-words leading-relaxed">
          <Markdown>{source.answer}</Markdown>
        </div>
      </details>
    </section>
  );
}

function ReviewBody({ group, canEdit, onSaved }: { group: ConflictGroup; canEdit: boolean; onSaved: () => void }) {
  const [selectedId, setSelectedId] = useState(group.preferred_article_id ?? "");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const save = async (dismissed = false) => {
    if (saving || (!dismissed && !selectedId)) return;
    setSaving(true);
    setError("");
    try {
      await api.patch(`/api/source-conflicts/groups/${encodeURIComponent(group.id)}/review`, {
        status: dismissed ? "dismissed" : "confirmed",
        preferred_article_id: dismissed ? null : selectedId,
        note: "",
      });
      onSaved();
    } catch (e) {
      setError(errorText(e));
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="space-y-4">
      <div>
        <p className="text-xs text-(--color-ink-soft)">{REVIEW_LABEL[group.review_status ?? "unreviewed"]}</p>
        <h3 className="mt-1 text-base font-semibold">Какой ответ оставить?</h3>
        <p className="mt-1 text-sm">{group.question}</p>
        {group.fact && <p className="mt-1 text-xs text-(--color-ink-soft)">{group.fact}</p>}
      </div>
      <div className="grid gap-3 sm:grid-cols-2">
        {group.sources.map(({ source, quote }) => (
          <AnswerChoice key={source.id} source={source} quote={quote ?? ""} selected={selectedId === source.article_id}
            canSelect={canEdit} onSelect={() => setSelectedId(source.article_id)} />
        ))}
      </div>
      <details className="text-xs">
        <summary className="w-fit cursor-pointer text-(--color-ink-soft)">Почему найдено расхождение</summary>
        <div className="mt-2 leading-relaxed"><Markdown>{group.explanation}</Markdown></div>
      </details>
      {error && <p role="alert" className="text-xs text-(--color-brand-ink)">{error}</p>}
      {canEdit && (
        <div className="flex flex-wrap items-center gap-2 border-t border-(--color-line) pt-3">
          <button type="button" className="btn btn-primary min-h-10 text-xs" disabled={saving || !selectedId}
            onClick={() => void save()}>
            <Check size={14} aria-hidden /> {saving ? "Сохраняем…" : "Оставить выбранный ответ"}
          </button>
          <button type="button" className="btn btn-ghost min-h-10 text-xs" disabled={saving}
            onClick={() => void save(true)}>Это не противоречие</button>
        </div>
      )}
    </div>
  );
}

export function ConflictReview({ id, canEdit, onClose, onSaved }: { id: string; canEdit: boolean; onClose: () => void; onSaved: () => void }) {
  const { data, error, reload } = useApiData<ConflictGroup>(`/api/source-conflicts/groups/${encodeURIComponent(id)}`);
  return (
    <Modal title="Решение по расхождению" size="lg" onClose={onClose}>
      <button type="button" className="btn btn-ghost mb-3 min-h-10 self-end text-xs" onClick={onClose}>
        <X size={14} aria-hidden /> Закрыть
      </button>
      {error && <LoadError message={error} onRetry={reload} />}
      {!data && !error && <p role="status" className="text-xs">Загружаем статьи…</p>}
      {data && <ReviewBody key={`${data.id}:${data.reviewed_at ?? ""}`} group={data} canEdit={canEdit} onSaved={onSaved} />}
    </Modal>
  );
}
