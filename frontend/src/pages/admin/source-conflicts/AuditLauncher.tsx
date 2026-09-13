import { useState } from "react";
import { Search } from "lucide-react";
import { LoadError } from "@/components/LoadError";
import { api, errorText } from "@/lib/api";
import type { Api, KbArticle } from "@/lib/types";
import { useApiData } from "@/lib/useApiData";
import { ROLE_LABEL, type AuditJob } from "./types";

const MAX_ARTICLES = 20;
const PAGE_SIZE = 20;

export function AuditLauncher({ onStarted }: { onStarted: (job: AuditJob) => void }) {
  const [query, setQuery] = useState("");
  const [page, setPage] = useState(1);
  const [selected, setSelected] = useState<KbArticle[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [calls, setCalls] = useState(20);
  const [pairs, setPairs] = useState(30);
  const params = new URLSearchParams({ query, page: String(page), status: "active" });
  const listing = useApiData<Api["ArticlePage"]>(`/api/kb?${params}`, { delayMs: 250 });
  const selectedIds = new Set(selected.map((article) => article.id));
  const toggle = (article: KbArticle) => setSelected((current) => selectedIds.has(article.id)
    ? current.filter((item) => item.id !== article.id) : current.length < MAX_ARTICLES ? [...current, article] : current);
  const validLimits = Number.isInteger(calls) && calls >= 1 && calls <= 100 && Number.isInteger(pairs) && pairs >= 1 && pairs <= 100;
  const start = async () => {
    if (busy || selected.length < 2 || !validLimits) return;
    setBusy(true); setError("");
    try {
      const job = await api.post<AuditJob>("/api/source-conflicts/jobs", {
        article_ids: selected.map((article) => article.id),
        limits: { max_articles: MAX_ARTICLES, max_pairs: pairs, max_calls: calls,
          top_k: 3, max_article_chars: 8000, max_output_tokens: 4000,
          timeout_seconds: 1800, request_timeout_seconds: 120 },
      });
      onStarted(job);
    } catch (e) { setError(errorText(e, "Проверку не удалось запустить")); }
    finally { setBusy(false); }
  };
  return (
    <section className="card space-y-3 p-4">
      <h2 className="text-sm font-semibold">Выберите статьи для проверки</h2>
      <p className="text-xs text-(--color-ink-soft)">Отметьте от 2 до 20 опубликованных ответов на близкую тему. Проверка сравнит их между собой и сохранит расхождения.</p>
      <label className="flex items-center gap-2 text-xs"><Search size={15} /><span className="sr-only">Поиск статей</span>
        <input className="input w-full" value={query} placeholder="Найти по вопросу или теме" onChange={(e) => { setQuery(e.target.value); setPage(1); }} />
      </label>
      {listing.error && <LoadError message={listing.error} onRetry={listing.reload} />}
      <div className="max-h-64 overflow-y-auto rounded-xl border border-(--color-line)">
        {listing.loading && !listing.data && <p role="status" className="p-3 text-xs">Загружаем статьи…</p>}
        {listing.data?.items.map((article) => {
          const eligible = !article.origin?.kind || article.origin.kind === "answer";
          const provenance = [article.origin?.file, article.origin?.locator].filter(Boolean).join(" · ");
          const preview = article.answer.replace(/\s+/g, " ").slice(0, 180);
          return <label key={article.id} aria-label={[article.question, provenance, preview].filter(Boolean).join(". ")} className="flex cursor-pointer gap-3 border-b border-(--color-line) p-3 last:border-0 hover:bg-(--color-surface-2)">
            <input type="checkbox" className="mt-0.5 shrink-0" checked={selectedIds.has(article.id)}
              disabled={!eligible || (!selectedIds.has(article.id) && selected.length >= MAX_ARTICLES)} onChange={() => toggle(article)} />
            <span className="min-w-0 text-xs"><span className="block font-medium">{article.question}</span>
              {provenance && <span className="mt-1 block break-words text-(--color-ink-soft)">{provenance}</span>}
              <span className="mt-1 line-clamp-2 leading-relaxed">{preview}</span>
              <span className="mt-1 block text-(--color-ink-soft)">{article.category} · {ROLE_LABEL[article.audience]}{!eligible && " · не является общим правилом"}</span>
            </span>
          </label>;
        })}
        {listing.data?.items.length === 0 && <p className="p-4 text-xs text-(--color-ink-soft)">Статей не найдено. Измените поисковый запрос.</p>}
      </div>
      <div className="flex flex-wrap items-center gap-2 text-xs">
        <span className="mr-auto">Выбрано {selected.length} из {MAX_ARTICLES}</span>
        {selected.length > 0 && <button type="button" className="btn btn-ghost !py-1 text-xs" onClick={() => setSelected([])}>Снять выбор</button>}
        <button type="button" className="btn btn-ghost !py-1 text-xs" disabled={page === 1} onClick={() => setPage(page - 1)}>Назад</button>
        <span>Страница {page}</span>
        <button type="button" className="btn btn-ghost !py-1 text-xs" disabled={!listing.data || page * PAGE_SIZE >= listing.data.total} onClick={() => setPage(page + 1)}>Далее</button>
      </div>
      {selected.length > 0 && <details className="text-xs"><summary className="cursor-pointer">Выбранные статьи, включая другие страницы</summary>
        <ul className="mt-2 space-y-1">{selected.map((article) => <li key={article.id} className="flex items-center gap-2"><span className="mr-auto">{article.question}</span>
          <button type="button" className="btn btn-ghost !py-1 text-xs" aria-label={`Убрать ${article.question}`} onClick={() => toggle(article)}>Убрать</button></li>)}</ul>
      </details>}
      <details className="text-xs"><summary className="cursor-pointer font-medium">Ограничения проверки</summary>
        <div className="mt-3 grid gap-3 sm:grid-cols-2">
          <label>Не более сравнений<input className="input mt-1 w-full" type="number" min={1} max={100} value={pairs} onChange={(e) => setPairs(e.target.valueAsNumber)} /></label>
          <label>Не более запросов к модели<input className="input mt-1 w-full" type="number" min={1} max={100} value={calls} onChange={(e) => setCalls(e.target.valueAsNumber)} /></label>
        </div>
        <p className="mt-2 text-(--color-ink-soft)">До 2 минут за проход. Длинные статьи пропускаются. Запросы используют настроенную модель и могут расходовать её оплачиваемый лимит.</p>
      </details>
      {error && <p role="alert" className="text-xs text-(--color-brand-ink)">{error}</p>}
      <button type="button" className="btn btn-primary text-xs" disabled={busy || selected.length < 2 || !validLimits} onClick={() => void start()}>
        {busy ? "Запускаем…" : "Проверить выбранные статьи"}
      </button>
    </section>
  );
}
