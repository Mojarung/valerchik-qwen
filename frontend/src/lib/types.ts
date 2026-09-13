/**
 * Типы данных API — из схемы бэкенда: src/lib/api-schema.ts генерирует
 * openapi-typescript (`npm run api:types`). Здесь — короткие имена для страниц
 * и типы того, чего в схеме нет: содержимое meta сообщения и события сокета
 * бэкенд описывает свободным словарём.
 */
import type { components } from "@/lib/api-schema";

/** Все модели ответов API по имени: Api["GapOut"], Api["QueueItem"]… */
export type Api = components["schemas"];

export type ChatMessage = Api["ChatMessageOut"];
export type ArticleImage = Api["ImageRef"];
export type Citation = Api["Citation"];
export type ClarifyOption = Api["ClarifyOption"];
export type GuideLocator = Api["GuideLocator"];
export type GuideStep = Api["GuideStep"];
export type Guide = Api["GuidePublic"];
export type OperatorSession = Api["QueueItem"];
export type KbArticle = Api["ArticleOut"];
export type SettingEntry = Api["SettingEntryOut"];
export type Stats = Api["OverviewStats"];
export type TopicRow = Api["TopicRow"];
export type TopicsReport = Api["TopicsReport"];
export type TopicsResponse = Api["TopicsState"];
export type EvalReport = Api["EvalReport"];
export type EvalResponse = Api["EvalState"];

/** Похожее решённое обращение — подсказка оператору «как решали похожее».
 *  Приходит отдельным списком `cases` события copilot (backend
 *  chat/copilot_flow.py, _case_cards), а не среди статей `suggestions`: это
 *  отчёт оператора о действиях по чужой заявке, и клиенту его как есть не
 *  отправишь. Поля статьи плюс линия, на которой такое закрывали. */
export type OperatorCaseHint = Api["KbHit"] & { line: number | null; kind: "operator_case" };

/** Карточка товара каталога портала: приезжает в meta ответа от MCP-инструмента. */
export interface ProductCard {
  sku_id: number | null;
  name: string;
  url: string;
  image: string | null;
  image_full?: string | null;
  /** false — показана картинка категории, а не фотография самого товара. */
  own_image?: boolean;
  price?: number | null;
  price_kind?: "reference" | "median" | "offer" | null;
  unit?: string | null;
  category?: string | null;
  offers_count?: number | null;
}

export type { WsEvent } from "@/lib/wsEvents";

export type MascotMood =
  | "idle" | "thinking" | "speaking" | "sorry" | "alert" | "happy" | "fixing" | "listening";
