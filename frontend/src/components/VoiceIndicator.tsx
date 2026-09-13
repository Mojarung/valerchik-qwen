import { Loader2, Mic } from "lucide-react";
import { useVoiceLevel } from "@/lib/store";

/**
 * Полоса состояния голосового ввода над композером.
 *
 * Нужна потому, что без неё голосовой ввод выглядит как ничего: пользователь
 * говорит, отпускает кнопку, несколько секунд не происходит ничего видимого,
 * а потом в поле сам собой появляется текст. Здесь видно и что микрофон слышит
 * звук (живые столбики), и что реплика ушла на распознавание.
 */

const BARS = 14;

export function VoiceIndicator({ state, seconds, hint }: {
  state: "recording" | "transcribing";
  seconds?: number;
  hint?: string;
}) {
  // Громкость берём из стора здесь, а не пропсом сверху: так частые замеры
  // перерисовывают четырнадцать столбиков, а не всю страницу чата.
  const level = useVoiceLevel((s) => s.level);

  if (state === "transcribing") {
    return (
      <div className="mb-2 flex items-center gap-2 rounded-2xl bg-(--color-surface-2) px-4 py-2.5">
        <Loader2 size={14} className="shrink-0 animate-spin text-(--color-brand-ink)" />
        <span className="text-xs font-medium">Распознаю речь…</span>
        <span className="ml-auto text-xs text-(--color-ink-soft)">
          текст появится в поле ввода
        </span>
      </div>
    );
  }

  return (
    <div className="mb-2 flex items-center gap-3 rounded-2xl bg-(--color-surface-2) px-4 py-2.5">
      <span className="relative flex size-2.5 shrink-0">
        <span className="absolute inline-flex size-full animate-ping rounded-full bg-(--color-brand) opacity-70" />
        <span className="relative inline-flex size-2.5 rounded-full bg-(--color-brand)" />
      </span>
      <Mic size={14} className="shrink-0 text-(--color-brand-ink)" />
      {/* Столбики от центра: тихая речь даёт мелкую рябь, громкая — заметный всплеск.
          Высота считается от синуса по индексу, чтобы полоса читалась как форма волны,
          а не как ряд одинаковых палок. */}
      <div className="flex h-5 flex-1 items-center gap-[3px]" aria-hidden>
        {Array.from({ length: BARS }, (_, i) => {
          const shape = 0.35 + 0.65 * Math.sin((i / (BARS - 1)) * Math.PI);
          const height = Math.max(3, Math.min(20, 3 + level * 60 * shape));
          return (
            <span key={i}
              className="w-[3px] rounded-full bg-(--color-brand) transition-[height] duration-75"
              style={{ height: `${height}px`, opacity: 0.45 + shape * 0.55 }} />
          );
        })}
      </div>
      {seconds !== undefined && (
        <span className="shrink-0 tabular-nums text-xs font-medium text-(--color-ink-soft)">
          {Math.floor(seconds / 60)}:{String(seconds % 60).padStart(2, "0")}
        </span>
      )}
      <span className="shrink-0 text-xs text-(--color-ink-soft)">
        {hint ?? "нажмите ■, чтобы закончить"}
      </span>
    </div>
  );
}
