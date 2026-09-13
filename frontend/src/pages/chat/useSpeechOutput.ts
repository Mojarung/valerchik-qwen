import { useCallback, useEffect, useState, type RefObject } from "react";
import type { LiveState } from "@/lib/liveVoice";
import { setSpeechEnabled, speechEnabled, stopSpeaking } from "@/lib/speech";

export interface SpeechOutputInput {
  /** Синтез речи доступен на сервере. */
  ttsAvailable: boolean;
  liveState: LiveState;
  /** Микрофон открыт — человек говорит. */
  listening: boolean;
}

export interface SpeechOutput {
  speechOn: boolean;
  toggleSpeech: () => void;
}

/**
 * Озвучка ответов. Решение «озвучивать ли сейчас» кладётся в ref: обработчик
 * websocket захватывается один раз при подключении, и замороженные в нём
 * значения молча выключили бы озвучку после первого переключения режима.
 */
export function useSpeechOutput(
  speakRef: RefObject<boolean>, { ttsAvailable, liveState, listening }: SpeechOutputInput,
): SpeechOutput {
  const [speechOn, setSpeechOn] = useState(speechEnabled);

  // Только в голосовом режиме: человек, который печатает, читает быстрее, чем
  // слушает, и внезапный голос из вкладки его скорее напугает.
  useEffect(() => {
    speakRef.current = ttsAvailable && speechOn && liveState !== "off";
  }, [speakRef, ttsAvailable, speechOn, liveState]);

  // Человек заговорил — замолкаем. Перебивать невежливо, а микрофон вдобавок
  // слышал бы наш собственный голос и отправил бы его как реплику.
  useEffect(() => {
    if (listening) stopSpeaking();
  }, [listening]);

  // Уход со страницы посреди фразы не должен оставлять звук играть в фоне
  useEffect(() => stopSpeaking, []);

  const toggleSpeech = useCallback(() => {
    const next = !speechOn;
    setSpeechOn(next);
    setSpeechEnabled(next);      // выбор переживает перезагрузку
  }, [speechOn]);

  return { speechOn, toggleSpeech };
}
