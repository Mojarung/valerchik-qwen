import type { Api } from "@/lib/types";

export type ConflictResult = Api["ConflictResult"];
export type ConflictGroup = Api["ConflictGroup"];
export type ConflictGroupSource = Api["ConflictGroupSource"];
export type AuditJob = Api["AuditJobOut"];
export type SourceSnapshot = Api["SourceSnapshot"];
export type ReviewStatus = "unreviewed" | "confirmed" | "dismissed";

export const REVIEW_LABEL: Record<string, string> = {
  unreviewed: "Нужно выбрать ответ", confirmed: "Ответ выбран", dismissed: "Не противоречие",
};
export const OUTCOME_LABEL: Record<string, string> = {
  conflict: "Есть расхождение", compatible: "Расхождений нет", uncertain: "Недостаточно данных",
};
export const JOB_LABEL: Record<string, string> = {
  pending: "В очереди", running: "Проверяем статьи", done: "Проверка завершена",
  retryable: "Проверка прервалась", error: "Ошибка проверки", budget_exhausted: "Достигнут лимит",
  cancelled: "Проверка отменена", stopped: "Проверка остановлена",
};
export const ROLE_LABEL: Record<string, string> = {
  all: "Для всех", supplier: "Поставщик", customer: "Заказчик",
};
export const activeJob = (job: AuditJob) => job.status === "pending" || job.status === "running";
