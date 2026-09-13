import { API_BASE, apiUrl, assetUrl, originHeaders, wsUrl } from "@/lib/apiBase";
import { readChatSession } from "@/lib/chatSession";

// Адреса живут в apiBase.ts (там же объяснено почему), но привычный путь
// импорта остаётся: половина интерфейса берёт их отсюда.
export { API_BASE, apiUrl, assetUrl, wsUrl };

/**
 * Связь с хранилищем сессии — обратными вызовами, а не импортом.
 *
 * Импортировать хранилище отсюда напрямую нельзя: хранилище само ходит на
 * сервер через `api`, и получилось бы то самое кольцо импортов, ради разрыва
 * которого адреса вынесены в apiBase.ts. Обратные вызовы оставляют связь
 * односторонней: api.ts не знает про хранилище ничего.
 *
 * `sessionEpoch` — номер текущего состояния сессии, снимок делается в момент
 * старта запроса. Без него ответ 401 нельзя отличить от опоздавшего: запрос,
 * ушедший до входа, возвращается уже после него, и его «не авторизован»
 * выкинул бы только что вошедшего сотрудника.
 */
let sessionEpoch: () => number = () => 0;
let onUnauthorized: (epochAtStart: number) => void = () => {};

export function setSessionHandlers(
  epoch: () => number,
  unauthorized: (epochAtStart: number) => void,
): void {
  sessionEpoch = epoch;
  onUnauthorized = unauthorized;
}

export class ApiError extends Error {
  status: number;
  constructor(status: number, detail: string) {
    super(detail);
    this.status = status;
  }
}

/**
 * Сколько ждать ответа. Без предела зависший сервер держал страницу в
 * «загрузке» вечно; предел по умолчанию короче таймаута nginx (120 с), чтобы
 * человек узнал о проблеме от интерфейса, а не от страницы ошибки прокси.
 */
export const DEFAULT_TIMEOUT_MS = 30_000;
/** Долгие операции: ответ нейросети (черновик оператору, тексты сценария) и
 *  проверка внешнего MCP-сервера, который отвечает в меру своих сил. */
export const SLOW_TIMEOUT_MS = 120_000;
/** Загрузка документа до 128 МБ по медленному каналу и разбор его на месте в lite. */
export const UPLOAD_TIMEOUT_MS = 600_000;

export interface RequestOptions {
  /** Отмена вызывающим кодом: страница закрылась, фильтр сменился. */
  signal?: AbortSignal;
  timeoutMs?: number;
}

/** Запрос отменил сам вызывающий код — это не сбой, и показывать его не нужно. */
export function isAbort(error: unknown): boolean {
  return error instanceof DOMException && error.name === "AbortError";
}

/** Текст ошибки для человека: сообщение сервера или запасная формулировка. */
export function errorText(error: unknown, fallback = "Не удалось выполнить действие"): string {
  return error instanceof Error && error.message ? error.message : fallback;
}

/**
 * Понятный текст там, где тело ответа не наше: страницу 413 рисует nginx, и
 * пользователь видел бы английский statusText вместо объяснения.
 */
const STATUS_FALLBACK: Record<number, string> = {
  413: "Файл слишком большой — сервер отклонил загрузку",
  502: "Сервис недоступен, попробуйте через минуту",
  503: "Сервис перегружен или перезапускается, попробуйте позже",
  504: "Сервер не ответил вовремя",
};

/** Ошибки валидации FastAPI приходят массивом объектов — показывать его сырым нельзя. */
function formatDetail(detail: unknown): string {
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    return detail
      .map((item) => {
        const entry = item as { loc?: unknown[]; msg?: string };
        const field = Array.isArray(entry.loc) ? entry.loc.slice(1).join(".") : "";
        return field ? `${field}: ${entry.msg ?? ""}` : (entry.msg ?? "");
      })
      .filter(Boolean)
      .join("; ");
  }
  return "";
}

