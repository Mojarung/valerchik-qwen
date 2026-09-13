import { useCallback, useEffect, useRef, useState } from "react";
import { Maximize2, Minus, Plus, X } from "lucide-react";
import { assetUrl } from "@/lib/api";

/**
 * Просмотр иллюстрации крупно, не уходя со страницы.
 *
 * Скриншоты из инструкций — это интерфейс с мелкими подписями и выносками
 * «(1)», «(2)», на которые ссылается текст. В ленте чата они видны как
 * миниатюры, поэтому нужен зум: без него картинка есть, а прочитать её нельзя.
 *
 * Управление намеренно избыточное — колесом, кнопками, двойным щелчком: в чат
 * приходят и с мыши, и с тачпада, и с телефона.
 */

const MIN_SCALE = 1;
const MAX_SCALE = 6;
const STEP = 0.5;

export interface LightboxImage {
  url: string;
  caption?: string;
  page?: number | null;
}

export function ImageLightbox({ image, onClose }: {
  image: LightboxImage;
  onClose: () => void;
}) {
  const [scale, setScale] = useState(1);
  const [offset, setOffset] = useState({ x: 0, y: 0 });
  const dragFrom = useRef<{ x: number; y: number } | null>(null);
  // Перетаскивание увеличенной картинки кончается щелчком — он не должен закрывать просмотр
  const dragged = useRef(false);
  const [dragging, setDragging] = useState(false);

  const clamp = (value: number) => Math.min(MAX_SCALE, Math.max(MIN_SCALE, value));

  const zoomBy = useCallback((delta: number) => {
    setScale((current) => {
      const next = clamp(current + delta);
      if (next === MIN_SCALE) setOffset({ x: 0, y: 0 });   // вернулись к исходному — центрируем
      return next;
    });
  }, []);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        // Иначе Esc закрыл бы заодно и модалку источника под лайтбоксом
        e.stopPropagation();
        onClose();
      }
      if (e.key === "+" || e.key === "=") zoomBy(STEP);
      if (e.key === "-") zoomBy(-STEP);
    };
    // capture: перехватываем раньше обработчиков нижележащих модалок
    window.addEventListener("keydown", onKey, true);
    // Прокрутка страницы под оверлеем сбивает с толку — блокируем на время просмотра
    const overflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      window.removeEventListener("keydown", onKey, true);
      document.body.style.overflow = overflow;
    };
  }, [onClose, zoomBy]);

  return (
    <div
      className="fixed inset-0 z-[60] flex flex-col bg-black/80 backdrop-blur-sm"
      // Щелчок по фону закрывает только лайтбокс: без остановки всплытия он
      // доходит до подложки модалки источника и закрывает её тоже.
      onClick={(e) => { e.stopPropagation(); onClose(); }}
      role="dialog"
      aria-modal="true"
      aria-label={image.caption || "Просмотр иллюстрации"}
    >
      <div className="flex items-center gap-2 px-3 py-2 text-white"
        onClick={(e) => { e.stopPropagation(); if (e.target === e.currentTarget) onClose(); }}>
        <div className="mr-auto min-w-0 truncate text-xs">
          {image.caption}
          {image.page ? <span className="opacity-60"> · стр. {image.page}</span> : null}
        </div>
        <button className="rounded-lg p-2 transition-colors hover:bg-white/15" title="Отдалить"
          onClick={() => zoomBy(-STEP)} aria-label="Отдалить">
          <Minus size={16} />
        </button>
        <span className="w-12 text-center text-xs tabular-nums">{Math.round(scale * 100)}%</span>
        <button className="rounded-lg p-2 transition-colors hover:bg-white/15" title="Приблизить"
          onClick={() => zoomBy(STEP)} aria-label="Приблизить">
          <Plus size={16} />
        </button>
        <button className="rounded-lg p-2 transition-colors hover:bg-white/15" title="Исходный размер"
          onClick={() => { setScale(1); setOffset({ x: 0, y: 0 }); }} aria-label="Исходный размер">
          <Maximize2 size={16} />
        </button>
        <button className="rounded-lg p-2 transition-colors hover:bg-white/15" title="Закрыть (Esc)"
          onClick={onClose} aria-label="Закрыть">
          <X size={16} />
        </button>
      </div>

      <div
        className="flex min-h-0 flex-1 items-center justify-center overflow-hidden p-3"
        // Эта область занимает почти весь чёрный фон: щелчок мимо картинки закрывает
        // просмотр, как и по остальному фону, а по самой картинке — нет.
        onClick={(e) => {
          e.stopPropagation();
          if (e.target === e.currentTarget && !dragged.current) onClose();
        }}
        // passive-обработчик колеса нельзя отменить, поэтому предотвращаем прокрутку страницы здесь
        onWheel={(e) => zoomBy(e.deltaY < 0 ? STEP / 2 : -STEP / 2)}
        onDoubleClick={() => (scale > 1 ? (setScale(1), setOffset({ x: 0, y: 0 })) : zoomBy(1))}
        onPointerDown={(e) => {
          dragged.current = false;
          if (scale === 1) return;
          dragFrom.current = { x: e.clientX - offset.x, y: e.clientY - offset.y };
          setDragging(true);
          (e.target as HTMLElement).setPointerCapture?.(e.pointerId);
        }}
        onPointerMove={(e) => {
          if (!dragFrom.current) return;
          dragged.current = true;
          setOffset({ x: e.clientX - dragFrom.current.x, y: e.clientY - dragFrom.current.y });
        }}
        onPointerUp={() => { dragFrom.current = null; setDragging(false); }}
        style={{ cursor: scale > 1 ? (dragging ? "grabbing" : "grab") : "zoom-in" }}
      >
        <img
          src={assetUrl(image.url)}
          alt={image.caption || "Иллюстрация из инструкции"}
          draggable={false}
          className="max-h-full max-w-full select-none rounded-lg bg-white shadow-2xl"
          style={{
            transform: `translate(${offset.x}px, ${offset.y}px) scale(${scale})`,
            transition: dragging ? "none" : "transform 0.12s ease-out",
          }}
        />
      </div>

      <div className="px-3 pb-2 text-center text-xs text-white/60">
        колесо мыши — масштаб · двойной щелчок — вписать · перетаскивание — сдвиг · Esc — закрыть
      </div>
    </div>
  );
}
