import type { OperatorCaseHint } from "@/lib/types";

/**
 * Похожие обращения из события copilot: бэкенд шлёт их полем `cases` рядом со
 * статьями `suggestions` (chat/copilot_flow.py, _push_copilot). Читаем
 * недоверчиво: сервер, который поля ещё не присылает, или одна кривая запись
 * не должны ронять колонку подсказок — без обращений остаются статьи.
 */
export function casesOf(event: object): OperatorCaseHint[] {
  return parseCases((event as { cases?: unknown }).cases);
}

/** Список похожих обращений из ответа сервера — сокета или REST. */
export function parseCases(raw: unknown): OperatorCaseHint[] {
  if (!Array.isArray(raw)) return [];
  return raw.filter(isCaseCard).map((c) => ({
    article_id: c.article_id,
    question: c.question,
    answer: c.answer,
    score: c.score,
    category: typeof c.category === "string" ? c.category : null,
    line: typeof c.line === "number" ? c.line : null,
    // Вид проставляем сами: список `cases` и есть обращения, а без пометки
    // карточка потом не отличит отчёт по чужой заявке от статьи
    kind: "operator_case",
  }));
}

type RawCard = { article_id: string; question: string; answer: string; score: number; category?: unknown; line?: unknown };

/** Без текста ответа и id карточку не вставить и не отрисовать — такую пропускаем. */
function isCaseCard(x: unknown): x is RawCard {
  if (!x || typeof x !== "object") return false;
  const c = x as Record<string, unknown>;
  return typeof c.article_id === "string" && typeof c.question === "string"
    && typeof c.answer === "string" && typeof c.score === "number";
}
