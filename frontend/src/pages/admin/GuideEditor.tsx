import { ChevronDown, ChevronUp, Plus, Trash2 } from "lucide-react";
import type { GuideLocator } from "@/lib/types";

/**
 * Форма сценария по порталу: шаги и локаторы.
 *
 * Локатор — это то, чем расширение находит элемент на странице. Их несколько
 * на шаг и пробуются они сверху вниз: первый устойчивый (data-testid), дальше
 * запасные (класс, текст). Поэтому порядок редактируемый, а не «один селектор».
 */

export interface EditableStep {
  url: string;
  title: string;
  text: string;
  target: GuideLocator[];
  action: "" | "click" | "input";
  optional: boolean;
  /** Куда перейти, если человек не на той странице. */
  go: string;
  /** «Шаг доступен только с предыдущего» — переход запрещён явно. */
  noGo: boolean;
}

export interface EditableGuide {
  id: string;
  title: string;
  audience: string;
  /** Формулировки вопроса — по одной в строке: так их и правят. */
  questions: string;
  steps: EditableStep[];
  enabled: boolean;
  source: string;
}

export const EMPTY_STEP: EditableStep = {
  url: "", title: "", text: "", target: [{ css: "" }],
  action: "click", optional: false, go: "", noGo: false,
};

const LABEL = "mb-1 block text-xs font-semibold tracking-wide text-(--color-ink-soft) uppercase";

export function GuideEditor({ value, onChange }: {
  value: EditableGuide;
  onChange: (next: EditableGuide) => void;
}) {
  const patch = (fields: Partial<EditableGuide>) => onChange({ ...value, ...fields });
  const patchStep = (index: number, fields: Partial<EditableStep>) =>
    patch({ steps: value.steps.map((s, i) => (i === index ? { ...s, ...fields } : s)) });
  const moveStep = (index: number, delta: number) => {
    const next = [...value.steps];
    const target = index + delta;
    if (target < 0 || target >= next.length) return;
    const moved = next[index];
    const replaced = next[target];
    if (!moved || !replaced) return;
    next[index] = replaced;
    next[target] = moved;
    patch({ steps: next });
  };

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-3">
        <div>
          <span className={LABEL}>Идентификатор</span>
          <input aria-label="Идентификатор" className="input font-mono !text-xs" value={value.id} placeholder="register-supplier"
            onChange={(e) => patch({ id: e.target.value.toLowerCase().replace(/[^a-z0-9-]/g, "") })} />
        </div>
        <div>
          <span className={LABEL}>Кому</span>
          <select aria-label="Кому" className="input" value={value.audience}
            onChange={(e) => patch({ audience: e.target.value })}>
            <option value="all">Всем</option>
            <option value="supplier">Поставщику</option>
            <option value="customer">Заказчику</option>
          </select>
        </div>
      </div>

      <div>
        <span className={LABEL}>Название</span>
        <input aria-label="Название" className="input" value={value.title} placeholder="Регистрация поставщика"
          onChange={(e) => patch({ title: e.target.value })} />
      </div>

      <div>
        <span className={LABEL}>Формулировки вопроса, по одной в строке</span>
        <textarea aria-label="Формулировки вопроса, по одной в строке" className="input h-20 resize-none !text-xs" value={value.questions}
          placeholder={"как зарегистрироваться на портале\nкак завести личный кабинет"}
          onChange={(e) => patch({ questions: e.target.value })} />
        <p className="mt-1 text-xs text-(--color-ink-soft)">
          Сценарий предложится, когда вопрос человека почти целиком покрывает одну из формулировок.
        </p>
      </div>

      <div className="space-y-3">
        {value.steps.map((step, index) => (
          <div key={index} className="rounded-2xl border border-(--color-line) p-3">
            <div className="mb-2 flex items-center gap-2">
              <span className="rounded-full bg-(--color-brand-soft) px-2 py-0.5 text-xs font-bold text-(--color-brand-ink)">
                Шаг {index + 1}
              </span>
              <div className="ml-auto flex gap-1">
                <button className="btn btn-ghost !p-1.5" title="Выше" onClick={() => moveStep(index, -1)}>
                  <ChevronUp size={14} />
                </button>
                <button className="btn btn-ghost !p-1.5" title="Ниже" onClick={() => moveStep(index, 1)}>
                  <ChevronDown size={14} />
                </button>
                <button className="btn btn-ghost !p-1.5 hover:text-(--color-brand-ink)" title="Удалить шаг"
                  onClick={() => patch({ steps: value.steps.filter((_, i) => i !== index) })}>
                  <Trash2 size={14} />
                </button>
              </div>
            </div>

            <input className="input mb-2" placeholder="Заголовок: «Нажмите Зарегистрироваться»"
              value={step.title} onChange={(e) => patchStep(index, { title: e.target.value })} />
            <textarea className="input mb-2 h-16 resize-none !text-xs" placeholder="Что произойдёт и на что смотреть"
              value={step.text} onChange={(e) => patchStep(index, { text: e.target.value })} />

            <div className="mb-2 grid grid-cols-2 gap-2">
              <div>
                <span className={LABEL}>Страница шага</span>
                <input aria-label="Страница шага" className="input font-mono !text-xs" placeholder="/profile/user" value={step.url}
                  onChange={(e) => patchStep(index, { url: e.target.value })} />
              </div>
              <div>
                <span className={LABEL}>Действие</span>
                <select aria-label="Действие" className="input" value={step.action}
                  onChange={(e) => patchStep(index, { action: e.target.value as EditableStep["action"] })}>
                  <option value="">Просто показать</option>
                  <option value="click">Нажать (можно за человека)</option>
                  <option value="input">Заполнить самому</option>
                </select>
              </div>
            </div>

            <div className="mb-2 grid grid-cols-2 gap-2">
              <div>
                <span className={LABEL}>Перейти, если не там</span>
                <input aria-label="Перейти, если не там" className="input font-mono !text-xs" placeholder="/registration" value={step.go}
                  disabled={step.noGo}
                  onChange={(e) => patchStep(index, { go: e.target.value })} />
              </div>
              <div className="flex flex-col justify-end gap-1 pb-1 text-xs">
                <label className="flex items-center gap-2">
                  <input type="checkbox" checked={step.noGo}
                    onChange={(e) => patchStep(index, { noGo: e.target.checked })} />
                  Только с предыдущего шага
                </label>
                <label className="flex items-center gap-2">
                  <input type="checkbox" checked={step.optional}
                    onChange={(e) => patchStep(index, { optional: e.target.checked })} />
                  Можно не найти (за входом в кабинет)
                </label>
              </div>
            </div>

            <span className={LABEL}>Как найти элемент</span>
            <div className="space-y-1.5">
              {step.target.map((locator, li) => (
                <LocatorRow key={li} value={locator}
                  onChange={(next) => patchStep(index, {
                    target: step.target.map((l, i) => (i === li ? next : l)),
                  })}
                  onRemove={step.target.length > 1
                    ? () => patchStep(index, { target: step.target.filter((_, i) => i !== li) })
                    : undefined} />
              ))}
              <button className="btn btn-ghost !py-1 text-xs"
                onClick={() => patchStep(index, { target: [...step.target, { css: "" }] })}>
                <Plus size={12} /> Запасной способ
              </button>
            </div>
          </div>
        ))}
        <button className="btn btn-ghost text-xs"
          onClick={() => patch({ steps: [...value.steps, { ...EMPTY_STEP }] })}>
          <Plus size={14} /> Добавить шаг
        </button>
      </div>
    </div>
  );
}

