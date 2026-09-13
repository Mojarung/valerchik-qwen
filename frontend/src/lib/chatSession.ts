/**
 * Идентификатор текущего диалога в куке.
 *
 * Без него каждая перезагрузка страницы начинала разговор с чистого листа:
 * человек ждёт оператора, случайно обновляет вкладку — и попадает в новый
 * пустой чат, хотя его обращение по-прежнему стоит в очереди и оператор ему
 * что-то пишет. То же самое, если закрыть вкладку и вернуться.
 *
 * Кука, а не localStorage: чат встраивается на сторонние сайты через iframe
 * (/widget), а там localStorage у виджета свой и легко обнуляется политиками
 * браузера для сторонних хранилищ. И, в отличие от sessionStorage, кука
 * переживает закрытие вкладки — ради этого всё и делается.
 */

import { readValue, writeValue } from "@/lib/storage";

const COOKIE = "valerchik-chat";
/** Неделя: дольше держать неоконченный разговор смысла нет, короче — мало,
 *  человек может вернуться к своему вопросу и на следующий день. */
const MAX_AGE_SECONDS = 60 * 60 * 24 * 7;

/** Диалог и доказательство владения им. */
export interface StoredChatSession {
  sessionId: string;
  owner: string;
}

/**
 * В куке лежит пара «id.токен».
 *
 * Токен нужен, чтобы писать в диалог: одного id мало — он уезжает в выгрузку
 * диалогов, в /dbadmin и остаётся в куке на общем компьютере, а значит
 * посторонний мог бы отправлять реплики от имени заявителя прямо в живой
 * разговор с оператором.
 */
export function readChatSession(): StoredChatSession | null {
  const value = readValue(COOKIE);
  // Строгий формат: мусорная кука не должна превращаться в бесконечные
  // запросы к несуществующему диалогу.
  const parsed = /^([a-f0-9]{32})\.([a-f0-9]{32})$/.exec(value);
  if (!parsed?.[1] || !parsed[2]) return null;
  return { sessionId: parsed[1], owner: parsed[2] };
}

/**
 * SameSite подбирается по месту, а не задан константой.
 *
 * Во фрейме на чужом сайте кука с `Lax` для браузера третьесторонняя, и он её
 * не отправляет — то есть ровно в том сценарии, ради которого кука и выбиралась
 * вместо localStorage, разговор терялся при каждой перезагрузке. Нужен `None`,
 * но браузеры принимают его только вместе с `Secure`, а `Secure` не переживёт
 * http-стенд: там кука не запишется вовсе, и мы сломаем обычный чат ради
 * встройки. Поэтому `None; Secure` ставится только когда мы действительно во
 * фрейме и соединение защищённое; во всех остальных случаях остаётся `Lax`.
 *
 * Встройка по http (демо-стенд на голом IP) диалог между перезагрузками не
 * сохранит — это ограничение браузера, обойти его нечем, кроме https.
 */
export function cookieSameSite(): string {
  const embedded = window.self !== window.top;
  return embedded && window.isSecureContext ? "samesite=none; secure" : "samesite=lax";
}

export function saveChatSession(sessionId: string, owner: string): void {
  writeValue(COOKIE, `${sessionId}.${owner}`,
             `path=/; max-age=${MAX_AGE_SECONDS}; ${cookieSameSite()}`);
}

export function clearChatSession(): void {
  writeValue(COOKIE, "", `path=/; max-age=0; ${cookieSameSite()}`);
}
