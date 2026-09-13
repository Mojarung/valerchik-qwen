import { useState } from "react";
import { Plus, RefreshCcw, Search, Upload } from "lucide-react";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { LoadError } from "@/components/LoadError";
import { api, errorText } from "@/lib/api";
import type { Api, KbArticle } from "@/lib/types";
import { useToast } from "@/lib/useToast";
import { ArticleEditor } from "./kb/ArticleEditor";
import { ArticleTable } from "./kb/ArticleTable";
import { BULK_DONE, EMPTY_ARTICLE, type BulkAction } from "./kb/articles";
import { BulkBar } from "./kb/BulkBar";
import { ImportJsonModal } from "./kb/ImportJsonModal";
import { useKbListing } from "./kb/useKbListing";

/** Отложенное необратимое действие: показывается в диалоге подтверждения. */
interface PendingAction {
  title: string;
  text: string;
  run: () => Promise<void>;
}

/**
 * База знаний: поиск и фильтры, таблица статей, массовые действия, редактор и
 * импорт. Выборка и отметки — в useKbListing, окна — в своих компонентах.
 */
export default function KbPage() {
  const kb = useKbListing();
  const { filters, reload } = kb;
  const [editing, setEditing] = useState<Partial<KbArticle> | null>(null);
  const [importOpen, setImportOpen] = useState(false);
  const { toast, show: notify } = useToast(3000);
  const [pending, setPending] = useState<PendingAction | null>(null);

  /** Действие над статьями: итог — тостом, отказ — тостом подольше, чтобы успели прочесть. */
  const perform = async (run: () => Promise<string>, fallback: string) => {
    try {
      notify(await run());
      reload();
    } catch (e) {
      notify(errorText(e, fallback), 8000);
    }
  };

  // Отказ пробрасывается редактору: он покажет его в окне и не закроется
  const save = async (article: Partial<KbArticle>) => {
    const payload = {
      question: article.question, answer: article.answer, category: article.category,
      keywords: article.keywords, line: article.line, status: article.status,
      audience: article.audience, images: article.images,
    };
    if (article.id) await api.put(`/api/kb/${article.id}`, payload);
    else await api.post("/api/kb", payload);
    setEditing(null);
    notify("Сохранено и переиндексировано — уже ищется в чате");
    reload();
  };

  const purge = (id: string, question: string) => setPending({
    title: "Удалить статью насовсем?",
    text: `${question}\n\nОтменить будет нельзя — в отличие от архива, откуда статью можно вернуть.`,
    run: () => perform(async () => {
      await api.delete(`/api/kb/${id}?purge=true`);
      return "Статья удалена";
    }, "Статья не удалилась"),
  });

  const archive = (id: string) => perform(async () => {
    await api.delete(`/api/kb/${id}`);
    return "Статья в архиве, из поиска убрана";
  }, "Статья не ушла в архив");

  const bulk = async (action: BulkAction, confirmed = false) => {
    if (action === "purge" && !confirmed) {
      setPending({
        title: `Удалить насовсем статей: ${kb.selectionCount}?`,
        text: "Отменить будет нельзя — в отличие от архива, откуда статьи можно вернуть.",
        run: () => bulk(action, true),
      });
      return;
    }
    // Отметки снимаются только после успеха: при отказе действие можно
    // повторить, не выбирая статьи заново
    await perform(async () => {
      const res = await api.post<Api["BulkResult"]>("/api/kb/bulk", { action, ...kb.bulkTarget() });
      kb.clearSelection();
      return BULK_DONE[action](res.affected);
    }, "Действие над статьями не выполнилось");
  };

  const reindex = () => perform(async () => {
    await api.post("/api/admin/reindex");
    return "Переиндексация запущена (blue/green, без простоя)";
  }, "Переиндексация не запустилась");

  return (
    <div className="space-y-3">
      {kb.error && <LoadError message={`Статьи не загрузились: ${kb.error}`} onRetry={reload} />}
      <div className="card flex flex-wrap items-center gap-2 p-3">
        <div className="relative min-w-52 flex-1">
          <Search size={14} className="absolute top-3 left-3 text-(--color-ink-soft)" />
          <input className="input !pl-8" placeholder="Поиск по вопросам и ответам…" value={filters.query}
            type="search" aria-label="Поиск по базе знаний"
            onChange={(e) => kb.setQuery(e.target.value)} />
        </div>
        <select className="input w-auto" value={filters.status} aria-label="Фильтр по статусу статьи"
          onChange={(e) => kb.setStatus(e.target.value)}>
          <option value="">Все статусы</option>
          <option value="active">Активные</option>
          <option value="draft">Черновики</option>
          <option value="archived">Архив</option>
        </select>
        <select className="input w-auto" value={filters.audience} title="Кому адресована статья"
          aria-label="Фильтр по роли, которой адресована статья"
          onChange={(e) => kb.setAudience(e.target.value)}>
          <option value="">Любая роль</option>
          <option value="all">Для всех</option>
          <option value="supplier">Поставщику</option>
          <option value="customer">Заказчику</option>
        </select>
        <button className="btn btn-ghost text-xs" onClick={() => void reindex()}>
          <RefreshCcw size={13} /> Переиндексировать
        </button>
        <button className="btn btn-ghost text-xs" onClick={() => setImportOpen(true)}>
          <Upload size={13} /> Импорт JSON
        </button>
        <button className="btn btn-primary text-xs" onClick={() => setEditing({ ...EMPTY_ARTICLE })}>
          <Plus size={13} /> Новая статья
        </button>
      </div>

      {toast && (
        <div className="card animate-(--animate-fade-up) border-l-4 border-l-(--color-ok) px-3 py-2 text-xs">
          {toast}
        </div>
      )}

      <BulkBar kb={kb} onBulk={(action) => void bulk(action)} />

      <ArticleTable kb={kb} onEdit={(article) => setEditing({ ...article })}
        onArchive={(id) => void archive(id)} onPurge={purge} />

      {editing && <ArticleEditor initial={editing} onSave={save} onClose={() => setEditing(null)} />}

      {importOpen && (
        <ImportJsonModal onClose={() => setImportOpen(false)}
          onImported={(count) => {
            notify(`Импортировано статей: ${count}`);
            setImportOpen(false);
            reload();
          }} />
      )}

      <ConfirmDialog
        open={pending !== null}
        title={pending?.title ?? ""}
        text={pending?.text ?? ""}
        confirmLabel="Удалить насовсем"
        danger
        onCancel={() => setPending(null)}
        onConfirm={() => {
          const action = pending;
          setPending(null);
          void action?.run();
        }}
      />
    </div>
  );
}
