/** Переключатель с подписью и пояснением: роль switch, имя — из заголовка. */
export function Toggle({ checked, onChange, title, hint }: {
  checked: boolean;
  onChange: (value: boolean) => void;
  title: string;
  hint: string;
}) {
  return (
    <div className="flex cursor-pointer items-start gap-2.5">
      <button
        type="button"
        role="switch"
        aria-label={title}
        aria-checked={checked}
        className={`relative mt-0.5 h-5 w-9 shrink-0 rounded-full transition-colors ${
          checked ? "bg-(--color-brand)" : "bg-(--color-line)"}`}
        onClick={() => onChange(!checked)}>
        <span className={`absolute top-0.5 size-4 rounded-full bg-white transition-all ${
          checked ? "left-4.5" : "left-0.5"}`} />
      </button>
      <span className="min-w-0">
        <span className="block text-xs font-medium">{title}</span>
        <span className="block text-xs leading-snug text-(--color-ink-soft)">{hint}</span>
      </span>
    </div>
  );
}