function LocatorRow({ value, onChange, onRemove }: {
  value: GuideLocator;
  onChange: (next: GuideLocator) => void;
  onRemove?: () => void;
}) {
  return (
    <div className="flex flex-wrap items-center gap-1.5">
      <input className="input min-w-40 flex-1 font-mono !text-xs" placeholder='[data-testid="…"] или .class'
        value={value.css ?? ""} onChange={(e) => onChange({ ...value, css: e.target.value })} />
      {/* Ссылку записывает рекордер, и без этого поля она была невидимой:
          админ видел пустую строку и не мог ни исправить её, ни убрать */}
      <input className="input w-28 font-mono !text-xs" placeholder="/путь-ссылки"
        title="Найти ссылку по адресу — так рекордер записывает пункты меню"
        value={value.href ?? ""} onChange={(e) => onChange({ ...value, href: e.target.value })} />
      <input className="input w-32 !text-xs" placeholder="текст на кнопке"
        value={value.text ?? ""} onChange={(e) => onChange({ ...value, text: e.target.value })} />
      <input className="input w-20 font-mono !text-xs" placeholder="тег"
        value={value.in ?? ""} onChange={(e) => onChange({ ...value, in: e.target.value })} />
      <label className="flex items-center gap-1 text-xs text-(--color-ink-soft)" title="Текст входит в подпись, а не равен ей">
        <input type="checkbox" checked={Boolean(value.contains)}
          onChange={(e) => onChange({ ...value, contains: e.target.checked })} />
        часть
      </label>
      {onRemove && (
        <button className="btn btn-ghost !p-1" title="Убрать способ" onClick={onRemove}>
          <Trash2 size={12} />
        </button>
      )}
    </div>
  );
}
