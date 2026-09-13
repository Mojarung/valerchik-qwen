import { useCallback, useState } from "react";
import { useApiData } from "@/lib/useApiData";

export interface Suggest {
  suggestions: string[];
  /** Выбранная подсказка стала текстом поля — список для неё больше не нужен. */
  hideFor: (text: string) => void;
}

/** Автодополнение вопроса: после паузы в наборе, один запрос на слово, прежний отменяется. */
export function useSuggest(input: string): Suggest {
  const [hidden, setHidden] = useState<string | null>(null);
  const path = input.trim().length >= 2 && input !== hidden
    ? `/api/chat/suggest?q=${encodeURIComponent(input)}`
    : null;
  const { data, error } = useApiData<string[]>(path, { delayMs: 200 });
  const hideFor = useCallback((text: string) => setHidden(text), []);
  // Сбой подсказок — не повод для плашки: без них просто печатают дальше.
  // Но и прежние подсказки к новому тексту показывать нельзя.
  return { suggestions: path && !error ? (data ?? []) : [], hideFor };
}
