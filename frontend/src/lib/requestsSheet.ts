import { create } from "zustand";

/**
 * «Мои обращения» вне страницы чата: кнопка живёт в навигации сайта (или в подвале
 * виджета), а список, модалка и боковая панель — на странице чата. Стор их
 * связывает: кнопка открывает модалку, переключатель сворачивает панель, оба показывают
 * счётчик, который считает чат.
 */
interface RequestsSheetState {
  open: boolean;
  /** Обращений, которые ещё не завершены. */
  openCount: number;
  /** В каком-то обращении есть ответ, который человек ещё не видел. */
  unread: boolean;
  /** Боковая панель свёрнута: по умолчанию да, пока человек её не развернул. */
  panelCollapsed: boolean;
  setOpen: (open: boolean) => void;
  setSummary: (openCount: number, unread: boolean) => void;
  setPanelCollapsed: (collapsed: boolean) => void;
}

/** Выбор развернуть панель помнится между визитами: это привычка человека, а не
 *  состояние одного диалога. Хранилище недоступно — выбор живёт до перезагрузки. */
const PANEL_KEY = "valerchik-requests-panel";

/** По умолчанию панель свёрнута: страница открывается на переписке, а список
 *  разворачивает тот, кому он нужен. */
function readCollapsed(): boolean {
  try {
    return localStorage.getItem(PANEL_KEY) !== "expanded";
  } catch {
    return true;
  }
}

function writeCollapsed(collapsed: boolean): void {
  try {
    if (collapsed) localStorage.removeItem(PANEL_KEY);
    else localStorage.setItem(PANEL_KEY, "expanded");
  } catch {
    /* приватный режим: выбор проживёт до перезагрузки */
  }
}

export const useRequestsSheet = create<RequestsSheetState>()((set) => ({
  open: false,
  openCount: 0,
  unread: false,
  panelCollapsed: readCollapsed(),
  setOpen: (open) => set({ open }),
  setSummary: (openCount, unread) => set({ openCount, unread }),
  setPanelCollapsed: (panelCollapsed) => {
    writeCollapsed(panelCollapsed);
    set({ panelCollapsed });
  },
}));
