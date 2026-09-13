import { Ban, CircleCheck, Clock, Headset, History, MessageCircle, Plus, type LucideIcon } from "lucide-react";
import type { ReactNode } from "react";
import { addresseeText } from "./addressee";
import { isOpen, type RequestCard } from "./useRequestHistory";

/** Статус обращения словами человека, а не кодом из базы. Цвет статуса карточка
 *  не несёт: открытая залита брендовым — цветные подписи на ней не читались бы.
 *  Различают иконка и слово. */
const STATUS: Record<string, { label: string; icon: LucideIcon }> = {
  active: { label: "Отвечает Валерчик", icon: MessageCircle },
  waiting_operator: { label: "Ждёт специалиста", icon: Clock },
  operator_active: { label: "У специалиста", icon: Headset },
  resolved: { label: "Завершено", icon: CircleCheck },
  closed_violation: { label: "Закрыто за нарушение правил", icon: Ban },
};

const TIME = new Intl.DateTimeFormat("ru-RU", { hour: "2-digit", minute: "2-digit" });
const DAY = new Intl.DateTimeFormat("ru-RU", { day: "numeric", month: "short" });
const FULL = new Intl.DateTimeFormat("ru-RU", { day: "2-digit", month: "2-digit", year: "numeric" });

/** Сегодня — время, вчера — «вчера», в этом году — дата, раньше — с годом. */
export function whenText(iso: string, now = new Date()): string {
  const at = new Date(iso);
  if (Number.isNaN(at.getTime())) return "";
  const startOfDay = (d: Date) => new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();
  const days = Math.round((startOfDay(now) - startOfDay(at)) / 86_400_000);
  if (days <= 0) return TIME.format(at);
  if (days === 1) return "вчера";
  return at.getFullYear() === now.getFullYear() ? DAY.format(at).replace(".", "") : FULL.format(at);
}

function RequestItem({ item, current, onOpen }: { item: RequestCard; current: boolean; onOpen: () => void }) {
  const status = STATUS[item.status] ?? STATUS.active!;
  const Icon = status.icon;
  const routed = item.line !== null && item.line_label !== "";
  const addressee = routed
    ? addresseeText({ line: item.line!, lineLabel: item.line_label, externalLabel: item.addressee_label }) : "";
  const when = whenText(item.updated_at);
  // Сервер собирает заголовок из первого вопроса человека и присылает пустым, когда
  // взять его не из чего. Пустая строка оставляла карточку без подписи и сбивала её
  // поля: сверху был только статус, снизу — отступ несуществующего заголовка.
  // Бэкенд старой сборки поля не присылает вовсе — карточка не должна ронять список.
  const title = (item.title as string | undefined)?.trim() || "Новое обращение";
  // Скринридер читал бы разметку карточки подряд: «Завершено01:29Позовите…». Имя
  // собираем фразами: вопрос, статус, адресат, новый ответ, когда.
  const name = [title, status.label, addressee && `Кому: ${addressee}`,
                item.unread && "Новый ответ", when].filter(Boolean).join(". ");
  // Брендовым залита только открытая карточка: красный на всём списке не выделял
  // ничего. Остальные — нейтральные, с обводкой вместо тени: тень на каждой
  // карточке превращала список в рябь.
  const soft = current ? "text-white/85" : "text-(--color-ink-soft)";
  return (
    <li>
      <button type="button" onClick={onOpen} aria-current={current ? "true" : undefined} aria-label={name}
        className={`w-full cursor-pointer rounded-2xl px-3 py-2.5 text-left transition-[background-color,box-shadow] duration-200 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-(--color-brand) ${
          current
            ? "bg-linear-to-br from-(--color-brand-lift) to-(--color-brand) text-white shadow-[0_3px_10px_oklch(54%_0.21_25/0.35)]"
            : "bg-(--color-surface) text-(--color-ink) ring-1 ring-inset ring-(--color-line) hover:bg-(--color-surface-2)"}`}>
        <span className="flex items-center gap-1.5 text-xs">
          <span className={`flex min-w-0 items-center gap-1 ${soft}`}>
            <Icon size={13} className="shrink-0" aria-hidden />
            <span className="truncate">{status.label}</span>
          </span>
          {item.unread && (
            <span className={`shrink-0 rounded-full px-1.5 py-px text-[11px] font-semibold ${
              current ? "bg-white text-(--color-brand-ink)" : "bg-(--color-brand) text-white"}`}>
              Новый ответ
            </span>
          )}
          {when && (
            <time className={`ml-auto shrink-0 tabular-nums ${soft}`} dateTime={item.updated_at}>
              {when}
            </time>
          )}
        </span>
        <span className="mt-1 line-clamp-2 text-sm font-medium leading-snug">{title}</span>
        {routed && (
          <span className={`mt-0.5 block truncate text-xs ${soft}`}>Кому: {addressee}</span>
        )}
      </button>
    </li>
  );
}

