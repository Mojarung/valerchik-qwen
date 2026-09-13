import { useEffect, useState } from "react";
import { Star } from "lucide-react";

/** Ответы на два быстрых вопроса после звёзд; null — человек вопрос пропустил. */
export interface RatingAnswers {
  /** «Проблема решена?» — оценка работы специалиста. */
  solved: boolean | null;
  /** «Понятно объяснили?» — оценка того, как донесли информацию. */
  clear: boolean | null;
}

export const NO_ANSWERS: RatingAnswers = { solved: null, clear: null };

/** След отправленной оценки: сколько звёзд ушло и что они дошли.
 *  Через несколько секунд уходит сам — подтверждение своё дело сделало, а
 *  висеть в ленте ему незачем. Уходит плавно: мгновенное исчезновение читалось
 *  бы как сбой. */
export function RatedNote({ stars }: { stars: number }) {
  const [leaving, setLeaving] = useState(false);
  const [gone, setGone] = useState(false);

  useEffect(() => {
    const fade = setTimeout(() => setLeaving(true), 4000);
    const drop = setTimeout(() => setGone(true), 4800);
    return () => { clearTimeout(fade); clearTimeout(drop); };
  }, []);

  if (gone) return null;
  return (
    <div className={`card mx-auto flex max-w-md flex-wrap items-center justify-center gap-x-2 gap-y-1 px-4 py-3 transition-all duration-700 ease-(--ease-slime) ${
      leaving ? "-translate-y-1 scale-95 opacity-0" : "animate-(--animate-pop)"}`}>
      <div className="flex gap-0.5">
        {[1, 2, 3, 4, 5].map((n) => (
          <Star key={n} size={15}
            className={n <= stars ? "fill-(--color-warn) text-(--color-warn-ink)" : "text-(--color-line)"} />
        ))}
      </div>
      <span className="text-xs font-semibold">Спасибо! Оценка отправлена</span>
    </div>
  );
}

/** Вопрос «да / нет» крупными кнопками: на телефоне в них попадают пальцем.
 *  Повторное нажатие снимает ответ — вопрос необязательный, и передумать
 *  «не хочу отвечать» тоже можно. */
function YesNo({ label, value, onChange }: {
  label: string;
  value: boolean | null;
  onChange: (value: boolean | null) => void;
}) {
  const option = (answer: boolean, text: string) => {
    const on = value === answer;
    const tone = answer
      ? "border-(--color-ok-ink) bg-(--color-ok)/15 text-(--color-ok-ink)"
      : "border-(--color-brand-ink) bg-(--color-brand-soft) text-(--color-brand-ink)";
    return (
      <button type="button" aria-pressed={on} onClick={() => onChange(on ? null : answer)}
        className={`min-h-11 flex-1 cursor-pointer rounded-xl border px-3 text-sm font-semibold transition-colors duration-200 ${
          on ? tone : "border-(--color-line) hover:border-(--color-ink-soft)"}`}>
        {text}
      </button>
    );
  };
  return (
    <div role="group" aria-label={label} className="mb-2 text-left">
      <div className="mb-1 text-xs font-semibold">{label}</div>
      <div className="flex gap-2">
        {option(true, "Да")}
        {option(false, "Нет")}
      </div>
    </div>
  );
}

export function RatingCard({ onRate, note = "", onDismiss }: {
  /** Оценка 1–5, комментарий и ответы на два вопроса. Ответы — третьим
   *  аргументом: обработчик, который знает только звёзды и комментарий,
   *  по-прежнему подходит. */
  onRate: (rating: number, comment: string, answers: RatingAnswers) => Promise<void>;
  /** Текст просьбы из настроек — показываем, когда чат сам завёл разговор об оценке. */
  note?: string;
  /** Задан, если карточку можно закрыть: после закрытия диалога уходить ей некуда. */
  onDismiss?: () => void;
}) {
  const [stars, setStars] = useState(0);
  const [hover, setHover] = useState(0);
  const [comment, setComment] = useState("");
  const [answers, setAnswers] = useState<RatingAnswers>(NO_ANSWERS);
  return (
    <div className="card animate-(--animate-pop) mx-auto w-full max-w-md p-5 text-center">
      <div className="mb-2 text-sm font-bold">Оцените консультацию</div>
      {note && <p className="mb-3 text-xs text-(--color-ink-soft)">{note}</p>}
      <div className="mb-3 flex justify-center gap-1" role="group" aria-label="Оценка консультации">
        {[1, 2, 3, 4, 5].map((n) => (
          <button key={n} type="button" aria-label={`${n} из 5`} aria-pressed={n === stars}
            onMouseEnter={() => setHover(n)} onMouseLeave={() => setHover(0)}
            onClick={() => setStars(n)}
            className="cursor-pointer p-1 transition-transform duration-200 ease-(--ease-slime) hover:scale-125">
            <Star
              size={28}
              className={n <= (hover || stars) ? "fill-(--color-warn) text-(--color-warn-ink)" : "text-(--color-line)"}
            />
          </button>
        ))}
      </div>
      {/* Вопросы — после звёзд: сначала общее впечатление, потом два уточнения.
          Показанные сразу, пять кнопок вместо одной отпугивали бы от оценки вовсе. */}
      {stars > 0 && (
        <>
          <YesNo label="Проблема решена?" value={answers.solved}
            onChange={(solved) => setAnswers((a) => ({ ...a, solved }))} />
          <YesNo label="Понятно объяснили?" value={answers.clear}
            onChange={(clear) => setAnswers((a) => ({ ...a, clear }))} />
          <textarea
            className="input mt-1 mb-2 resize-none text-left"
            rows={2}
            aria-label="Комментарий"
            placeholder="Комментарий (необязательно)"
            maxLength={2000}
            value={comment}
            onChange={(e) => setComment(e.target.value)}
          />
          <button type="button" className="btn btn-primary min-h-11 w-full"
            onClick={() => void onRate(stars, comment, answers)}>
            Отправить оценку
          </button>
        </>
      )}
      {onDismiss && (
        <button type="button"
          className="mt-2 text-xs text-(--color-ink-soft) underline underline-offset-2 hover:text-(--color-brand-ink)"
          onClick={onDismiss}>
          Не сейчас
        </button>
      )}
    </div>
  );
}
