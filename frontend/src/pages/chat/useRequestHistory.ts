import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, errorText } from "@/lib/api";
import {
  readRequests, REQUESTS_CHANGED, REQUESTS_KEY, type RequestsChange, type StoredRequest,
} from "@/lib/requestHistory";
import type { Api } from "@/lib/types";

type Summary = Api["SessionSummary"];

/** Карточка обращения и то, что знает о нём только браузер. */
export type RequestCard = Summary & {
  owner: string;
  /** После последнего визита в обращении ответил кто-то, кроме самого человека. */
  unread: boolean;
};

/** Пока обращение у специалиста, его ответ может прийти в любой момент — и в
 *  другом диалоге, не в открытом сейчас. Чаще опрашивать незачем: это не чат. */
export const POLL_MS = 30_000;
/** Тишина перед запросом: переключение обращения растягивает поводы на два запроса
 *  подряд (статус диалога, затем история), и каждый повод переносит запрос сюда. */
export const QUIET_MS = 400;
/** Дольше поводы копить не даём: непрерывный поток (соседняя вкладка пишет пары)
 *  иначе откладывал бы запрос бесконечно. */
export const MAX_WAIT_MS = 2_000;
const IN_WORK = new Set(["waiting_operator", "operator_assigned", "operator_active", "waiting_user"]);

export function isOpen(status: string): boolean {
  return status !== "resolved" && status !== "closed_violation";
}

function isUnread(card: Summary, stored: StoredRequest, currentId: string | null): boolean {
  if (card.unread_count !== undefined) return card.unread_count > 0;
  if (card.session_id === currentId || card.last_message_role === "user") return false;
  // Ни разу не открывали при списке — ответ оператора точно не видели, а реплику
  // бота человек читал в том же диалоге, где спрашивал
  if (!stored.seenAt) return card.last_message_role === "operator";
  return Date.parse(card.updated_at) > Date.parse(stored.seenAt);
}

/** Карточки сервера + пары браузера. Чужие для этого браузера карточки выпадают. */
function withLocalState(cards: Summary[], stored: StoredRequest[], currentId: string | null): RequestCard[] {
  const byId = new Map(stored.map((s) => [s.sessionId, s]));
  return cards.flatMap((card) => {
    const own = byId.get(card.session_id);
    return own ? [{ ...card, owner: own.owner, unread: isUnread(card, own, currentId) }] : [];
  });
}

/**
 * Список «Моих обращений» с сервера по парам, накопленным браузером.
 *
 * С сервера перечитывается при монтировании (сразу), а дальше — когда меняется
 * состав списка (эта вкладка или соседняя), когда у открытого диалога сменилось
 * состояние (`refreshKey`), когда человек вернулся на вкладку, и раз в POLL_MS,
 * пока хоть одно обращение у специалиста. Эти поводы ждут QUIET_MS тишины и
 * сливаются в один запрос. Отметка «просмотрено» запроса не делает —
 * пересчитывается на месте. `refresh` (кнопка «Повторить») идёт без ожидания.
 */
export function useRequestHistory({ currentId, refreshKey }: { currentId: string | null; refreshKey: string }) {
  const [cards, setCards] = useState<Summary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  // Снимок пар браузера: отметка «просмотрено» меняет только его, карточки
  // с сервера остаются прежними, а пометка «новый ответ» пересчитывается
  const [stored, setStored] = useState(readRequests);
  const items = useMemo(() => withLocalState(cards, stored, currentId), [cards, stored, currentId]);
  // Переключение обращения даёт пачку поводов за несколько сотен миллисекунд
  // (сбросился экран, пришёл статус, догрузилась история и пары), и по запросу на
  // повод список выбирал минутный лимит сервера — дальше 429 и замершие карточки.
  // Поэтому поводы сначала ждут тишины (schedule), а сам запрос — один в полёте и
  // не больше одного догоняющего. Любой запуск читает пары заново, так что
  // последнее изменение не теряется.
  const inFlight = useRef(false);
  const again = useRef(false);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const pendingSince = useRef<number | null>(null);
  const alive = useRef(true);

  const refresh = useCallback(async () => {
    // Запуск читает пары после всех поводов до него — отложенный больше не нужен
    if (timer.current !== null) clearTimeout(timer.current);
    timer.current = null;
    pendingSince.current = null;
    if (inFlight.current) {
      again.current = true;
      return;
    }
    inFlight.current = true;
    try {
      do {
        again.current = false;
        const pairs = readRequests();
        setStored(pairs);
        if (!pairs.length) {
          setCards([]); setError(null);
          continue;
        }
        try {
          const fresh = await api.post<Summary[]>("/api/chat/sessions/history", {
            sessions: pairs.map(({ sessionId, owner }) => ({ session_id: sessionId, owner })),
          });
          setCards(Array.isArray(fresh) ? fresh : []);
          setError(null);
        } catch (e) {
          setError(errorText(e, "Не удалось загрузить обращения"));
        }
      } while (again.current && alive.current);
    } finally {
      inFlight.current = false;
      again.current = false;
      setLoading(false);
    }
  }, []);

  /** Повод обновиться: запрос уйдёт, когда поводы стихнут на QUIET_MS, но не
   *  позже MAX_WAIT_MS от первого из них. */
  const schedule = useCallback(() => {
    const now = Date.now();
    pendingSince.current ??= now;
    const delay = Math.max(0, Math.min(QUIET_MS, pendingSince.current + MAX_WAIT_MS - now));
    if (timer.current !== null) clearTimeout(timer.current);
    timer.current = setTimeout(() => { void refresh(); }, delay);
  }, [refresh]);

  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
      if (timer.current !== null) clearTimeout(timer.current);
      timer.current = null;
      pendingSince.current = null;
    };
  }, []);

  // Первая загрузка — без ожидания: список пуст, пока не придёт ответ
  useEffect(() => {
    void refresh();
  }, [refresh]);

  const lastKey = useRef(refreshKey);
  useEffect(() => {
    if (lastKey.current === refreshKey) return;
    lastKey.current = refreshKey;
    schedule();
  }, [schedule, refreshKey]);

  useEffect(() => {
    const onStorage = (e: StorageEvent) => { if (e.key === REQUESTS_KEY) schedule(); };
    const onChanged = (e: Event) => {
      if ((e as CustomEvent<RequestsChange>).detail === "seen") setStored(readRequests());
      else schedule();
    };
    const onVisible = () => { if (document.visibilityState === "visible") schedule(); };
    window.addEventListener("storage", onStorage);
    window.addEventListener(REQUESTS_CHANGED, onChanged);
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      window.removeEventListener("storage", onStorage);
      window.removeEventListener(REQUESTS_CHANGED, onChanged);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [schedule]);

  const inWork = cards.some((card) => IN_WORK.has(card.status));
  useEffect(() => {
    if (!inWork) return;
    const poll = setInterval(() => {
      if (document.visibilityState === "visible") schedule();
    }, POLL_MS);
    return () => clearInterval(poll);
  }, [inWork, schedule]);

  return { items, loading, error, refresh };
}
