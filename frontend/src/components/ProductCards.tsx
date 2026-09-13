import { useId, useState } from "react";
import { ChevronLeft, ChevronRight } from "lucide-react";
import type { ArticleImage, ProductCard } from "@/lib/types";
import { useCarousel } from "@/components/carousel/useCarousel";
import { ProductTile } from "@/components/ProductTile";

/**
 * Карусель товаров каталога портала под ответом.
 *
 * Лентой, а не сеткой: карточки приезжают к реплике в чате, где ширины на сетку
 * нет, а число товаров заранее неизвестно. Листается пальцем, мышью, стрелками
 * и клавиатурой — в чат приходят и с телефона, и с ноутбука, и через виджет на
 * чужой странице, где на ленту остаётся 230 пикселей и в кадре ровно один товар.
 * Механика ленты — в useCarousel, карточка — ProductTile.
 */
export function ProductCards({ products, onZoom }: {
  products: ProductCard[];
  /** Крупный просмотр фотографии — тот же лайтбокс, что у иллюстраций методичек. */
  onZoom?: (image: ArticleImage) => void;
}) {
  const [broken, setBroken] = useState<Set<string>>(new Set());
  const markBroken = (url: string) =>
    setBroken((was) => (was.has(url) ? was : new Set(was).add(url)));

  const labelId = useId();
  const trackId = useId();
  const total = products.length;
  const { trackRef, nav, page, trackProps } = useCarousel(total);

  if (!total) return null;
  return (
    <div className="mt-3 border-t border-(--color-line) pt-3">
      <div className="mb-1.5 flex items-center gap-2">
        <div id={labelId} className="min-w-0 flex-1 text-xs text-(--color-ink-soft)">
          Товары каталога Портала поставщиков
        </div>
        {/* Счётчик и стрелки появляются, только когда листать действительно есть
            что: два товара помещаются целиком на десктопе и не помещаются в
            виджете, поэтому решает переполнение, а не их количество. */}
        {nav.overflow && (
          // shrink-0: в виджете на ленту остаётся 230 пикселей, и без этого
          // счётчик рвался бы по словам, а подпись налезала на стрелки.
          <div className="flex shrink-0 items-center gap-1">
            {/* Скринридер и так идёт по карточкам подряд — дублировать позицию
                в речь незачем, это подсказка глазами. */}
            <span aria-hidden="true" className="mr-1 text-xs text-(--color-ink-soft) tabular-nums">
              {nav.from === nav.to ? nav.from : `${nav.from}–${nav.to}`} из {total}
            </span>
            {/* aria-disabled, а не disabled: браузер снимает фокус с кнопки в тот
                же миг, когда она становится disabled, и человек, долиставший до
                упора с клавиатуры, терял бы фокус в body — следующий Tab начинал
                обход страницы заново. */}
            <button type="button" className="btn btn-ghost !min-h-0 !p-2 aria-disabled:pointer-events-none aria-disabled:opacity-45"
              onClick={() => { if (!nav.atStart) page(-1); }}
              aria-disabled={nav.atStart} aria-controls={trackId}
              aria-label="Предыдущие товары" title="Предыдущие товары">
              <ChevronLeft size={14} />
            </button>
            <button type="button" className="btn btn-ghost !min-h-0 !p-2 aria-disabled:pointer-events-none aria-disabled:opacity-45"
              onClick={() => { if (!nav.atEnd) page(1); }}
              aria-disabled={nav.atEnd} aria-controls={trackId}
              aria-label="Следующие товары" title="Следующие товары">
              <ChevronRight size={14} />
            </button>
          </div>
        )}
      </div>

      {/* Вынос за поля живёт на обёртке, а не на ленте: тогда затухания по краям
          совпадают с настоящими краями ленты, а не уезжают на четыре пикселя. */}
      <div className="relative -mx-1">
        <div
          ref={trackRef}
          id={trackId}
          role="group"
          aria-labelledby={labelId}
          // Прокручиваемая область обязана быть достижима с клавиатуры, но
          // плодить пустую остановку там, где прокручивать нечего, не за чем.
          tabIndex={nav.overflow ? 0 : -1}
          {...trackProps}
          // Нативное перетаскивание картинки или ссылки обрывает поток
          // pointermove и вешает призрак под курсором — гасим разом за всех детей.
          onDragStartCapture={(e) => e.preventDefault()}
          // Курсор «сжатой руки» задан и потомкам: у фотографии свой zoom-in, у
          // ссылок — pointer, и без этого во время жеста рука была бы видна
          // только в зазорах между карточками.
          className={`flex snap-x snap-mandatory gap-2 overflow-x-auto overscroll-x-contain scroll-px-1 px-1 py-1
            focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-(--color-brand)
            [&[data-dragging]]:cursor-grabbing [&[data-dragging]_*]:cursor-grabbing
            [&[data-dragging]]:select-none
            ${nav.overflow ? "cursor-grab" : ""}`}
        >
          {products.map((product) => (
            <ProductTile key={product.url ?? product.sku_id ?? product.name} product={product}
              broken={Boolean(product.image && broken.has(product.image))}
              onBroken={markBroken} onZoom={onZoom} />
          ))}
        </div>

        {/* Затухания по краям — единственный сигнал «дальше есть ещё» на телефоне,
            где в кадре один товар. На токене поверхности: фон пузыря задаёт .card,
            и захардкоженный белый дал бы светлую полосу поперёк тёмной темы. */}
        {nav.overflow && !nav.atStart && (
          <div aria-hidden="true"
            className="pointer-events-none absolute inset-y-0 left-0 w-6 bg-linear-to-r from-(--color-surface) to-transparent" />
        )}
        {nav.overflow && !nav.atEnd && (
          <div aria-hidden="true"
            className="pointer-events-none absolute inset-y-0 right-0 w-6 bg-linear-to-l from-(--color-surface) to-transparent" />
        )}
      </div>
    </div>
  );
}
