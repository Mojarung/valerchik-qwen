import { useCallback, useState } from "react";
import type { Api } from "@/lib/types";
import { useApiData } from "@/lib/useApiData";

export type Listing = Api["ArticlePage"];

export interface KbFilters {
  query: string;
  status: string;
  audience: string;
}

export interface KbListing {
  filters: KbFilters;
  setQuery: (query: string) => void;
  setStatus: (status: string) => void;
  setAudience: (audience: string) => void;
  page: number;
  setPage: (page: number) => void;
  pages: number;
  listing: Listing | null;
  error: string | null;
  reload: () => void;
  selected: Set<string>;
  /** Отмечено «всё по фильтру», а не только видимая страница. */
  allMatching: boolean;
  pageIds: string[];
  allSelected: boolean;
  /** Часть страницы отмечена — чекбокс в шапке показывает прочерк. */
  partlySelected: boolean;
  /** Сколько статей затронет массовое действие. */
  selectionCount: number;
  toggleOne: (id: string) => void;
  togglePage: () => void;
  toggleAllMatching: () => void;
  clearSelection: () => void;
  /** Кому адресовать массовое действие: перечнем id или всем фильтром. */
  bulkTarget: () => { ids: string[] } | { filter: KbFilters };
}

/** Статей на странице — столько же отдаёт сервер. */
const PAGE_SIZE = 20;

/**
 * Выборка статей базы знаний: фильтры, страницы и отметки для массовых действий.
 * Отметки живут в пределах одной выборки: после смены фильтра или страницы
 * отмеченного на экране не видно, а действие ушло бы по невидимым статьям.
 */
export function useKbListing(): KbListing {
  const [filters, setFilters] = useState<KbFilters>({ query: "", status: "", audience: "" });
  const [page, setPageState] = useState(1);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  // Черновиков после разбора методички бывает больше, чем влезает в таблицу,
  // поэтому отметить можно и всю выборку, а не только страницу.
  const [allMatching, setAllMatching] = useState(false);

  // Поиск по мере набора: запрос уходит после паузы, а смена фильтра отменяет
  // прежний — ответ на старую выборку не перепишет уже показанную новую.
  const params = new URLSearchParams({ page: String(page), ...filters });
  const { data: listing, error, reload } = useApiData<Listing>(`/api/kb?${params}`, { delayMs: 250 });

  const clearSelection = useCallback(() => {
    setSelected(new Set());
    setAllMatching(false);
  }, []);

  const setFilter = (patch: Partial<KbFilters>) => {
    setFilters((current) => ({ ...current, ...patch }));
    setPageState(1);
    clearSelection();
  };
  const setPage = (next: number) => {
    setPageState(next);
    clearSelection();
  };

  const pageIds = listing?.items.map((a) => a.id) ?? [];
  const allSelected = pageIds.length > 0 && pageIds.every((id) => selected.has(id));
  const partlySelected = !allSelected && pageIds.some((id) => selected.has(id));

  const toggleOne = (id: string) => {
    // Снятие одной галочки выводит из режима «всё по фильтру»: иначе действие
    // ушло бы по статьям, которые администратор только что исключил.
    setAllMatching(false);
    setSelected((prev) => {
      const next = new Set(prev);
      if (!next.delete(id)) next.add(id);
      return next;
    });
  };
  const togglePage = () => {
    setSelected(allSelected ? new Set() : new Set(pageIds));
    setAllMatching(false);
  };

  return {
    filters,
    setQuery: (query) => setFilter({ query }),
    setStatus: (status) => setFilter({ status }),
    setAudience: (audience) => setFilter({ audience }),
    page, setPage,
    pages: listing ? Math.max(1, Math.ceil(listing.total / PAGE_SIZE)) : 1,
    listing, error, reload, selected, allMatching, pageIds, allSelected, partlySelected,
    selectionCount: allMatching ? (listing?.total ?? 0) : selected.size,
    toggleOne, togglePage,
    toggleAllMatching: () => setAllMatching((value) => !value),
    clearSelection,
    // По фильтру — когда отмечено «всё»: перечень id ограничен страницей,
    // а под фильтр может попадать несколько сотен статей.
    bulkTarget: () => (allMatching ? { filter: { ...filters } } : { ids: [...selected] }),
  };
}