async function request<T>(path: string, init: RequestInit = {}, options: RequestOptions = {}): Promise<T> {
  // Каким было состояние сессии, когда запрос уходил: ответ 401 относится
  // именно к нему, а не к тому, что успело случиться за время ожидания.
  const epochAtStart = sessionEpoch();
  // FormData сериализует браузер — свой Content-Type ставить нельзя, иначе потеряется
  // multipart boundary и сервер не увидит файл.
  const isForm = init.body instanceof FormData;
  // Доказательство владения диалогом: сервер требует его на любой записи в чат,
  // иначе чужой человек, узнавший id, писал бы реплики от имени заявителя.
  const chat = readChatSession();
  const headers: Record<string, string> = {
    ...(init.body && !isForm ? { "Content-Type": "application/json" } : {}),
    ...originHeaders(),
    ...(chat && path.includes(`/sessions/${chat.sessionId}`)
      ? { "X-Session-Owner": chat.owner } : {}),
    ...((init.headers as Record<string, string>) ?? {}),
  };
  // Свой контроллер: оборвать запрос могут два повода — истёк срок или
  // передумал вызывающий, — и ответить на них нужно по-разному.
  const { signal, timeoutMs = DEFAULT_TIMEOUT_MS } = options;
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  const cancel = () => controller.abort();
  if (signal?.aborted) cancel();
  signal?.addEventListener("abort", cancel, { once: true });
  try {
    // Права сотрудника подтверждает httpOnly-кука, а не заголовок: JWT больше не
    // виден JS, поэтому украсть его со страницы (или из localStorage) нечем.
    // `same-origin`, а не `include`: на чужой origin куку слать незачем.
    const resp = await fetch(apiUrl(path), {
      ...init,
      credentials: "same-origin",
      headers,
      signal: controller.signal,
    });
    if (!resp.ok) {
      let detail = STATUS_FALLBACK[resp.status] ?? resp.statusText;
      try {
        const data = await resp.json();
        detail = formatDetail(data.detail) || detail;
      } catch {
        /* тело не JSON (например, страница ошибки nginx) — остаётся понятный текст выше */
      }
      // Кука протухла или её отозвали — состояние интерфейса должно это увидеть,
      // иначе шапка продолжает показывать сотрудника, а разделы отдают ошибки.
      // Решает хранилище: только оно знает, не устарел ли этот ответ.
      if (resp.status === 401) onUnauthorized(epochAtStart);
      throw new ApiError(resp.status, detail);
    }
    // 204 — законный успешный ответ без тела (выход из системы). json() на нём
    // падает, и вызывающий код получал бы ошибку на успешном запросе.
    if (resp.status === 204) return undefined as unknown as T;
    return (await resp.json()) as T;
  } catch (error) {
    if (error instanceof ApiError) throw error;
    if (signal?.aborted) throw new DOMException("Запрос отменён", "AbortError");
    if (controller.signal.aborted) {
      throw new ApiError(0, `Сервер не ответил за ${Math.round(timeoutMs / 1000)} с — попробуйте ещё раз`);
    }
    if (error instanceof SyntaxError) throw new ApiError(0, "Ответ сервера не удалось разобрать");
    // fetch отклоняется, только когда ответа не было вовсе: сеть, DNS, CORS
    throw new ApiError(0, "Нет связи с сервером — проверьте подключение и попробуйте ещё раз");
  } finally {
    clearTimeout(timer);
    signal?.removeEventListener("abort", cancel);
  }
}

export const api = {
  get: <T>(path: string, options?: RequestOptions) => request<T>(path, {}, options),
  post: <T>(path: string, body?: unknown, options?: RequestOptions) =>
    request<T>(
      path,
      { method: "POST", body: body === undefined ? undefined : JSON.stringify(body) },
      options,
    ),
  put: <T>(path: string, body: unknown, options?: RequestOptions) =>
    request<T>(path, { method: "PUT", body: JSON.stringify(body) }, options),
  patch: <T>(path: string, body: unknown, options?: RequestOptions) =>
    request<T>(path, { method: "PATCH", body: JSON.stringify(body) }, options),
  delete: <T>(path: string, options?: RequestOptions) => request<T>(path, { method: "DELETE" }, options),
  upload: <T>(path: string, form: FormData, options?: RequestOptions) =>
    request<T>(path, { method: "POST", body: form }, { timeoutMs: UPLOAD_TIMEOUT_MS, ...options }),
};
