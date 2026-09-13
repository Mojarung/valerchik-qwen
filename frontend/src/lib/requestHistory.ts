/**
 * «Мои обращения»: какие диалоги этот браузер открывал.
 *
 * Учётных записей у чата нет, поэтому список живёт у посетителя: пара «id
 * диалога + токен владельца» на каждое обращение. Сервер по ним отдаёт карточки
 * и выкидывает чужие и поддельные пары (POST /api/chat/sessions/history).
 *
 * localStorage, а не кука, как у текущего диалога (lib/chatSession.ts): полсотни
 * пар — это килобайты, а кука уезжает на сервер с каждым запросом, включая
 * картинки. Потерять список не страшно — текущий диалог по-прежнему в куке, и
 * без хранилища (приватный режим) чат работает, просто список пуст.
 */

export const REQUESTS_KEY = "valerchik-requests";
/** Событие окна: список поменялся в этой вкладке. Из соседних придёт `storage`. */
export const REQUESTS_CHANGED = "valerchik-requests-changed";
/** Больше человеку не пролистать, а хранилище и тело запроса не резиновые. */
export const MAX_REQUESTS = 50;

const HEX32 = /^[a-f0-9]{32}$/;

export interface StoredRequest {
  sessionId: string;
  owner: string;
  /** Время последней реплики, которую человек видел (часы сервера); пусто — не открывал. */
  seenAt: string;
}

function isStored(value: unknown): value is StoredRequest {
  const item = value as StoredRequest | null;
  return typeof item === "object" && item !== null
    && HEX32.test(String(item.sessionId)) && HEX32.test(String(item.owner))
    && typeof item.seenAt === "string";
}

export function readRequests(): StoredRequest[] {
  try {
    const parsed: unknown = JSON.parse(localStorage.getItem(REQUESTS_KEY) ?? "[]");
    return Array.isArray(parsed) ? parsed.filter(isStored).slice(0, MAX_REQUESTS) : [];
  } catch {
    return [];
  }
}

/** Что поменялось: состав списка (нужны карточки с сервера) или только отметка
 *  «просмотрено» (пометку «новый ответ» пересчитать на месте, без запроса). */
export type RequestsChange = "list" | "seen";

function write(items: StoredRequest[], change: RequestsChange): void {
  try {
    localStorage.setItem(REQUESTS_KEY, JSON.stringify(items.slice(0, MAX_REQUESTS)));
  } catch {
    return; // хранилище недоступно: список проживёт до перезагрузки в виде пустого
  }
  window.dispatchEvent(new CustomEvent<RequestsChange>(REQUESTS_CHANGED, { detail: change }));
}

/** Диалог стал обращением (человек что-то спросил) или снова открыт — наверх списка. */
export function rememberRequest(sessionId: string, owner: string): void {
  const items = readRequests();
  const known = items.find((r) => r.sessionId === sessionId);
  if (known && items[0] === known && known.owner === owner) return;
  write([
    { sessionId, owner, seenAt: known?.seenAt ?? "" },
    ...items.filter((r) => r.sessionId !== sessionId),
  ], "list");
}

/** Человек видел переписку до реплики с этим временем. Назад не откатываем:
 *  история могла догрузиться позже живого сообщения из сокета. */
export function markRequestSeen(sessionId: string, at: string): void {
  const items = readRequests();
  const known = items.find((r) => r.sessionId === sessionId);
  if (!known || (known.seenAt && Date.parse(known.seenAt) >= Date.parse(at))) return;
  write(items.map((r) => (r === known ? { ...r, seenAt: at } : r)), "seen");
}
