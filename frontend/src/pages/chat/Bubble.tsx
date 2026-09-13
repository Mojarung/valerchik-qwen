import { memo, useMemo, useState } from "react";
import {
  BookOpenText, ChevronDown, ChevronUp, Footprints, Gauge, Headset, PencilLine, UserRound, Wrench,
} from "lucide-react";
import { assetUrl } from "@/lib/api";
import { AnswerRating } from "./AnswerRating";
import { hostSupportsGuides, sendGuideToHost } from "@/lib/pageContext";
import type { ArticleImage, ChatMessage, Citation, ClarifyOption, Guide, ProductCard } from "@/lib/types";
import { Attachment, attachmentOf } from "@/components/Attachment";
import { ImageLightbox } from "@/components/ImageLightbox";
import { Markdown } from "@/components/Markdown";
import { ProductCards } from "@/components/ProductCards";
import { SourceModal } from "@/components/SourceModal";
import { CALL_OPERATOR_TEXT, CONFIRM_OPERATOR_TEXT, operatorOfferOf } from "./operatorOffer";

/** Источников под ответом — не больше трёх: остальные ответ почти не использует. */
const MAX_SOURCES = 3;

/** Пузырь сообщения.
 *  memo не косметика: пока набегает ответ, страница перерисовывается двадцать
 *  пять раз в секунду (по чанку каждые 40 мс), и без него вся лента вместе с
 *  разбором Markdown пересчитывалась бы на каждом кадре. Ссылки на msg между
 *  чанками не меняются — во время стрима setMessages не вызывается. */
