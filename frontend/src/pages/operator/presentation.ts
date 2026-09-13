import type { Api } from "@/lib/types";

export type SupportLines = Api["MetaResponse"]["lines"];

/** Human-facing direction name. Numeric line ids stay an implementation detail. */
export function directionName(lines: SupportLines, line: number | null | undefined): string {
  if (!line) return "Направление не определено";
  return lines[String(line)]?.label || "Поддержка";
}

export function supportStatusLabel(status: string): string {
  return {
    waiting_operator: "Новое",
    operator_assigned: "Назначено",
    waiting_user: "Ждём клиента",
    operator_active: "В работе",
    active: "Диалог с ботом",
    resolved: "Завершено",
    closed_violation: "Закрыто из-за нарушения",
  }[status] ?? status;
}

/** Older records store the question and the escalation trigger in one string. */
export function escalationSummary(raw: string | null | undefined): {
  question: string;
  cause: string;
} {
  const value = (raw ?? "").trim();
  const disliked = /(?:[.!?]\s*)?ответ не помог\s*$/i.test(value);
  const question = value.replace(/(?:[.!?]\s*)?ответ не помог\s*$/i, "").trim();
  return {
    question,
    cause: disliked ? "Ответ бота не помог" : "",
  };
}
