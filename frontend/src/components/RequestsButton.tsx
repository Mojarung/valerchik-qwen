import { History, PanelLeftClose, PanelLeftOpen } from "lucide-react";
import { useRequestsSheet } from "@/lib/requestsSheet";

/** Счётчик открытых обращений в углу кнопки; точка — ответ пришёл в уже завершённое. */
function Badge({ openCount, unread }: { openCount: number; unread: boolean }) {
  if (openCount > 0) {
    return (
      <span aria-hidden className={`absolute -top-1.5 -right-1.5 min-w-[18px] rounded-full px-1 text-center text-[11px] font-semibold leading-[18px] tabular-nums ring-2 ring-(--color-surface) ${
        unread ? "bg-(--color-brand) text-white" : "bg-(--color-ink) text-(--color-surface)"}`}>
        {openCount}
      </span>
    );
  }
  return unread
    ? <span aria-hidden className="absolute -top-0.5 -right-0.5 size-2.5 rounded-full bg-(--color-brand) ring-2 ring-(--color-surface)" />
    : null;
}

function useSummaryLabel(base: string): { label: string; openCount: number; unread: boolean } {
  const openCount = useRequestsSheet((s) => s.openCount);
  const unread = useRequestsSheet((s) => s.unread);
  const label = unread ? `${base}, есть новый ответ` : openCount ? `${base}, открытых: ${openCount}` : base;
  return { label, openCount, unread };
}

/**
 * «Мои обращения» там, где боковой панели нет: навигация сайта на телефоне и
 * планшете, подвал виджета. Открывает список в модалке.
 */
export function RequestsButton({ className = "", compact = false }: { className?: string; compact?: boolean }) {
  const setOpen = useRequestsSheet((s) => s.setOpen);
  const { label, openCount, unread } = useSummaryLabel("Мои обращения");
  return (
    <button type="button" onClick={() => setOpen(true)} aria-label={label} title="Мои обращения"
      className={`${compact ? "rate-btn" : "btn btn-ghost !min-h-0 !px-3.5 !py-2"} relative shrink-0 ${className}`}>
      <History size={compact ? 12 : 15} aria-hidden />
      <Badge openCount={openCount} unread={unread} />
    </button>
  );
}

/**
 * Переключатель боковой панели — как в чатах нейросетей: одна кнопка на одном и
 * том же месте и прячет панель, и возвращает её. Ни числа открытых, ни точки
 * нового ответа у свёрнутой нет — в узкой полоске они только шумели.
 */
export function PanelToggle() {
  const collapsed = useRequestsSheet((s) => s.panelCollapsed);
  const setPanelCollapsed = useRequestsSheet((s) => s.setPanelCollapsed);
  const label = collapsed ? "Показать мои обращения" : "Скрыть мои обращения";
  const Icon = collapsed ? PanelLeftOpen : PanelLeftClose;
  return (
    <button type="button" onClick={() => setPanelCollapsed(!collapsed)} aria-label={label}
      aria-expanded={!collapsed} aria-controls="requests-panel"
      title={label}
      className="flex size-9 shrink-0 cursor-pointer items-center justify-center rounded-full text-(--color-ink-soft) transition-colors hover:bg-(--color-surface-2) hover:text-(--color-ink) focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-(--color-brand)">
      <Icon size={18} aria-hidden />
    </button>
  );
}
