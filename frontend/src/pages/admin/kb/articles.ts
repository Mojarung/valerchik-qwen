import type { KbArticle } from "@/lib/types";

/** Кому адресована статья. Ключи те же, что на сервере (services/audience.py). */
export const AUDIENCE_LABEL: Record<string, string> = {
  all: "Всем", supplier: "Поставщику", customer: "Заказчику",
};

/** Значение списка «Кому» — ключ AUDIENCE_LABEL; всё прочее — «для всех». */
export const audienceOf = (value: string): KbArticle["audience"] =>
  value === "supplier" || value === "customer" ? value : "all";

/** С чего начинается новая статья в редакторе. */
export const EMPTY_ARTICLE: Partial<KbArticle> = {
  question: "", answer: "", category: "Новые темы", keywords: [], line: 1, status: "active",
  audience: "all",
};

/** Однострочное превью ответа: в таблице разметка не рендерится, и «**орган**»
 *  со звёздочками читался как опечатка в тексте статьи. */
export function plainPreview(text: string): string {
  return text
    .replace(/!\[[^\]]*\]\([^)]*\)/g, "")          // картинки — целиком
    .replace(/\[([^\]]*)\]\([^)]*\)/g, "$1")       // ссылки — только подпись
    .replace(/[*_`>#]+/g, "")                       // выделение, код, цитаты, заголовки
    .replace(/\s+/g, " ")
    .trim();
}

export type BulkAction = "publish" | "archive" | "purge";

export const BULK_DONE: Record<BulkAction, (n: number) => string> = {
  publish: (n) => `Опубликовано статей: ${n} — уже ищутся в чате`,
  archive: (n) => `В архив отправлено статей: ${n}, из поиска убраны`,
  purge: (n) => `Удалено статей насовсем: ${n}`,
};
