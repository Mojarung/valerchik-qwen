import { useCallback, useEffect, useRef, useState } from "react";
import { api, isAbort } from "@/lib/api";

export interface ApiData<T> {
  data: T | null;
  /** Текст ошибки последней загрузки; null — последняя удалась. */
  error: string | null;
  loading: boolean;
  /** Когда пришли текущие данные, мс от эпохи; null — ещё не приходили. Ставится
   *  в одном обновлении с данными: часы очереди не видят кадра «список новый, время старое». */
  loadedAt: number | null;
  /** Загрузить заново: кнопка «Повторить» и обновление после правки. */
  reload: () => void;
}

export interface ApiDataOptions<T> {
  /** Пауза перед запросом: поиск по мере набора не шлёт запрос на каждую букву. */
  delayMs?: number;
  /** Переспросить через столько мс после ответа; null — не опрашивать. */
  pollMs?: number | null;
  /** Опрашивать, только пока условие держится: идёт расчёт, прогон, импорт. */
  pollWhile?: (data: T | null) => boolean;
}

/**
 * Данные страницы с сервера: ошибка видна и повторяема, смена адреса отменяет
 * прежний запрос. Опоздавший ответ на старый фильтр не перезаписывает новый,
 * даже если сам запрос отменить не удалось: результат отменённой загрузки
 * просто не записывается. null вместо адреса — ничего не грузить.
 */
export function useApiData<T>(
  path: string | null,
  { delayMs = 0, pollMs = null, pollWhile }: ApiDataOptions<T> = {},
): ApiData<T> {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loadedAt, setLoadedAt] = useState<number | null>(null);
  const [loading, setLoading] = useState(path !== null);
  const [version, setVersion] = useState(0);
  const reload = useCallback(() => setVersion((v) => v + 1), []);
  // Условие опроса страница передаёт новой функцией на каждый рендер: держим
  // последнюю в ref, чтобы таймер не перезапускался от каждой перерисовки.
  const pollWhileRef = useRef(pollWhile);
  useEffect(() => {
    pollWhileRef.current = pollWhile;
  });

  useEffect(() => {
    if (path === null) return;
    const controller = new AbortController();
    const run = async () => {
      setLoading(true);
      try {
        const value = await api.get<T>(path, { signal: controller.signal });
        if (controller.signal.aborted) return;
        setData(value);
        setLoadedAt(Date.now());
        setError(null);
      } catch (e) {
        if (controller.signal.aborted || isAbort(e)) return;
        setError(e instanceof Error ? e.message : "Не удалось загрузить данные");
      } finally {
        if (!controller.signal.aborted) setLoading(false);
      }
    };
    const timer = delayMs > 0 ? setTimeout(() => void run(), delayMs) : null;
    if (timer === null) void run();
    return () => {
      if (timer !== null) clearTimeout(timer);
      controller.abort();
    };
  }, [path, version, delayMs]);

  useEffect(() => {
    if (pollMs === null || loading) return;
    if (pollWhileRef.current && !pollWhileRef.current(data)) return;
    const timer = setTimeout(reload, pollMs);
    return () => clearTimeout(timer);
  }, [pollMs, loading, reload, data]);

  // Без адреса грузить нечего: запрос, оборванный сменой адреса на null, флаг
  // сам не снимает, и «загрузка» висела бы до следующего адреса.
  return { data, error, loading: path !== null && loading, loadedAt, reload };
}
