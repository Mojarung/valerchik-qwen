import { Archive, Pencil, Trash2 } from "lucide-react";
import type { KbArticle } from "@/lib/types";
import { AUDIENCE_LABEL, plainPreview } from "./articles";
import type { KbListing } from "./useKbListing";

/** Таблица статей с отметками и листанием страниц. */
export function ArticleTable({ kb, onEdit, onArchive, onPurge }: {
  kb: KbListing;
  onEdit: (article: KbArticle) => void;
  onArchive: (id: string) => void;
  onPurge: (id: string, question: string) => void;
}) {
  const { listing, selected, pageIds, allSelected, partlySelected, page, pages } = kb;
  return (
    <div className="card overflow-x-auto">
      <table className="tabular-nums w-full text-left text-xs">
        <thead>
          <tr className="border-b border-(--color-line) text-xs text-(--color-ink-soft) uppercase">
            <th className="w-8 px-3 py-2.5">
              <input type="checkbox" className="checkbox" title="Отметить всю страницу"
                aria-label="Отметить все статьи на странице"
                checked={allSelected} disabled={pageIds.length === 0}
                // Часть страницы отмечена — показываем это состояние прочерком
                ref={(el) => { if (el) el.indeterminate = partlySelected; }}
                onChange={kb.togglePage} />
            </th>
            <th className="px-3 py-2.5">Вопрос</th>
            <th className="hidden px-3 py-2.5 md:table-cell">Категория</th>
            <th className="px-3 py-2.5">Линия</th>
            <th className="hidden px-3 py-2.5 sm:table-cell">Кому</th>
            <th className="px-3 py-2.5">Статус</th>
            <th className="hidden px-3 py-2.5 lg:table-cell">Источник</th>
            <th className="px-3 py-2.5"><span className="sr-only">Действия</span></th>
          </tr>
        </thead>
        <tbody>
          {listing?.items.map((a) => (
            <tr key={a.id}
              className={`border-b border-(--color-line) transition-colors last:border-0 hover:bg-(--color-surface-2) ${
                selected.has(a.id) ? "bg-(--color-brand-soft)" : ""}`}>
              <td className="px-3 py-2.5">
                <input type="checkbox" className="checkbox" checked={selected.has(a.id)}
                  aria-label={`Отметить статью: ${a.question}`}
                  onChange={() => kb.toggleOne(a.id)} />
              </td>
              <td className="max-w-md px-3 py-2.5">
                <div className="line-clamp-1 font-medium">{a.question}</div>
                <div className="line-clamp-1 text-xs text-(--color-ink-soft)">
                  {plainPreview(a.answer)}
                </div>
              </td>
              <td className="hidden px-3 py-2.5 md:table-cell">{a.category}</td>
              <td className="px-3 py-2.5">Л{a.line}</td>
              <td className="hidden px-3 py-2.5 sm:table-cell"><AudienceBadge audience={a.audience} /></td>
              <td className="px-3 py-2.5"><StatusBadge status={a.status} /></td>
              <td className="hidden px-3 py-2.5 text-(--color-ink-soft) lg:table-cell">
                {/* Для импортированной статьи важен не канал («import»), а файл и
                    строка: по ним видно, откуда бот взял странный ответ. */}
                {a.origin?.file ? (
                  <span title={[a.origin.file, a.origin.locator, a.origin.source]
                    .filter(Boolean).join(" · ")}>
                    <span className="block max-w-40 truncate">{a.origin.file}</span>
                    {a.origin.locator && (
                      <span className="block text-xs opacity-70">{a.origin.locator}</span>
                    )}
                  </span>
                ) : a.source}
              </td>
              <td className="px-3 py-2.5">
                <div className="flex justify-end gap-1">
                  <button className="btn btn-ghost !p-1.5" title="Редактировать" aria-label="Редактировать статью"
                    onClick={() => onEdit(a)}>
                    <Pencil size={13} />
                  </button>
                  {a.status !== "archived" && (
                    <button className="btn btn-ghost !p-1.5" title="В архив" aria-label="Отправить статью в архив"
                      onClick={() => onArchive(a.id)}>
                      <Archive size={13} />
                    </button>
                  )}
                  {/* Удаление насовсем — для мусора, который незачем держать
                      даже в архиве. Отдельная кнопка с подтверждением: архив
                      обратим, это нет. */}
                  <button className="btn btn-ghost !p-1.5 hover:text-(--color-brand-ink)"
                    title="Удалить насовсем" aria-label="Удалить статью насовсем"
                    onClick={() => onPurge(a.id, a.question)}>
                    <Trash2 size={13} />
                  </button>
                </div>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <div className="flex items-center justify-between px-3 py-2 text-xs text-(--color-ink-soft)">
        <span>Всего: {listing?.total ?? 0}</span>
        <div className="flex gap-1">
          <button className="btn btn-ghost !py-1 text-xs" disabled={page <= 1} aria-label="Предыдущая страница"
            onClick={() => kb.setPage(page - 1)}>←</button>
          <span className="px-2 py-1">{page} / {pages}</span>
          <button className="btn btn-ghost !py-1 text-xs" disabled={page >= pages} aria-label="Следующая страница"
            onClick={() => kb.setPage(page + 1)}>→</button>
        </div>
      </div>
    </div>
  );
}

function AudienceBadge({ audience }: { audience: string }) {
  const cls: Record<string, string> = {
    supplier: "bg-(--color-brand-soft) text-(--color-brand-ink)",
    customer: "bg-(--color-ok)/12 text-(--color-ok-ink)",
  };
  return (
    <span className={`rounded-full px-2 py-0.5 text-xs font-semibold ${
      cls[audience] ?? "bg-(--color-surface-2) text-(--color-ink-soft)"}`}>
      {AUDIENCE_LABEL[audience] ?? audience}
    </span>
  );
}

function StatusBadge({ status }: { status: string }) {
  const map: Record<string, [string, string]> = {
    active: ["Активна", "bg-(--color-ok)/12 text-(--color-ok-ink)"],
    draft: ["Черновик", "bg-(--color-warn)/12 text-(--color-warn-ink)"],
    archived: ["Архив", "bg-(--color-surface-2) text-(--color-ink-soft)"],
  };
  const [label, cls] = map[status] ?? [status, ""];
  return <span className={`rounded-full px-2 py-0.5 text-xs font-semibold ${cls}`}>{label}</span>;
}
