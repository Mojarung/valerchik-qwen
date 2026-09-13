/**
 * Адреса сервера: где он живёт и как собрать путь до ручки, файла и вебсокета.
 *
 * Отдельным модулем от api.ts намеренно. Хранилище состояния тоже строит
 * адреса, а api.ts обращается к хранилищу — импортируй они друг друга
 * напрямую, получилось бы кольцо, из-за которого сборщик уже клал хранилище
 * в бандл дважды: страница входа писала сессию в один экземпляр, а запросы
 * читали пустой второй. Здесь только чистые функции и константы без единой
 * зависимости, поэтому импортировать модуль может кто угодно.
 *
 * Обычно чат и API — один origin, и пути относительные. В расширении для
 * браузера интерфейс лежит внутри самого расширения (chrome-extension://…),
 * а сервер задаётся параметром `?api=http://localhost:8090`: так страница
 * портала не встраивает localhost напрямую — Chrome такое блокирует
 * (Local Network Access), — а расширение ходит к серверу по своим правам.
 */

/**
 * Подтверждение, что запрос отправил наш интерфейс, а не чужая страница.
 *
 * Кука сессии уходит на сервер сама, в том числе с формы на постороннем сайте;
 * заголовок же поставить со стороны может только код нашего origin. Сервер
 * требует его на любом изменяющем запросе с кукой — расхождение в названии
 * между клиентом и сервером выглядит как молчаливый 403, поэтому имя и
 * значение живут в одной константе на весь фронтенд.
 */
export const REQUESTED_WITH_HEADER = "X-Requested-With";
export const REQUESTED_WITH_VALUE = "valerchik";

/** Заголовки происхождения запроса — их несёт каждый запрос к API. */
export function originHeaders(): Record<string, string> {
  return { [REQUESTED_WITH_HEADER]: REQUESTED_WITH_VALUE };
}

const EXTENSION_PROTOCOLS = ["chrome-extension:", "moz-extension:"];

/**
 * Адрес сервера из `?api=` — и только внутри расширения.
 *
 * На обычном сайте этот параметр читать нельзя: ссылка `https://наш.домен/?api=
 * https://evil.tld` оставляла бы в адресной строке наш домен, а весь разговор
 * вместе с логином и паролем сотрудника уводила бы на чужой сервер. Внутри
 * расширения origin страницы — `chrome-extension://…`, подделать его чужой
 * ссылкой невозможно, и там параметр остаётся единственным способом узнать,
 * где поднят сервер.
 *
 * Функция чистая (никакого `window`), чтобы её поведение проверялось тестом,
 * а не догадками про окружение.
 */
export function resolveApiBase(search: string, protocol: string): string {
  if (!EXTENSION_PROTOCOLS.includes(protocol)) return "";
  try {
    const raw = new URLSearchParams(search).get("api");
    return raw ? new URL(raw).origin : "";
  } catch {
    return "";
  }
}

export const API_BASE: string = resolveApiBase(
  window.location.search,
  window.location.protocol,
);

/** Абсолютный адрес ручки API (или относительный, когда сервер — этот же origin). */
export function apiUrl(path: string): string {
  return API_BASE ? `${API_BASE}${path}` : path;
}

/** Адрес файла, который сервер отдал относительным путём: картинки статей, вложения, товары. */
export function assetUrl(url: string): string {
  return API_BASE && url.startsWith("/") ? `${API_BASE}${url}` : url;
}

export function wsUrl(path: string): string {
  const origin = API_BASE ? new URL(API_BASE) : window.location;
  const proto = origin.protocol === "https:" ? "wss" : "ws";
  return `${proto}://${origin.host}${path}`;
}
