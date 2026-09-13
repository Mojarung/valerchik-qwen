import { useCallback, useEffect, useRef, useState } from "react";
import { api, errorText } from "@/lib/api";
import { LiveVoiceSession, liveVoiceSupported, type LiveState } from "@/lib/liveVoice";
import { useMeta, useVoiceLevel } from "@/lib/store";

export interface VoiceInputOptions {
  /** Диалог завершён — микрофон отпускаем сразу. */
  closed: boolean;
  /** Распознанная диктовка: текст дописывается в поле ввода. */
  onDictation: (text: string) => void;
  /** Реплика live-режима уходит сразу, без поля ввода. */
  onUtterance: (text: string) => void;
  onError: (message: string) => void;
}

export interface VoiceInput {
  recording: boolean;
  transcribing: boolean;
  /** Сколько секунд идёт диктовка — для индикатора записи. */
  recordSeconds: number;
  liveState: LiveState;
  /** Микрофон открыт — и в live-режиме, и на обычной записи: для человека это
   *  одно и то же «крыска меня слушает», разное поведение только сбивало бы. */
  listening: boolean;
  toggleRecording: () => Promise<void>;
  toggleLive: () => Promise<void>;
}

/**
 * Голосовой ввод: диктовка в поле и live-режим. Сессия записи переживает
 * перерисовки, а звать должна свежие обработчики — поэтому они в ref.
 */
export function useVoiceInput({ closed, onDictation, onUtterance, onError }: VoiceInputOptions): VoiceInput {
  const [recording, setRecording] = useState(false);
  const [transcribing, setTranscribing] = useState(false);
  const [recordSeconds, setRecordSeconds] = useState(0);
  const [liveState, setLiveState] = useState<LiveState>("off");
  const recorderRef = useRef<MediaRecorder | null>(null);
  /** Освободить микрофон обычной записи (см. toggleRecording). */
  const releaseRecordingRef = useRef<(() => void) | null>(null);
  const liveRef = useRef<LiveVoiceSession | null>(null);
  const liveVoice = useMeta((s) => s.liveVoice);
  // Замеры громкости идут в отдельный стор: держи их в состоянии страницы —
  // и лента сообщений перерисовывалась бы по двадцать раз в секунду.
  const setMicLevel = useVoiceLevel((s) => s.setLevel);

  const handlers = useRef({ onDictation, onUtterance, onError });
  useEffect(() => {
    handlers.current = { onDictation, onUtterance, onError };
  });

  const toggleRecording = useCallback(async () => {
    if (recording) {
      recorderRef.current?.stop();
      return;
    }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const recorder = new MediaRecorder(stream, { mimeType: "audio/webm" });
      const chunks: Blob[] = [];

      // Уровень звука для индикатора: без него запись и распознавание выглядят
      // как «ничего не происходит», а потом текст сам собой появляется в поле.
      const audioCtx = new AudioContext();
      const analyser = audioCtx.createAnalyser();
      analyser.fftSize = 1024;
      audioCtx.createMediaStreamSource(stream).connect(analyser);
      const samples = new Float32Array(new ArrayBuffer(analyser.fftSize * 4));
      const started = Date.now();
      const meter = setInterval(() => {
        analyser.getFloatTimeDomainData(samples);
        let sum = 0;
        for (const sample of samples) sum += sample * sample;
        setMicLevel(Math.sqrt(sum / samples.length));   // в стор, не в состояние страницы
        setRecordSeconds(Math.floor((Date.now() - started) / 1000));
      }, 80);

      // Освобождение железа отдельной функцией: раньше оно жило только внутри
      // onstop, и уход со страницы посреди диктовки оставлял микрофон
      // захваченным до перезагрузки вкладки — с горящим индикатором записи и
      // недозакрытым AudioContext (их число на документ ограничено, и после
      // нескольких таких заходов запись переставала включаться вовсе).
      const release = () => {
        clearInterval(meter);
        audioCtx.close().catch(() => undefined);   // уже закрыт — закрывать нечего
        stream.getTracks().forEach((t) => t.stop());
        setMicLevel(0);
      };
      releaseRecordingRef.current = release;

      recorder.ondataavailable = (e) => chunks.push(e.data);
      recorder.onstop = async () => {
        release();
        releaseRecordingRef.current = null;
        setRecording(false);
        setTranscribing(true);
        const form = new FormData();
        form.append("file", new Blob(chunks, { type: "audio/webm" }), "voice.webm");
        try {
          const { text } = await api.upload<{ text: string }>("/api/voice/transcribe", form);
          if (text.trim()) handlers.current.onDictation(text);
          else handlers.current.onError("Ничего не распознал — попробуйте сказать ещё раз");
        } catch (e) {
          handlers.current.onError(errorText(e, "Ошибка распознавания"));
        } finally {
          setTranscribing(false);
        }
      };
      recorder.start();
      recorderRef.current = recorder;
      setRecordSeconds(0);
      setRecording(true);
    } catch {
      handlers.current.onError("Нет доступа к микрофону");
    }
  }, [recording, setMicLevel]);

  /** Live-режим: микрофон слушает непрерывно, реплика уходит по паузе. */
  const toggleLive = useCallback(async () => {
    if (liveRef.current) {
      liveRef.current.stop();
      liveRef.current = null;
      setLiveState("off");
      return;
    }
    if (!liveVoiceSupported()) {
      handlers.current.onError("Браузер не поддерживает запись с микрофона");
      return;
    }
    const live = new LiveVoiceSession({
      silenceMs: liveVoice.silence_ms,
      onState: setLiveState,
      onLevel: setMicLevel,
      onError: (message) => handlers.current.onError(message),
      onUtterance: (text) => handlers.current.onUtterance(text),
      transcribe: async (audio) => {
        const form = new FormData();
        form.append("file", audio, "live.webm");
        const { text } = await api.upload<{ text: string }>("/api/voice/transcribe", form);
        return text;
      },
    });
    liveRef.current = live;
    await live.start();
  }, [liveVoice, setMicLevel]);

  // Диалог завершён — микрофон отпускаем сразу. Обе ветки записи, а не только
  // live: кнопка «стоп» при закрытом диалоге становится неактивной, и обычная
  // диктовка иначе оставалась бы висеть.
  useEffect(() => {
    if (!closed) return;
    if (liveRef.current) {
      liveRef.current.stop();
      liveRef.current = null;
      setLiveState("off");
    }
    if (recorderRef.current?.state === "recording") recorderRef.current.stop();
  }, [closed]);

  useEffect(() => () => {
    liveRef.current?.stop();
    liveRef.current = null;
    // Размонтирование посреди диктовки: onstop уже никто не дождётся —
    // освобождаем поток и AudioContext напрямую.
    releaseRecordingRef.current?.();
    releaseRecordingRef.current = null;
    recorderRef.current = null;
  }, []);

  const listening = recording || liveState === "listening" || liveState === "capturing";
  return { recording, transcribing, recordSeconds, liveState, listening, toggleRecording, toggleLive };
}
