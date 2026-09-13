import type { ChatMessage } from "@/lib/types";

/**
 * Предложение позвать человека под ответом Валерчика.
 *
 * «call» — отказ «в базе знаний ответа нет»: одна кнопка «Позвать оператора».
 * «confirm» — Валерчик сам спросил, передать ли обращение специалисту
 * (kind = "operator_offer"): «да» и «нет, спрошу иначе».
 */
export type OperatorOffer = "call" | "confirm";

/* Кнопки шлют обычные реплики, а не отдельную команду сокета. Сервер узнаёт
   просьбу позвать человека по словам (wants_operator) — ровно так же, как если
   бы человек напечатал её сам. Поэтому у кнопки нет своего пути на сервере,
   который мог бы разойтись с текстовым, а в переписке и у оператора остаётся
   понятная реплика вместо «нажата кнопка». */
export const CALL_OPERATOR_TEXT = "Позовите оператора";
export const CONFIRM_OPERATOR_TEXT = "Да, передайте оператору";

/** Какое предложение несёт сообщение; null — никакого. meta — свободный словарь
 *  с сервера, поэтому поля читаются по одному и сравниваются строго. */
export function operatorOfferOf(msg: ChatMessage): OperatorOffer | null {
  if (msg.role !== "assistant") return null;
  const meta = msg.meta as { offer_operator?: unknown; kind?: unknown };
  // Вопрос «передать специалисту?» без кнопок бессмыслен, даже если флаг
  // offer_operator сервер положить забыл: отвечать на него пришлось бы вслепую.
  if (meta.kind === "operator_offer") return "confirm";
  return meta.offer_operator === true ? "call" : null;
}

export interface OfferState {
  /** Диалог закрыт: решён или завершён за нарушение. */
  closed: boolean;
  /** Обращение уже у оператора — в очереди или в работе. */
  escalated: boolean;
  /** Ждём ответа: Валерчик ищет или ответ ещё набегает. */
  busy: boolean;
}

/**
 * id ответа, под которым кнопки сейчас действуют; null — предлагать нечего.
 *
 * Живо только предложение в последней реплике разговора. Как только человек
 * написал что-то ещё (в том числе нажал саму кнопку), старое предложение
 * отвечает уже не на тот вопрос, и повторное нажатие под ним звало бы
 * оператора второй раз. Системные строки («предупреждение», «оцените»)
 * реплики не заменяют, их пропускаем.
 */
export function liveOperatorOffer(messages: ChatMessage[], { closed, escalated, busy }: OfferState): string | null {
  if (closed || escalated || busy) return null;
  for (let i = messages.length - 1; i >= 0; i -= 1) {
    const msg = messages[i]!;
    if (msg.role === "system") continue;
    return operatorOfferOf(msg) ? msg.id : null;
  }
  return null;
}