export const Bubble = memo(function Bubble({ msg, sessionId, onPick, onOperator, onAskAgain, canRecover = false }: {
  msg: ChatMessage; sessionId?: string; canRecover?: boolean;
  onPick?: (text: string, option?: ClarifyOption) => void;
  /** Позвать оператора: отправить реплику-просьбу. Страница передаёт его только
   *  тому ответу, чьё предложение ещё в силе (см. liveOperatorOffer), — у
   *  остальных кнопок нет вовсе. */
  onOperator?: (text: string) => void;
  /** «Нет, спрошу иначе»: вернуть человека в поле ввода. */
  onAskAgain?: () => void;
}) {
  const [source, setSource] = useState<Citation | null>(null);
  const [zoomed, setZoomed] = useState<ArticleImage | null>(null);
  const [showOriginal, setShowOriginal] = useState(false);
  const [showSources, setShowSources] = useState(false);
  const citations = (msg.citations ?? []).slice(0, MAX_SOURCES);

  // Одна и та же иллюстрация приходит с разными цитатами — показываем по разу
  const answerImages = useMemo(() => {
    const seen = new Map<string, ArticleImage>();
    for (const citation of msg.citations ?? []) {
      for (const image of citation.images ?? []) {
        if (!seen.has(image.url)) seen.set(image.url, image);
      }
    }
    return [...seen.values()];
  }, [msg.citations]);

  const products = (msg.meta as { products?: ProductCard[] }).products ?? [];
  const guide = (msg.meta as { guide?: Guide }).guide ?? null;

  if (msg.role === "system") {
    const isViolation = (msg.meta as { kind?: string }).kind === "violation";
    return (
      <div className="flex justify-center">
        <div className={`animate-(--animate-pop) max-w-[90%] rounded-full px-4 py-2 text-center text-xs ${isViolation ? "bg-(--color-brand-soft) text-(--color-brand-ink)" : "border border-(--color-line) bg-(--color-surface) text-(--color-ink-soft)"}`}>
          {msg.content}
        </div>
      </div>
    );
  }
  const isUser = msg.role === "user";
  const meta = msg.meta as { kind?: string; options?: ClarifyOption[] };
  // Уточнение темы и уточнение роли рисуются одинаково: кнопки-варианты под ответом
  const clarifyOptions = meta.kind === "clarify" || meta.kind === "clarify_audience"
    ? (meta.options ?? []) : [];
  const offer = operatorOfferOf(msg);
  const attachment = attachmentOf(msg.meta);
  const confidence = (msg.meta as { confidence?: number }).confidence;
  const sourceConflict = msg.meta.retrieval_outcome === "source_conflict";
  // Ответ взят из документа слово в слово, а не сочинён моделью. Для человека
  // это разница между «так написано в регламенте» и «так пересказал бот».
  const verbatim = (msg.meta as { verbatim?: boolean }).verbatim === true;
  const fixedMeta = msg.meta as { layout_fixed?: boolean; typo_fixed?: boolean; original?: string };
  const fixLabel = fixedMeta.layout_fixed && fixedMeta.typo_fixed ? "раскладка и опечатки исправлены"
    : fixedMeta.layout_fixed ? "раскладка исправлена"
    : fixedMeta.typo_fixed ? "опечатки исправлены" : "";
  return (
    <div className={`flex ${isUser ? "justify-end" : "justify-start"}`}>
      <div className={`animate-(--animate-pop) max-w-[85%] ${isUser ? "origin-bottom-right" : "flex origin-bottom-left items-end gap-2"}`}>
        {msg.role === "operator" && (
          <div className="mb-1 flex size-8 shrink-0 items-center justify-center rounded-full bg-(--color-warn)/15 text-(--color-warn-ink)">
            <UserRound size={15} />
          </div>
        )}
        {/* min-w-0: у пузыря с карточками товаров собственная ширина содержимого —
            восемь карточек по 160 пикселей. Без этого колонка как флекс-элемент
            берёт по умолчанию min-width: auto, раздувается до полутора тысяч
            пикселей и утаскивает за собой весь пузырь: max-w-[85%] перестаёт
            действовать, текст ответа и лента обрезаются краем переписки, а
            прокручиваться ленте становится некуда. */}
        <div className="min-w-0">
          <div
            data-msg-id={msg.id}
            className={`rounded-3xl px-4 py-3 text-sm ${
              isUser
                ? "rounded-br-lg bg-(--color-brand) whitespace-pre-wrap text-white shadow-[0_4px_14px_oklch(54%_0.21_25_/_0.25)]"
                : "card rounded-bl-lg"
            }`}
          >
            {/* обёртка нужна анимации починки раскладки: размывается и уплывает текст,
                а не сам пузырь с его фоном и тенью */}
            {/* Пустой текст бывает у вложения без подписи — тогда пузырь состоит
                из одной картинки, и лишний узел разметки только добавил бы отступ */}
            {msg.content && (isUser ? <span className="bubble-text">{msg.content}</span>
                                    : <Markdown>{msg.content}</Markdown>)}
            {/* Вложение оператора: скриншот открывается тем же лайтбоксом, что и
                иллюстрации методичек, документ — плашкой со скачиванием. */}
            {attachment && (
              <Attachment attachment={attachment}
                onZoom={attachment.kind === "image"
                  ? () => setZoomed({
                      url: attachment.url, caption: attachment.name, page: null, figure: null,
                    })
                  : undefined} />
            )}
            {/* Иллюстрации живут внутри пузыря, а не под ним: они часть ответа —
                текст инструкции ссылается на них прямо («нажмите (Рисунок 26 (2))»),
                и вынесенные наружу они читались бы как отдельное вложение. */}
            {!isUser && answerImages.length > 0 && (
              <div className="mt-3 space-y-2 border-t border-(--color-line) pt-3">
                {answerImages.map((image) => (
                  <figure key={image.url}>
                    {/* Потолок высоты: скриншот страницы методички в полный рост
                        растягивал пузырь на несколько экранов, а мелкая иконка при
                        w-full раздувалась и мылилась. Целиком — в лайтбоксе по клику. */}
                    <button
                      className="flex w-full cursor-zoom-in justify-center overflow-hidden rounded-xl border border-(--color-line) bg-(--color-surface) transition-shadow hover:shadow-md"
                      onClick={() => setZoomed(image)}
                      title="Открыть крупно"
                    >
                      <img src={assetUrl(image.url)} alt={image.caption || "Иллюстрация из инструкции"}
                        loading="lazy" className="block h-auto max-h-80 w-auto max-w-full object-contain" />
                    </button>
                    {image.caption && (
                      <figcaption className="mt-1 text-xs leading-snug text-(--color-ink-soft)">
                        {image.caption}
                        {image.page ? ` · стр. ${image.page}` : ""}
                      </figcaption>
                    )}
                  </figure>
                ))}
              </div>
            )}
            {/* Товары каталога портала — тоже часть ответа: на «найди ноутбук»
                человек ждёт карточки с фотографиями, а не абзац текста со ссылками. */}
            {!isUser && <ProductCards products={products} onZoom={setZoomed} />}
          </div>
          {/* Пошаговый сценарий: в расширении Валерчик проведёт по кнопкам портала.
              В обычном виджете кнопки нет: ссылка «Открыть раздел на портале» вела
              везде на одну и ту же страницу и только сбивала. Кнопка вне пузыря,
              как варианты уточнения: это действие, а не часть текста ответа. */}
          {!isUser && guide && guide.steps.length > 0 && hostSupportsGuides() && (
            <div className="mt-2">
              <button className="chip !border-(--color-brand) !bg-(--color-brand-soft) !text-xs !text-(--color-brand-ink)"
                onClick={() => sendGuideToHost(guide)}
                title="Валерчик подсветит кнопки на портале и проведёт по шагам">
                <Footprints size={13} /> Показать на портале · {guide.steps.length} {stepsWord(guide.steps.length)}
              </button>
            </div>
          )}
          {/* Варианты уточнения. Отдельного состояния у них нет: нажатие — это
              обычный вопрос из базы знаний, поэтому и в переписке остаётся
              нормальная реплика, а не «выбран вариант 2». Те же варианты уже
              перечислены в тексте вопроса — на случай голосового режима, где
              нажимать нечего. */}
          {clarifyOptions.length > 0 && onPick && (
            <div className="mt-2 flex flex-wrap gap-1.5">
              {clarifyOptions.map((option) => (
                <button key={option.article_id ?? option.audience ?? option.text}
                  className="chip !text-xs"
                  onClick={() => onPick(option.text, option)}>
                  {option.text}
                </button>
              ))}
            </div>
          )}
          {/* «Позвать оператора» под отказом и «да / нет» под вопросом «передать
              специалисту?». По ТЗ бот, не нашедший ответа, обязан предложить
              оператора — а предложение, которое надо выполнять вручную, набирая
              фразу, человек читает как отказ. Кнопки вне пузыря, как варианты
              уточнения: это действие, а не часть ответа. flex-wrap и перенос
              текста в кнопке — чтобы на телефоне шириной 320 пикселей пара
              кнопок вставала в столбик, а не вылезала за край ленты. */}
          {offer && onOperator && (
            <div className="mt-2 flex flex-wrap gap-2" role="group"
              aria-label={offer === "confirm" ? "Передать обращение специалисту?" : "Нужна помощь человека?"}>
              {offer === "confirm" ? (
                <>
                  <button type="button" className="btn btn-primary max-w-full !px-4 !py-2 !text-xs !whitespace-normal"
                    onClick={() => onOperator(CONFIRM_OPERATOR_TEXT)}
                    aria-label="Да, передать специалисту: обращение уйдёт оператору поддержки">
                    <Headset size={14} aria-hidden="true" className="shrink-0" /> Да, передать специалисту
                  </button>
                  <button type="button" className="btn btn-ghost max-w-full !px-4 !py-2 !text-xs !whitespace-normal"
                    onClick={onAskAgain}
                    aria-label="Нет, спрошу иначе: перейти к полю ввода">
                    <PencilLine size={14} aria-hidden="true" className="shrink-0" /> Нет, спрошу иначе
                  </button>
                </>
              ) : (
                <button type="button" className="btn btn-ghost max-w-full !px-4 !py-2 !text-xs !whitespace-normal"
                  onClick={() => onOperator(CALL_OPERATOR_TEXT)}
                  aria-label="Позвать оператора: передать вопрос специалисту поддержки">
                  <Headset size={14} aria-hidden="true" className="shrink-0" /> Позвать оператора
                </button>
              )}
            </div>
          )}
          {/* правку текста пользователя нельзя делать молча — показываем, что именно изменено */}
          {fixLabel && (
            <div className={`mt-1 text-xs text-(--color-ink-soft) ${isUser ? "text-right" : ""}`}>
              <span className="inline-flex items-center gap-1">
                <Wrench size={11} />
                {fixLabel}
              </span>
              {fixedMeta.original && (
                <button className="ml-1.5 underline decoration-dotted underline-offset-2 hover:text-(--color-brand-ink)"
                  onClick={() => setShowOriginal((v) => !v)}
                  aria-expanded={showOriginal}
                  title="Как сообщение было написано до исправления">
                  {showOriginal ? "скрыть оригинал" : "посмотреть оригинал"}
                </button>
              )}
              {showOriginal && fixedMeta.original && (
                <div className="mt-1 rounded-lg border border-(--color-line) bg-(--color-surface) px-2 py-1 text-left font-mono text-xs break-all text-(--color-ink-soft)">
                  {fixedMeta.original}
                </div>
              )}
            </div>
          )}
          {sessionId && msg.role === "assistant" && !(msg.meta as { kind?: string }).kind && (
            <AnswerRating msg={msg} sessionId={sessionId} canRecover={canRecover} />
          )}
          {!isUser && citations.length > 0 && (
            <div className="mt-2 space-y-1.5">
              {/* Источники — те, на которые опирается ответ, и свёрнуты: ответ читают
                  сразу, а откуда он — открывают, когда нужно проверить. Уверенность
                  относится ко всему ответу, поэтому она в строке кнопки, а не в списке. */}
              <div className="flex flex-wrap items-center gap-1.5 text-xs text-(--color-ink-soft)">
                {sourceConflict ? (
                  <>
                    <BookOpenText size={12} className="text-(--color-warn-ink)" aria-hidden="true" />
                    <span className="text-(--color-warn-ink)">Источники расходятся — требуется проверка</span>
                  </>
                ) : typeof confidence === "number" && (verbatim ? (
                  <>
                    <BookOpenText size={12} className="text-(--color-ok-ink)" />
                    <span>
                      Ответ приведён <b className="text-(--color-ok-ink)">дословно по документу</b>
                    </span>
                  </>
                ) : (
                  <>
                    <Gauge size={12} className={confidenceColor(confidence)} />
                    <span>
                      Уверенность{" "}
                      <b className={confidenceColor(confidence)}>
                        {(confidence * 100).toFixed(0)}%
                      </b>
                    </span>
                  </>
                ))}
                {(sourceConflict || typeof confidence === "number") && (
                  <span aria-hidden="true" className="text-(--color-ink-soft)">·</span>
                )}
                <button type="button"
                  className="inline-flex items-center gap-1 hover:text-(--color-brand-ink)"
                  aria-expanded={showSources}
                  onClick={() => setShowSources((v) => !v)}>
                  <BookOpenText size={12} aria-hidden="true" />
                  Источники · {citations.length}
                  {showSources ? <ChevronUp size={12} aria-hidden="true" /> : <ChevronDown size={12} aria-hidden="true" />}
                </button>
              </div>
              {showSources && <div className="flex flex-wrap gap-1.5">
                {citations.map((c) => (
                  // вопрос показываем целиком с переносом: обрезка многоточием упиралась в рамку чипа
                  <button
                    key={c.article_id}
                    className="chip max-w-full !items-start !py-1.5 !text-left !text-xs !whitespace-normal"
                    title={`${c.question} — открыть источник с подсветкой`}
                    onClick={() => setSource(c)}
                  >
                    <BookOpenText size={11} className="mt-0.5 shrink-0" />
                    <span className="min-w-0">{c.question}</span>
                    {!sourceConflict && <b className="shrink-0 text-(--color-brand-ink)">{(c.score * 100).toFixed(0)}%</b>}
                  </button>
                ))}
              </div>}
            </div>
          )}
          {source && (
            <SourceModal citation={source} answerText={msg.content} evidenceMode={sourceConflict} onClose={() => setSource(null)} />
          )}
          {zoomed && <ImageLightbox image={zoomed} onClose={() => setZoomed(null)} />}
        </div>
      </div>
    </div>
  );
});

/** «1 шаг», «2 шага», «5 шагов». */
function stepsWord(count: number): string {
  const tail = count % 10;
  if (count % 100 >= 11 && count % 100 <= 14) return "шагов";
  if (tail === 1) return "шаг";
  if (tail >= 2 && tail <= 4) return "шага";
  return "шагов";
}

/** Цвет уверенности: высокая — зелёный, средняя — янтарный, низкая — приглушённый. */
function confidenceColor(value: number): string {
  if (value >= 0.7) return "text-(--color-ok-ink)";
  if (value >= 0.45) return "text-(--color-warn-ink)";
  return "text-(--color-ink-soft)";
}
