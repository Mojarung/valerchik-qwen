/**
 * События WebSocket как размеченное объединение.
 *
 * Поля каждого варианта повторяют то, что реально публикует сервер
 * (`backend/app/chat/*.py`, `services/chat.py`, `services/dispatch.py`,
 * `routers/chat.py`), а не то, что удобно фронтенду: опечатка в имени поля
 * должна ловиться компилятором, а не молча давать `undefined` в рантайме.
 */
import type { components } from "@/lib/api-schema";
import type { ChatMessage } from "@/lib/types";

/** Карточка статьи в подсказках оператору (то же, что отдаёт поиск по базе знаний). */
export type CopilotSuggestion = components["schemas"]["KbHit"];

/** Адресное назначение обращения оператору (комната `operator:{id}`). */
export interface AssignmentEvent {
  type: "assigned";
  session_id: string;
  operator_id: string;
  operator_name: string;
  line: number | null;
  reason: string;
  question: string;
  waiting_seconds: number;
}

export type ChatWsEvent =
  | { type: "support_state"; session_id: string; version: number; status: string; line: number | null; waiting_reason: string | null }
  | { type: "typing"; role: "assistant" }
  | { type: "assistant_chunk"; text: string }
  | { type: "message"; message: ChatMessage }
  /** Сообщение пользователя исправлено: kinds — "layout" (раскладка) и/или "typo" (опечатки). */
  | { type: "layout_fixed"; message_id: string; original: string; text: string; kinds?: string[] }
  /** Кому передано обращение: название линии и, если вопрос решает не она, адресат по регламенту. */
  | { type: "escalated"; line: number; line_label: string; addressee_label: string }
  | { type: "session_closed"; reason: "resolved" | "violation" | string }
  | { type: "audience"; audience: string | null }
  | { type: "error"; detail: string }
  /** Оператор что-то печатает в ответ — throttled сигнал, гаснет сам через пару секунд без повторов. */
  | { type: "operator_typing" };

export type OperatorWsEvent =
  | { type: "ticket_changed"; session_id: string }
  | { type: "new_ticket"; session_id: string; line: number; reason: string }
  | { type: "ticket_taken"; session_id: string }
  | { type: "ticket_assigned"; session_id: string; operator_id: string; operator_name: string }
  | { type: "ticket_closed"; session_id: string }
  | { type: "ticket_reclaimed"; session_id: string }
  | { type: "session_violation"; session_id: string }
  | { type: "reclaimed"; session_id: string }
  | AssignmentEvent
  | { type: "copilot"; suggestions: CopilotSuggestion[]; cases?: (CopilotSuggestion & {line: number | null; kind: "operator_case"})[]; reply_to: string; draft_pending: boolean }
  | ({ type: "copilot_draft"; draft: string; generated: boolean; reply_to: string } & Partial<components["schemas"]["CopilotDraft"]>)
  | { type: "user_typing"; text: string }
  | { type: "message_rating"; session_id: string; value: number }
  | { type: "feedback"; session_id: string; rating: number };

export type WsEvent = ChatWsEvent | OperatorWsEvent;

const KNOWN_TYPES: ReadonlySet<string> = new Set<WsEvent["type"]>([
  "support_state", "ticket_changed",
  "typing", "assistant_chunk", "message", "layout_fixed", "escalated", "session_closed",
  "audience", "error", "operator_typing", "new_ticket", "ticket_taken", "ticket_assigned",
  "ticket_closed", "ticket_reclaimed", "session_violation", "reclaimed", "assigned", "copilot",
  "copilot_draft", "user_typing", "message_rating", "feedback",
]);

/**
 * Разбор кадра: объект с известным `type` проходит, всё остальное → null.
 * Поля вариантов здесь не валидируются — сервер свой, а объединение выше
 * описывает его контракт; защита нужна от мусора и от событий, которых клиент
 * ещё не знает после обновления сервера.
 */
export function parseWsEvent(raw: unknown): WsEvent | null {
  if (!raw || typeof raw !== "object") return null;
  const type = (raw as { type?: unknown }).type;
  if (typeof type !== "string" || !KNOWN_TYPES.has(type)) return null;
  return raw as WsEvent;
}
