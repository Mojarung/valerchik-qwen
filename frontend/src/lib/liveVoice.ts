/**
 * Live-режим: непрерывный голосовой ввод без единого нажатия.
 *
 * Цикл: слушаем микрофон → детектор речи (VAD) ловит начало реплики → по паузе
 * реплика уходит в STT → распознанный текст отправляется в диалог → снова слушаем.
 * Микрофон не останавливается, пока агент отвечает: следующий вопрос можно задать
 * не дожидаясь конца ответа.
 *
 * Речь детектируем по энергии сигнала относительно шумового порога, замеренного
 * в первые полсекунды: абсолютный порог не работает — у одного пользователя
 * кондиционер, у другого тишина студии.
 *
 * Ответ агента только показывается текстом: синтез речи в системе не используется.
 */

export type LiveState = "off" | "listening" | "capturing" | "transcribing";

export interface LiveVoiceOptions {
  /** Пауза в речи, после которой реплика считается законченной. */
  silenceMs?: number;
  onState: (state: LiveState) => void;
  onError: (message: string) => void;
  /** Текущая громкость 0…1 — для индикатора: без неё режим выглядит мёртвым. */
  onLevel?: (level: number) => void;
  /** Распознанная реплика — отправить в диалог. */
  onUtterance: (text: string) => void;
  /** Расшифровка аудио на бэкенде. */
  transcribe: (audio: Blob) => Promise<string>;
}

const FRAME_MS = 50;
/** Речь = энергия выше шумового порога во столько раз. */
const SPEECH_FACTOR = 2.6;
/** Ниже этого RMS считаем тишиной при любом шумовом пороге. */
const ABSOLUTE_FLOOR = 0.006;
/** Короткие всплески (кашель, щелчок мыши) репликой не считаем. */
const MIN_SPEECH_MS = 220;
/** Дольше этого не копим аудио: длинная тишина зря раздувает буфер. */
const IDLE_RESTART_MS = 12000;

export function liveVoiceSupported(): boolean {
  return typeof window !== "undefined"
    && !!navigator.mediaDevices?.getUserMedia
    && typeof MediaRecorder !== "undefined";
}

export class LiveVoiceSession {
  private opts: Required<Pick<LiveVoiceOptions, "silenceMs">> & LiveVoiceOptions;
  private stream: MediaStream | null = null;
  private audioCtx: AudioContext | null = null;
  private analyser: AnalyserNode | null = null;
  // Float32Array<ArrayBuffer>, а не просто Float32Array: getFloatTimeDomainData не принимает
  // представление над SharedArrayBuffer, и обобщённый тип под него не подходит.
  private buffer: Float32Array<ArrayBuffer> | null = null;
  private recorder: MediaRecorder | null = null;
  private chunks: Blob[] = [];
  private timer: ReturnType<typeof setInterval> | null = null;

  private state: LiveState = "off";
  private noiseFloor = 0.01;
  private calibrationFrames = 0;
  private speechMs = 0;
  private silenceMs = 0;
  private quietMs = 0;         // тишина без речи — для перезапуска буфера
  private stopping = false;

  constructor(options: LiveVoiceOptions) {
    this.opts = { silenceMs: options.silenceMs ?? 900, ...options };
  }

  private setState(state: LiveState) {
    if (this.state === state) return;
    this.state = state;
    this.opts.onState(state);
  }

