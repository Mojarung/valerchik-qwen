import { useEffect, useId, useRef, type ReactNode, type RefObject } from "react";
import { X } from "lucide-react";

/**
 * Общая модалка. Раньше подложка `fixed inset-0 … bg-black/30` была свёрстана в
 * трёх местах (KbPage, OperatorPage, SourceModal), и только лайтбокс умел
 * закрываться по Escape. Здесь собрано то, без чего диалог не считается
 * доступным: `aria-modal`, имя из заголовка, фокус внутрь при открытии и
 * возврат на инициатор при закрытии, ловушка Tab, Escape, запрет прокрутки
 * страницы под ней.
 */
const FOCUSABLE =
  'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), ' +
  'textarea:not([disabled]), summary, [tabindex]:not([tabindex="-1"])';

export function Modal({ title, onClose, children, size = "md", initialFocus, hideTitle = false }: {
  title: string;
  onClose: () => void;
  children: ReactNode;
  /** md — формы и статьи, lg — широкие редакторы, xl — рабочие пространства. */
  size?: "md" | "lg" | "xl";
  /** Куда поставить фокус при открытии; по умолчанию — сам диалог без прокрутки содержимого. */
  initialFocus?: RefObject<HTMLElement | null>;
  /** Заголовок нужен ассистивным технологиям всегда; визуально его можно спрятать,
   *  если у содержимого свой заголовок (карточка источника). */
  hideTitle?: boolean;
}) {
  const panelRef = useRef<HTMLDivElement>(null);
  const titleId = useId();
  // Страницы передают onClose новой стрелкой на каждый рендер. Последнюю держим
  // в ref: эффект ниже живёт, пока открыт диалог, а не перезапускается от каждой
  // буквы в поле формы — иначе фокус уходил к инициатору и обратно на первое поле.
  const onCloseRef = useRef(onClose);
  useEffect(() => {
    onCloseRef.current = onClose;
  });

  useEffect(() => {
    const panel = panelRef.current;
    if (!panel) return;
    // Инициатор запоминается до переноса фокуса — потом activeElement уже внутри.
    const opener = document.activeElement as HTMLElement | null;
    const focusables = () => [...panel.querySelectorAll<HTMLElement>(FOCUSABLE)];
    (initialFocus?.current ?? panel).focus();

    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";

    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.stopPropagation();
        onCloseRef.current();
        return;
      }
      if (e.key !== "Tab") return;
      const items = focusables();
      if (items.length === 0) {
        e.preventDefault();
        return;
      }
      const first = items[0]!;
      const last = items[items.length - 1]!;
      // Tab по кругу: за последним элементом снова первый, за первым — последний.
      if (e.shiftKey && (document.activeElement === first || document.activeElement === panel)) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && document.activeElement === last) {
        e.preventDefault();
        first.focus();
      }
    };
    document.addEventListener("keydown", onKey, true);
    return () => {
      document.removeEventListener("keydown", onKey, true);
      document.body.style.overflow = previousOverflow;
      opener?.focus?.();
    };
  }, [initialFocus]);

  const width = size === "xl" ? "max-w-5xl" : size === "lg" ? "max-w-3xl" : "max-w-xl";
  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/35 p-4"
      onClick={onClose}
      data-modal-backdrop
    >
      <div
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        tabIndex={-1}
        className={`card animate-(--animate-pop) flex max-h-[90vh] w-full ${width} flex-col overflow-hidden outline-none`}
        style={{ overscrollBehavior: "contain" }}
        onClick={(e) => e.stopPropagation()}
      >
        {hideTitle ? (
          <h2 id={titleId} className="sr-only">{title}</h2>
        ) : (
          <div className="flex items-center gap-3 border-b border-(--color-line) px-5 py-3">
            <h2 id={titleId} className="min-w-0 flex-1 text-base font-bold text-balance">
              {title}
            </h2>
            <button
              type="button"
              className="btn btn-ghost size-11 shrink-0 !p-0"
              onClick={onClose}
              aria-label="Закрыть"
            >
              <X size={18} aria-hidden="true" />
            </button>
          </div>
        )}
        <div className={`flex min-h-0 flex-1 flex-col overflow-y-auto ${hideTitle ? "" : "px-5 pb-5 pt-4"}`}>
          {children}
        </div>
      </div>
    </div>
  );
}
