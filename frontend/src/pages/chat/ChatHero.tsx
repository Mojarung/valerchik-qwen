import { AudienceToggle } from "@/components/AudienceToggle";
import { Mascot } from "@/components/Mascot";
import type { Audience } from "@/lib/audience";

/** Стартовый экран: приветствие, выбор роли и подсказки-вопросы. */
export function ChatHero({ greeting, chips, audience, listening, onAudience, onPick }: {
  greeting: string;
  chips: string[];
  audience: Audience | null;
  listening: boolean;
  onAudience: (audience: Audience | null) => void;
  onPick: (text: string) => void;
}) {
  return (
    <div className="min-h-0 flex-1 overflow-y-auto">
      {/* m-auto: центр по вертикали, но без обрезки верха при переполнении */}
      <div className="mx-auto flex min-h-full max-w-xl flex-col items-center justify-center gap-3 py-4 text-center sm:gap-5">
        <div className="hero-mascot shrink-0">
          {/* Live-режим включают и с приветственного экрана — крыска должна
              показать, что уже слушает, не дожидаясь первого сообщения. */}
          <Mascot mood={listening ? "listening" : "happy"}
                  size={window.innerWidth < 640 ? 84 : 112} />
        </div>
        <div className="hero-text w-full max-w-md px-2">
          <h1 className="mb-1.5 text-xl font-extrabold tracking-tight sm:mb-2 sm:text-3xl">
            Чем помочь?
          </h1>
          <p className="text-sm leading-relaxed text-(--color-ink-soft)">
            {greeting}
          </p>
        </div>
        {/* Роль — до первого вопроса: инструкции для поставщика и заказчика
            разные, и подсказки ниже тоже подстраиваются под выбор */}
        <div className="hero-text w-full max-w-[17rem] px-2">
          <AudienceToggle value={audience} onChange={onAudience} />
          {/* Пока роль не выбрана, обе половины ползунка выглядят одинаково —
              со стороны это читается как «переключатель не работает».
              Подпись говорит, что выбор за человеком и зачем он нужен. */}
          <p className="mt-1.5 text-xs leading-snug text-(--color-ink-soft)">
            {audience
              ? "Роль можно сменить в любой момент — ответы подстроятся."
              : "Выберите, кто вы, — ответы станут точнее."}
          </p>
        </div>
        <div className="flex w-full max-w-full flex-wrap justify-center gap-2 px-1 sm:max-w-xl">
          {chips.map((c, i) => (
            // на телефоне и во встроенном виджете показываем только 3 подсказки — остальные на десктопе
            <button key={c}
              className={`chip hero-chip !px-4 !py-2.5 !text-sm sm:!px-5 sm:!py-3 sm:!text-sm ${i >= 3 ? "!hidden sm:!inline-flex" : ""}`}
              style={{ maxWidth: "100%", whiteSpace: "normal", overflowWrap: "anywhere" }}
              onClick={() => onPick(c)}>
              {c}
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}
