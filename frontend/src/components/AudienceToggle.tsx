import { Building2, Store } from "lucide-react";
import { useMeta } from "@/lib/store";
import type { Audience } from "@/lib/audience";
import "./audience-toggle.css";

const ICONS: Record<Audience, typeof Store> = { supplier: Store, customer: Building2 };

/**
 * Ползунок «Поставщик / Заказчик».
 *
 * Две кнопки под одной рамкой и бегунок, который скользит к выбранной: пока
 * роль не выбрана, бегунка нет и обе половины нейтральные — человеку видно,
 * что решение за ним. Подписи и пояснения приходят с сервера (/api/meta): он же
 * трактует эти значения в поиске и промпте, и держать их в двух местах нельзя.
 * Пояснение роли — во всплывающей подсказке: в одну строку ползунок помещается
 * и на приветственном экране, и в шапке диалога, и в виджете шириной 384px.
 *
 * Повторное нажатие на выбранную роль снимает выбор: поставщик, спрашивающий
 * за коллегу-заказчика, должен уметь вернуться к общему поиску.
 */
export function AudienceToggle({ value, onChange, compact = false }: {
  value: Audience | null;
  onChange: (audience: Audience | null) => void;
  /** В шапке диалога: ниже, а на узком экране — одни иконки. */
  compact?: boolean;
}) {
  const options = useMeta((s) => s.audiences);
  if (options.length === 0) return null;
  const index = options.findIndex((o) => o.key === value);
  return (
    <div className={`audience-toggle ${compact ? "audience-toggle--compact" : ""}`}
         role="radiogroup" aria-label="Кто вы: поставщик или заказчик">
      {index >= 0 && (
        <span aria-hidden className="audience-toggle__thumb"
              style={{ width: `calc((100% - 0.5rem) / ${options.length})`,
                       transform: `translateX(${index * 100}%)` }} />
      )}
      {options.map((option) => {
        const Icon = ICONS[option.key] ?? Store;
        const active = option.key === value;
        return (
          <button key={option.key} type="button" role="radio" aria-checked={active}
                  className={`audience-toggle__option ${active ? "audience-toggle__option--active" : ""}`}
                  title={`${option.label}: ${option.hint}`}
                  onClick={() => onChange(active ? null : option.key)}>
            <Icon size={compact ? 14 : 15} className="shrink-0" />
            <span className="audience-toggle__label">{option.label}</span>
          </button>
        );
      })}
    </div>
  );
}
