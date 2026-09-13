import { useRef } from "react";
import { ArrowUp, AudioLines, ImagePlus, Mic, Square, Volume2, VolumeX } from "lucide-react";
import { VoiceIndicator } from "@/components/VoiceIndicator";
import { useMeta } from "@/lib/store";
import type { SpeechOutput } from "./useSpeechOutput";
import type { VoiceInput } from "./useVoiceInput";

/** Поле вопроса: голос, озвучка ответов, скриншот оператору и кнопка отправки. */
export function Composer({
  input, onInput, onSend, closed, flash, voice, speech, withOperator, sendingImage, onImage,
}: {
  input: string;
  onInput: (text: string) => void;
  onSend: (text: string) => void;
  closed: boolean;
  /** Подсветка поля: сюда только что приехал распознанный текст. */
  flash: boolean;
  voice: VoiceInput;
  speech: SpeechOutput;
  /** Диалог ведёт оператор — можно прислать скриншот. */
  withOperator: boolean;
  sendingImage: boolean;
  onImage: (file: File) => void;
}) {
  const imageRef = useRef<HTMLInputElement>(null);
  const voiceAvailable = useMeta((s) => s.voiceAvailable);
  const ttsAvailable = useMeta((s) => s.ttsAvailable);
  const liveVoice = useMeta((s) => s.liveVoice);
  const { recording, transcribing, recordSeconds, liveState } = voice;
  const canSend = Boolean(input.trim()) && !closed;

  return (
    <div className="pb-3">
      {(recording || transcribing
        || liveState === "capturing" || liveState === "transcribing") && (
        <VoiceIndicator
          state={recording || liveState === "capturing" ? "recording" : "transcribing"}
          seconds={recording ? recordSeconds : undefined}
          hint={liveState !== "off" ? "отправлю, когда договорите" : undefined}
        />
      )}
      <div className={`card flex items-end gap-1.5 rounded-[1.75rem] p-2 transition-shadow ${
        flash ? "ring-2 ring-(--color-brand)" : ""}`}>
        {/* Скрепка появляется только когда диалог ведёт оператор: показать
            ошибку картинкой быстрее, чем описывать словами, но открытая
            загрузка файлов в любой момент — это не чат, а файлообменник.
            Слева от поля, чтобы не соседствовать с кнопками голоса и отправки. */}
        {withOperator && !closed && (
          <>
            <input ref={imageRef} type="file" className="hidden" accept="image/*"
              onChange={(e) => {
                const picked = e.target.files?.[0];
                e.target.value = "";
                if (picked) onImage(picked);
              }} />
            <button className="btn btn-ghost !min-h-0 !p-3" disabled={sendingImage}
              onClick={() => imageRef.current?.click()}
              title="Прикрепить скриншот для оператора" aria-label="Прикрепить скриншот">
              <ImagePlus size={16} />
            </button>
          </>
        )}
        <textarea
          className="max-h-32 min-h-11 flex-1 resize-none border-none bg-transparent px-3 py-2.5 text-sm focus:outline-none"
          placeholder={closed ? "Диалог завершён" : "Опишите ваш вопрос…"}
          value={input}
          disabled={closed}
          rows={1}
          aria-label="Сообщение"
          onChange={(e) => onInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              onSend(input);
            }
          }}
        />
        {voiceAvailable && (
          <button
            className={`btn !min-h-0 !p-3 ${recording ? "btn-primary animate-pulse" : "btn-ghost"}`}
            onClick={() => void voice.toggleRecording()}
            disabled={closed || liveState !== "off"}
            title={recording ? "Остановить запись" : "Надиктовать голосом"}
          >
            {recording ? <Square size={16} /> : <Mic size={16} />}
          </button>
        )}
        {liveVoice.enabled && (
          <button
            className={`btn !min-h-0 !p-3 ${liveState !== "off" ? "btn-primary" : "btn-ghost"}
                        ${liveState === "capturing" ? "animate-pulse" : ""}`}
            onClick={() => void voice.toggleLive()}
            disabled={closed || recording}
            title={liveState === "off" ? "Live-режим: говорите, не нажимая кнопок"
                                       : "Выключить live-режим"}
            aria-pressed={liveState !== "off"}
          >
            <AudioLines size={16} />
          </button>
        )}
        {/* Выключатель озвучки виден только в голосовом режиме: тому, кто
            печатает, ответы и так не читают вслух, и кнопка была бы загадкой. */}
        {ttsAvailable && liveState !== "off" && (
          <button
            className={`btn !min-h-0 !p-3 ${speech.speechOn ? "btn-ghost" : "btn-ghost opacity-50"}`}
            onClick={speech.toggleSpeech}
            title={speech.speechOn ? "Отключить озвучку ответов" : "Озвучивать ответы"}
            aria-pressed={speech.speechOn}
          >
            {speech.speechOn ? <Volume2 size={16} /> : <VolumeX size={16} />}
          </button>
        )}
        {/* кнопка выезжает слева направо, а блок под неё плавно расширяется
            (grid 0fr -> 1fr анимирует ширину без дёрганья соседей) */}
        <div className={`send-slot ${canSend ? "send-slot--open" : ""}`}>
          <div className="overflow-hidden">
            <button
              className="btn btn-primary !min-h-0 !rounded-full !p-3"
              onClick={() => onSend(input)} title="Отправить (Enter)" aria-label="Отправить"
              tabIndex={canSend ? 0 : -1}>
              <ArrowUp size={17} strokeWidth={2.5} />
            </button>
          </div>
        </div>
      </div>
      {liveState !== "off" ? (
        <div className="mt-1.5 flex items-center justify-center gap-2 text-center text-xs
                        font-semibold text-(--color-brand-ink)">
          <span className="inline-block size-1.5 animate-ping rounded-full bg-(--color-brand)" />
          {{
            listening: "Live-режим: слушаю — просто говорите",
            capturing: "Слушаю вас…",
            transcribing: "Распознаю…",
            off: "",
          }[liveState]}
        </div>
      ) : (
        <div className="mt-1.5 hidden text-center text-xs text-(--color-ink-soft) sm:block">
          Enter — отправить · Shift+Enter — новая строка
          {voiceAvailable && " · микрофон — голосовой ввод"}
          {liveVoice.enabled && " · волна — live-разговор"}
        </div>
      )}
    </div>
  );
}
