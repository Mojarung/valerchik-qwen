import { apiUrl } from "@/lib/apiBase";

/**
 * Озвучка ответов в голосовом режиме.
 *
 * Живёт вне React: воспроизведение — это не состояние экрана, а внешний ресурс,
 * который нужно уметь прервать из любого места (человек заговорил снова, ушёл со
 * страницы, нажал «стоп»). Держать его в состоянии компонента значило бы гонять
 * перерисовку на каждый старт и стоп звука.
 */

const COOKIE = "valerchik-voice";
const YEAR_SECONDS = 31536000;

let current: HTMLAudioElement | null = null;
let currentUrl = "";
// Промис озвучки завершается по концу воспроизведения, но прерванный pause()
// события `ended` не даёт. Без явного завершителя такой промис висел бы вечно, а
// вместе с ним — маскот в состоянии «говорит».
let finish: (() => void) | null = null;

/** Включена ли озвучка у этого посетителя. По умолчанию — да: он сам включил
 *  голосовой режим, и молчащий в ответ собеседник выглядел бы поломкой. */
export function speechEnabled(): boolean {
  return readCookie() !== "off";
}

export function setSpeechEnabled(enabled: boolean): void {
  document.cookie =
    `${COOKIE}=${enabled ? "on" : "off"}; path=/; max-age=${YEAR_SECONDS}; samesite=lax`;
  if (!enabled) stopSpeaking();
}

function readCookie(): string {
  const found = document.cookie.split("; ").find((row) => row.startsWith(`${COOKIE}=`));
  return found ? found.slice(COOKIE.length + 1) : "";
}

export function stopSpeaking(): void {
  current?.pause();
  current = null;
  // Освобождаем blob: иначе каждая реплика оставляет за собой объект в памяти
  // вкладки, а разговор из полусотни ответов — это уже десятки мегабайт.
  if (currentUrl) URL.revokeObjectURL(currentUrl);
  currentUrl = "";
  finish?.();
  finish = null;
}

/**
 * Озвучить текст. Возвращает промис, который завершается по концу
 * воспроизведения — по нему маскот перестаёт «говорить».
 *
 * Любая ошибка (сервис не поднят, автовоспроизведение запрещено браузером,
 * реплику прервали) гасится: озвучка — украшение, и ронять из-за неё разговор
 * нельзя. Текст ответа человек уже видит на экране.
 */
export async function speak(text: string, signal?: AbortSignal): Promise<void> {
  stopSpeaking();
  try {
    const resp = await fetch(apiUrl("/api/voice/speak"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text }),
      signal,
    });
    if (!resp.ok) return;
    const blob = await resp.blob();
    currentUrl = URL.createObjectURL(blob);
    const audio = new Audio(currentUrl);
    current = audio;
    await audio.play();
    await new Promise<void>((resolve) => {
      finish = resolve;
      audio.onended = () => stopSpeaking();
      audio.onerror = () => stopSpeaking();
    });
  } catch {
    /* озвучка необязательна: текст ответа уже на экране */
    stopSpeaking();
  }
}
