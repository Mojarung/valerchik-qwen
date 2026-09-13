/**
 * Роль посетителя: поставщик или заказчик.
 *
 * Хранится в куке, а не в состоянии страницы: чат встраивается на портал
 * виджетом и расширением, и выбранная один раз роль должна пережить и
 * перезагрузку, и закрытие вкладки — как и сам диалог (см. chatSession.ts).
 * Пустая кука — роль не выбрана: тогда сервер ищет по всей базе знаний и
 * переспрашивает, если статьи для двух сторон спорят за первое место.
 */

import { create } from "zustand";
import { cookieSameSite } from "@/lib/chatSession";
import { readValue, writeValue } from "@/lib/storage";

export type Audience = "supplier" | "customer";

const COOKIE = "valerchik-audience";
const MAX_AGE_SECONDS = 60 * 60 * 24 * 365;

export function readAudience(): Audience | null {
  const value = readValue(COOKIE);
  return value === "supplier" || value === "customer" ? value : null;
}

export function saveAudience(audience: Audience | null): void {
  if (audience) writeValue(COOKIE, audience, `path=/; max-age=${MAX_AGE_SECONDS}; ${cookieSameSite()}`);
  else writeValue(COOKIE, "", `path=/; max-age=0; ${cookieSameSite()}`);
}

interface AudienceState {
  audience: Audience | null;
  /** Выбор человека — в куку и в состояние. */
  setAudience: (audience: Audience | null) => void;
}

export const useAudience = create<AudienceState>()((set) => ({
  audience: readAudience(),
  setAudience: (audience) => {
    saveAudience(audience);
    set({ audience });
  },
}));
