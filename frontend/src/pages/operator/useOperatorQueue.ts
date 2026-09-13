import { useCallback, useEffect, useRef, useState } from "react";
import type { OperatorSession } from "@/lib/types";
import { useApiData } from "@/lib/useApiData";

export type QueueFilter = "queue" | "all" | "waiting_operator" | "operator_assigned" | "operator_active" | "waiting_user" | "resolved";

export interface OperatorQueue {
  filter: QueueFilter;
  setFilter: (filter: QueueFilter) => void;
  sessions: OperatorSession[];
  error: string | null;
  /** Перезапросить сейчас: после своего действия (взял, завершил). */
  reload: () => void;
  /** Перезапросить с паузой: события сокета приходят пачками. */
  scheduleReload: () => void;
  /** Ожидание «на сейчас»: серверное значение плюс время с загрузки списка. */
  waitingOf: (session: OperatorSession) => number | null;
}

/** Пауза, за которую пачка событий диспетчера склеивается в один перезапрос. */
const RELOAD_DEBOUNCE_MS = 300;
/** Как часто перерисовывать время ожидания. */
const CLOCK_TICK_MS = 15_000;

/**
 * Очередь обращений. Адрес зависит от фильтра, и перезапрос из события сокета
 * берёт текущий фильтр, а не замороженный в замыкании подключения — иначе
 * любое событие молча схлопывало «Все» обратно в очередь.
 */
export function useOperatorQueue(): OperatorQueue {
  const [filter, setFilter] = useState<QueueFilter>("queue");
  const { data, error, reload, loadedAt } = useApiData<OperatorSession[]>(`/api/operator/sessions?status=${filter}`);
  const reloadTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  /** Один проход диспетчера рассылает до двадцати событий подряд, и каждое
   *  дёргало полный список у каждого оператора: пачка назначений превращалась в
   *  сотни запросов. Склеить состояние из события нельзя — в нём только тип и
   *  session_id, — поэтому запрос неизбежен, но одного на пачку достаточно. */
  const scheduleReload = useCallback(() => {
    if (reloadTimer.current) clearTimeout(reloadTimer.current);
    reloadTimer.current = setTimeout(reload, RELOAD_DEBOUNCE_MS);
  }, [reload]);

  useEffect(() => () => {
    if (reloadTimer.current) clearTimeout(reloadTimer.current);
  }, []);

  // Время ожидания приходит с сервера одним числом на момент запроса. Чтобы оно
  // росло на глазах, а не замирало между обновлениями списка, помним момент
  // загрузки и раз в пятнадцать секунд перерисовываем: пересчёт на клиенте
  // дешевле, чем дёргать очередь ради одной изменившейся цифры.
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), CLOCK_TICK_MS);
    return () => clearInterval(id);
  }, []);
  useEffect(() => {
    const timer = setInterval(reload, 10000);
    return () => clearInterval(timer);
  }, [reload]);

  // Часы бывают старше самой загрузки — отсюда max(0, …): иначе только что
  // пришедший список несколько секунд «молодел» бы на глазах.
  const waitingOf = useCallback((s: OperatorSession): number | null => (
    s.waiting_seconds === null
      ? null
      : s.waiting_seconds + Math.floor(Math.max(0, now - (loadedAt ?? now)) / 1000)
  ), [now, loadedAt]);

  return { filter, setFilter, sessions: data ?? [], error, reload, scheduleReload, waitingOf };
}
