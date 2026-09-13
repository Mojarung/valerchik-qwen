import { ExternalLink, ImageOff } from "lucide-react";
import { assetUrl } from "@/lib/api";
import type { ArticleImage, ProductCard } from "@/lib/types";

/** Как называется цена: у портала их три, и путать их нельзя. */
const PRICE_KIND: Record<string, string> = {
  reference: "референтная",
  median: "медианная",
  offer: "цена предложения",
};

/** В модульной области: сборка Intl.NumberFormat дороже самого форматирования,
 *  а карточек в ленте до восьми и перерисовываются они на каждый свайп. */
const MONEY = new Intl.NumberFormat("ru-RU", { maximumFractionDigits: 0 });

function price(product: ProductCard): string | null {
  if (!product.price) return null;
  const money = MONEY.format(product.price);
  return product.unit ? `${money} ₽ / ${product.unit}` : `${money} ₽`;
}

/** Карточка товара в ленте: фото (крупно по нажатию), название-ссылка на портал и цена.
 *  Ссылка ведёт на страницу товара портала — бот показывает, а покупают там. */
export function ProductTile({ product, broken, onBroken, onZoom }: {
  product: ProductCard;
  /** Фото уже не доехало: у портала свои сбои, а мы всего лишь посредник. */
  broken: boolean;
  onBroken: (url: string) => void;
  onZoom?: (image: ArticleImage) => void;
}) {
  // Адреса картинок сервер отдаёт относительными — внутри расширения им нужен его origin
  const shown = product.image && !broken ? assetUrl(product.image) : null;
  const money = price(product);
  return (
    <div className="w-40 shrink-0 snap-start overflow-hidden rounded-xl border border-(--color-line) bg-(--color-surface)">
      {/* Белая подложка — только под саму фотографию: у портала они
          сняты на белом. Под значком «фото нет» это была бы светлая
          плита поперёк тёмной темы с еле различимым значком. */}
      <div className={`relative flex h-32 items-center justify-center ${shown ? "bg-white" : "bg-(--color-surface-2)"}`}>
        {shown ? (
          <button
            type="button"
            className="size-full cursor-zoom-in focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-(--color-brand)"
            onClick={() => onZoom?.({
              url: product.image_full ? assetUrl(product.image_full) : shown,
              caption: product.name, page: null, figure: null,
            })}
            aria-label={`Открыть фото крупно: ${product.name}`}
            title="Открыть крупно"
          >
            {/* Сломанная картинка показывает alt-текст поверх пустого белого
                квадрата — это выглядит как ошибка вёрстки. Значок «фото нет»
                честнее и спокойнее. */}
            <img src={shown} alt={product.name} loading="lazy" draggable={false}
              onError={() => onBroken(product.image ?? shown)}
              className="size-full object-contain p-1.5" />
          </button>
        ) : (
          <ImageOff size={20} className="text-(--color-ink-soft) opacity-50" />
        )}
        {/* Заглушка категории — не фотография этого товара. Молча выдавать
            одно за другое нельзя: человек решит, что видит именно эту модель.
            pointer-events-none: плашка лежит поверх кнопки и иначе съедала бы
            клик по нижнему правому углу фотографии. */}
        {shown && product.own_image === false && (
          <span className="pointer-events-none absolute right-1 bottom-1 rounded bg-black/55 px-1 text-xs text-white">
            фото категории
          </span>
        )}
      </div>
      <div className="space-y-0.5 p-2">
        <a href={product.url} target="_blank" rel="noreferrer noopener" draggable={false}
          className="line-clamp-2 text-xs leading-snug font-medium text-(--color-ink) hover:text-(--color-brand-ink)"
          title={product.name}>
          {product.name}
        </a>
        {money ? (
          <div className="text-xs font-semibold text-(--color-ink)">
            {money}
            {product.price_kind && (
              <span className="ml-1 text-xs font-normal text-(--color-ink-soft)">
                {PRICE_KIND[product.price_kind] ?? product.price_kind}
              </span>
            )}
          </div>
        ) : (
          <div className="text-xs text-(--color-ink-soft)">цена не указана</div>
        )}
        {/* Ведёт туда же, куда и заголовок: в табуляции внутри ленты это
            была бы вторая остановка на каждый товар без нового смысла. */}
        <a href={product.url} target="_blank" rel="noreferrer noopener"
          draggable={false} tabIndex={-1}
          className="inline-flex items-center gap-1 text-xs text-(--color-ink-soft) hover:text-(--color-brand-ink)">
          на портале <ExternalLink size={9} />
        </a>
      </div>
    </div>
  );
}