function Group({ title, items, currentId, onOpen }: {
  title: string; items: RequestCard[]; currentId: string | null; onOpen: (item: RequestCard) => void;
}) {
  if (!items.length) return null;
  return (
    <section className="mt-4 first:mt-0">
      <h3 className="mb-1 px-3 text-xs font-semibold text-(--color-ink-soft)">{title}</h3>
      <ul className="space-y-1.5">
        {items.map((item) => (
          <RequestItem key={item.session_id} item={item} current={item.session_id === currentId}
            onOpen={() => onOpen(item)} />
        ))}
      </ul>
    </section>
  );
}

/**
 * «Мои обращения»: открытые сверху, завершённые ниже. Одна и та же разметка в
 * боковой панели на широком экране и в модалке на телефоне и в виджете.
 */
export function RequestList({ items, loading, error, currentId, onOpen, onNew, onRetry, toggle, heading = true,
                             collapsed = false }: {
  items: RequestCard[];
  loading: boolean;
  error: string | null;
  currentId: string | null;
  onOpen: (item: RequestCard) => void;
  onNew: () => void;
  onRetry: () => void;
  /** Переключатель панели — первым в шапке, на том же месте, где он у свёрнутой. */
  toggle?: ReactNode;
  /** В модалке заголовок уже есть у самой модалки; заодно это признак панели. */
  heading?: boolean;
  /** Панель свёрнута в полоску: заголовок и список уезжают, кнопки остаются. */
  collapsed?: boolean;
}) {
  const open = items.filter((item) => isOpen(item.status));
  const done = items.filter((item) => !isOpen(item.status));
  return (
    <div className="flex min-h-0 flex-1 flex-col">
      {/* flex-wrap вместо смены направления: когда панель сузилась, «Новый диалог»
          переносится под переключатель сам — менять flex-direction нельзя, переход
          по нему не анимируется. Схлопнутый заголовок обнуляет и высоту, иначе
          в колонке от него остался бы пустой ряд. */}
      <div className="flex flex-wrap items-center gap-1 pb-3">
        {toggle}
        {heading && (
          <h2 aria-hidden={collapsed || undefined}
            className={`mr-auto overflow-hidden truncate px-1 text-sm font-bold transition-[max-width,max-height,opacity] duration-300 ease-(--ease-out-soft) ${
              collapsed ? "max-h-0 max-w-0 opacity-0" : "max-h-6 max-w-full opacity-100"}`}>
            Мои обращения
          </h2>
        )}
        {heading ? (
          // В панели — иконка, как у свёрнутой полоски: те же две кнопки, только в строку
          <button type="button" onClick={onNew} aria-label="Новый диалог" title="Новый диалог"
            className="flex size-9 shrink-0 cursor-pointer items-center justify-center rounded-full text-(--color-ink-soft) transition-colors hover:bg-(--color-surface-2) hover:text-(--color-ink) focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-(--color-brand)">
            <Plus size={18} aria-hidden />
          </button>
        ) : (
          <button type="button" onClick={onNew} className="btn btn-ghost !min-h-0 w-full !py-2 text-xs">
            <Plus size={14} aria-hidden /> Новый диалог
          </button>
        )}
      </div>
      {/* Ширина фиксирована шириной развёрнутой панели: иначе за те же 300 мс
          карточки пересобирали бы раскладку в схлопывающейся колонке. inert убирает
          уехавший список из фокуса и из дерева доступности. Поля внутри прокрутки —
          место под тень открытой карточки: overflow этого блока и панели обрезал её. */}
      <div aria-busy={loading} inert={collapsed}
        className={`min-h-0 flex-1 overflow-y-auto px-2 pt-1 pb-4 transition-opacity duration-300 ease-(--ease-out-soft) ${
          heading ? "w-72 shrink-0" : ""} ${collapsed ? "opacity-0" : "opacity-100"}`}>
        {error && (
          <p role="alert" className="mb-3 px-3 text-xs text-(--color-brand-ink)">
            {error}.{" "}
            <button type="button" className="cursor-pointer font-medium underline underline-offset-2" onClick={onRetry}>
              Повторить
            </button>
          </p>
        )}
        {loading && !items.length ? (
          <div className="space-y-2 px-1" role="status">
            <span className="sr-only">Загружаем обращения</span>
            {[0, 1, 2].map((n) => (
              <div key={n} aria-hidden className="h-16 rounded-2xl bg-(--color-surface-2) motion-safe:animate-pulse" />
            ))}
          </div>
        ) : !items.length && !error ? (
          <div className="flex flex-col items-start gap-2 px-3 py-2 text-sm text-(--color-ink-soft)">
            <History size={18} aria-hidden />
            <p className="leading-relaxed">
              Здесь появятся ваши обращения и их статус. Список хранится в этом браузере.
            </p>
          </div>
        ) : (
          <>
            <Group title="Открытые" items={open} currentId={currentId} onOpen={onOpen} />
            <Group title="Завершённые" items={done} currentId={currentId} onOpen={onOpen} />
          </>
        )}
      </div>
    </div>
  );
}
