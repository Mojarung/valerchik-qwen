import { useMemo, useState } from "react";
import { BookOpenText, X } from "lucide-react";
import { assetUrl } from "@/lib/api";
import { useApiData } from "@/lib/useApiData";
import { LoadError } from "./LoadError";
import { ImageLightbox } from "./ImageLightbox";
import { Modal } from "./Modal";
import type { Api, ArticleImage, Citation } from "@/lib/types";

type SourceArticle = Api["SourceArticle"];

const STOP = new Set([
  "это", "как", "что", "для", "при", "или", "если", "чтобы", "также", "либо",
  "после", "перед", "будет", "можно", "нужно", "его", "все", "вам", "ваш", "наш",
]);

function tokens(text: string): Set<string> {
  return new Set(
    (text.toLowerCase().replace(/ё/g, "е").match(/[а-яa-z0-9]{3,}/g) ?? [])
      .filter((w) => !STOP.has(w))
      .map((w) => w.slice(0, 6)),
  );
}

function splitSentences(text: string): string[] {
  return text
    .split(/(?<=[.!?…])\s+|\n+/)
    .map((s) => s.trim())
    .filter(Boolean);
}

/**
 * Модалка «источник ответа»: статья базы знаний с подсветкой предложений,
 * из которых ассистент собрал ответ (сопоставление по пересечению токенов).
 */
export function SourceModal({ citation, answerText, onClose, evidenceMode = false }: {
  citation: Citation;
  answerText: string;
  onClose: () => void;
  evidenceMode?: boolean;
}) {
  // Смена цитаты отменяет прежний запрос: поздний ответ про прошлую статью
  // не подменит текст уже открытой.
  const { data: article, error, reload } = useApiData<SourceArticle>(`/api/chat/articles/${citation.article_id}`);
  const [zoomed, setZoomed] = useState<ArticleImage | null>(null);

  const sentences = useMemo(() => {
    if (!article) return [];
    const answerTokens = tokens(answerText);
    return splitSentences(article.answer).map((sentence) => {
      const st = tokens(sentence);
      if (st.size === 0) return { sentence, hit: false };
      let overlap = 0;
      for (const t of st) if (answerTokens.has(t)) overlap += 1;
      return { sentence, hit: overlap / st.size >= 0.5 };
    });
  }, [article, answerText]);

  const hitCount = sentences.filter((s) => s.hit).length;

  return (
    <Modal title="Источник ответа" hideTitle onClose={onClose}>
        <div className="flex items-start gap-2.5 border-b border-(--color-line) px-4 py-3">
          <BookOpenText size={17} className="mt-0.5 shrink-0 text-(--color-brand-ink)" />
          <div className="mr-auto">
            <div className="text-sm font-bold leading-snug">{article?.question ?? citation.question}</div>
            <div className="mt-0.5 flex flex-wrap items-center gap-1.5 text-xs text-(--color-ink-soft)">
              {article && (
                <span className="rounded-full bg-(--color-surface-2) px-2 py-0.5">{article.category}</span>
              )}
              {!evidenceMode && <span className="rounded-full bg-(--color-brand-soft) px-2 py-0.5 font-semibold text-(--color-brand-ink)">
                релевантность {(citation.score * 100).toFixed(0)}%
              </span>}
              {hitCount > 0 && (
                <span>подсвечено фрагментов: {hitCount}</span>
              )}
            </div>
          </div>
          <button className="btn btn-ghost !p-1.5" onClick={onClose} aria-label="Закрыть">
            <X size={15} />
          </button>
        </div>

        <div className="min-h-0 flex-1 overflow-y-auto px-4 py-3 text-sm leading-relaxed">
          {error && <LoadError message={error} onRetry={reload} />}
          {!article && !error && (
            <div className="py-6 text-center text-xs text-(--color-ink-soft)">Загрузка статьи…</div>
          )}
          {article && (
            <p>
              {sentences.map(({ sentence, hit }, i) => (
                <span key={i}>
                  {hit ? (
                    <mark className="rounded bg-(--color-warn)/25 text-(--color-ink) box-decoration-clone px-0.5">
                      {sentence}
                    </mark>
                  ) : (
                    sentence
                  )}{" "}
                </span>
              ))}
            </p>
          )}
          {/* Иллюстрации из исходной методички. Текст инструкций ссылается на них
              прямо — «нажмите (Рисунок 26 (2))», — поэтому без картинки такой
              фрагмент читается как загадка. */}
          {article?.images?.length ? (
            <div className="mt-4 space-y-3 border-t border-(--color-line) pt-3">
              {article.images.map((image) => (
                <figure key={image.url}>
                  {/* Крупный просмотр открываем на месте: скриншот интерфейса с
                      мелкими выносками в модалке шириной с телефон не разобрать */}
                  <button className="flex w-full cursor-zoom-in justify-center" onClick={() => setZoomed(image)}
                    title="Открыть крупно">
                    <img src={assetUrl(image.url)} alt={image.caption || "Иллюстрация из инструкции"}
                      loading="lazy"
                      className="h-auto max-h-[60vh] w-auto max-w-full rounded-xl border border-(--color-line) object-contain transition-opacity hover:opacity-90" />
                  </button>
                  {image.caption && (
                    <figcaption className="mt-1 text-xs text-(--color-ink-soft)">
                      {image.caption}
                      {image.page ? ` · стр. ${image.page}` : ""}
                    </figcaption>
                  )}
                </figure>
              ))}
            </div>
          ) : null}
        </div>

        <div className="border-t border-(--color-line) px-4 py-2 text-xs text-(--color-ink-soft)">
          <mark className="rounded bg-(--color-warn)/25 text-(--color-ink) px-1">Подсвечены</mark> предложения статьи,
          {evidenceMode ? "совпадающие с приведёнными выдержками." : "из которых собран ответ ассистента."}
          {article && <> Обновлено: {new Date(article.updated_at).toLocaleDateString("ru")}.</>}
        </div>
      {zoomed && <ImageLightbox image={zoomed} onClose={() => setZoomed(null)} />}
    </Modal>
  );
}