  async start(): Promise<void> {
    if (!liveVoiceSupported()) {
      this.opts.onError("Браузер не поддерживает запись с микрофона");
      return;
    }
    try {
      this.stream = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
      });
    } catch {
      this.opts.onError("Нет доступа к микрофону");
      return;
    }
    this.audioCtx = new AudioContext();
    const source = this.audioCtx.createMediaStreamSource(this.stream);
    this.analyser = this.audioCtx.createAnalyser();
    this.analyser.fftSize = 1024;
    this.buffer = new Float32Array(new ArrayBuffer(this.analyser.fftSize * 4));
    source.connect(this.analyser);

    this.noiseFloor = 0.01;
    this.calibrationFrames = 0;
    this.startRecorder();
    this.setState("listening");
    this.timer = setInterval(() => this.tick(), FRAME_MS);
  }

  stop(): void {
    this.stopping = true;
    if (this.timer) clearInterval(this.timer);
    this.timer = null;
    if (this.recorder && this.recorder.state !== "inactive") this.recorder.stop();
    this.recorder = null;
    this.stream?.getTracks().forEach((t) => t.stop());
    this.stream = null;
    void this.audioCtx?.close();
    this.audioCtx = null;
    this.setState("off");
  }

  /**
   * Пишем непрерывно, а не с момента обнаружения речи: webm-поток декодируется только
   * целиком (заголовок в первом чанке), поэтому вырезать середину нельзя, — зато
   * непрерывная запись бесплатно даёт «предзапись» и не съедает первый слог.
   */
  private startRecorder() {
    if (!this.stream) return;
    const mime = MediaRecorder.isTypeSupported("audio/webm;codecs=opus")
      ? "audio/webm;codecs=opus"
      : "audio/webm";
    this.chunks = [];
    const recorder = new MediaRecorder(this.stream, { mimeType: mime });
    recorder.ondataavailable = (e) => { if (e.data.size) this.chunks.push(e.data); };
    recorder.start(250);
    this.recorder = recorder;
  }

  /** Обрывает текущую запись и отдаёт накопленное аудио. */
  private cutRecording(): Promise<Blob | null> {
    const recorder = this.recorder;
    if (!recorder || recorder.state === "inactive") return Promise.resolve(null);
    return new Promise((resolve) => {
      recorder.onstop = () => {
        const blob = this.chunks.length ? new Blob(this.chunks, { type: recorder.mimeType }) : null;
        this.chunks = [];
        resolve(blob);
      };
      recorder.stop();
    });
  }

  private rms(): number {
    if (!this.analyser || !this.buffer) return 0;
    this.analyser.getFloatTimeDomainData(this.buffer);
    let sum = 0;
    for (const sample of this.buffer) sum += sample * sample;
    return Math.sqrt(sum / this.buffer.length);
  }

  private tick() {
    if (this.stopping) return;
    const level = this.rms();
    this.opts.onLevel?.(level);
    if (this.state === "transcribing") return;

    // Первые полсекунды слушаем комнату и запоминаем её шум как базовый уровень.
    if (this.calibrationFrames < 10) {
      this.calibrationFrames += 1;
      this.noiseFloor = this.noiseFloor * 0.7 + level * 0.3;
      return;
    }

    const speechThreshold = Math.max(this.noiseFloor * SPEECH_FACTOR, ABSOLUTE_FLOOR);
    if (level > speechThreshold) {
      this.speechMs += FRAME_MS;
      this.silenceMs = 0;
      this.quietMs = 0;
      if (this.speechMs >= MIN_SPEECH_MS && this.state === "listening") this.setState("capturing");
      return;
    }

    if (this.state === "capturing") {
      this.silenceMs += FRAME_MS;
      if (this.silenceMs >= this.opts.silenceMs) void this.finishUtterance();
      return;
    }
    // фоновый шум подстраиваем в тишине — комната меняется по ходу разговора
    this.noiseFloor = this.noiseFloor * 0.97 + level * 0.03;
    this.speechMs = 0;
    this.quietMs += FRAME_MS;
    if (this.quietMs >= IDLE_RESTART_MS) {   // сбрасываем накопленную тишину
      this.quietMs = 0;
      // Сбой нарезки не повод бросать запись: перезапускаем её в любом случае,
      // а если не поднимется и она — говорим об этом, как о любой ошибке записи.
      void this.cutRecording()
        .catch(() => null)
        .then(() => { if (!this.stopping) this.startRecorder(); })
        .catch((e: unknown) => this.opts.onError(e instanceof Error ? e.message : "Запись остановилась"));
    }
  }

  private async finishUtterance() {
    this.setState("transcribing");
    this.speechMs = 0;
    this.silenceMs = 0;
    this.quietMs = 0;
    const blob = await this.cutRecording();
    if (!this.stopping) this.startRecorder();
    if (!blob) {
      this.setState("listening");
      return;
    }
    try {
      const text = (await this.opts.transcribe(blob)).trim();
      if (text) this.opts.onUtterance(text);
    } catch (e) {
      this.opts.onError(e instanceof Error ? e.message : "Ошибка распознавания");
    }
    if (!this.stopping) this.setState("listening");
  }

  get current(): LiveState {
    return this.state;
  }
}
