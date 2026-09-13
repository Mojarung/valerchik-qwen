import { Archive, CheckCheck, Trash2, X } from "lucide-react";
import type { BulkAction } from "./articles";
import type { KbListing } from "./useKbListing";

/** Панель массовых действий: появляется, только когда что-то отмечено —
 *  иначе три опасные кнопки висели бы над таблицей всё время. */
export function BulkBar({ kb, onBulk }: { kb: KbListing; onBulk: (action: BulkAction) => void }) {
  const total = kb.listing?.total ?? 0;
  if (kb.selected.size === 0) return null;
  return (
    <div className="card animate-(--animate-fade-up) flex flex-wrap items-center gap-2 border-l-4 border-l-(--color-brand) p-3">
      <span className="text-xs font-semibold">
        {kb.allMatching ? `Отмечено всё по фильтру: ${total}` : `Отмечено: ${kb.selected.size}`}
      </span>
      {/* Перескок со страницы на всю выборку: «опубликовать все черновики»
          иначе пришлось бы делать по двадцать статей за раз. */}
      {kb.allSelected && total > kb.pageIds.length && (
        <button className="text-xs font-semibold text-(--color-brand-ink) underline underline-offset-2"
          onClick={kb.toggleAllMatching}>
          {kb.allMatching ? "Только эту страницу" : `Выбрать все ${total} по фильтру`}
        </button>
      )}
      <span className="flex-1" />
      <button className="btn btn-ghost text-xs" onClick={() => onBulk("publish")}>
        <CheckCheck size={13} /> Опубликовать
      </button>
      <button className="btn btn-ghost text-xs" onClick={() => onBulk("archive")}>
        <Archive size={13} /> В архив
      </button>
      <button className="btn btn-ghost text-xs hover:text-(--color-brand-ink)" onClick={() => onBulk("purge")}>
        <Trash2 size={13} /> Удалить насовсем
      </button>
      <button className="btn btn-ghost !p-1.5" title="Снять отметки" aria-label="Снять отметки"
        onClick={kb.clearSelection}>
        <X size={13} />
      </button>
    </div>
  );
}
