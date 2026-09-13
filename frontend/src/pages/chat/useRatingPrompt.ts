import { useCallback, useEffect, useState } from "react";

export interface RatingPromptInput {
  /** Номер начала диалога: новый диалог — новая оценка. */
  generation: number;
  /** Пауза до предложения, с; 0 — не предлагать вовсе (настраивается в админке). */
  idleSeconds: number;
  /** Идёт ответ или человек печатает: карточка, выпрыгнувшая посреди набора,
   *  читалась бы как «разговор окончен». */
  busy: boolean;
  /** Предлагать нельзя: диалог закрыт, ждёт оператора или ответа ещё не было.
   *  «Оцените консультацию» посреди ожидания оператора читается как отказ. */
  blocked: boolean;
  /** Последнее сообщение: каждое новое начинает отсчёт заново. */
  lastMessageId: string | undefined;
}

export interface RatingPrompt {
  askRating: boolean;
  rated: boolean;
  ratedStars: number;
  /** Разговор продолжился — карточка убирается сама. */
  hide: () => void;
  /** «Не сейчас»: в этом диалоге больше не предлагаем. */
  decline: () => void;
  /** Оценка ушла: предложение гаснет, остаётся след с числом звёзд. */
  markRated: (stars: number) => void;
}

interface PromptState {
  generation: number;
  askRating: boolean;
  declined: boolean;
  /** 0 — оценки ещё не было. */
  ratedStars: number;
}

const initial = (generation: number): PromptState =>
  ({ generation, askRating: false, declined: false, ratedStars: 0 });

/**
 * Предложение оценить консультацию. Предлагаем сами после паузы в диалоге:
 * иначе звёзды видит только тот, чей диалог закрыл оператор, а чисто машинный
 * диалог не заканчивается никогда.
 */
export function useRatingPrompt(
  { generation, idleSeconds, busy, blocked, lastMessageId }: RatingPromptInput,
): RatingPrompt {
  const [stored, setStored] = useState(() => initial(generation));
  // Новый диалог — всё с чистого листа. Сброс прямо в рендере, а не эффектом:
  // иначе первый кадр новой ленты показал бы карточку оценки прежнего диалога.
  if (stored.generation !== generation) setStored(initial(generation));
  const state = stored.generation === generation ? stored : initial(generation);
  const { askRating, declined, ratedStars } = state;
  const rated = ratedStars > 0;

  useEffect(() => {
    const idleMs = idleSeconds * 1000;
    if (!idleMs || busy || blocked || rated || declined || askRating) return;
    const timer = setTimeout(() => setStored((s) => ({ ...s, askRating: true })), idleMs);
    return () => clearTimeout(timer);
  }, [idleSeconds, busy, blocked, rated, declined, askRating, lastMessageId]);

  const hide = useCallback(() => setStored((s) => ({ ...s, askRating: false })), []);
  const decline = useCallback(() => setStored((s) => ({ ...s, askRating: false, declined: true })), []);
  const markRated = useCallback(
    (stars: number) => setStored((s) => ({ ...s, askRating: false, ratedStars: stars })), [],
  );

  return { askRating, rated, ratedStars, hide, decline, markRated };
}
