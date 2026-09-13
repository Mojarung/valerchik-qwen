import { create } from "zustand";
import { persist } from "zustand/middleware";
import { api, ApiError, setSessionHandlers } from "@/lib/api";
import type { Api } from "@/lib/types";
import {
  applyTheme, applyThemeAnimated, onSystemThemeChange, readThemeChoice, resolveTheme,
  saveThemeChoice, type Theme, type ThemeChoice,
} from "@/lib/theme";

/**
 * Кто вошёл — но не чем он это докажет.
 *
 * Права сотрудника подтверждает httpOnly-кука `valerchik_auth`: её ставит и
 * снимает сервер, JS её не видит и записать не может. Здесь остаются только
 * роль и имя — то, что нужно нарисовать меню и решить, какие разделы
 * показывать. Само по себе это состояние ничего не открывает: подделав его в
 * localStorage, посторонний увидит пункт «Админ», по которому сервер ответит
 * 401.
 */
interface AuthState {
  role: string | null;
  displayName: string | null;
  /**
   * Сервер уже сказал, есть ли действующая сессия.
   *
   * До этого момента судить о правах нельзя: роль в localStorage — прошлая
   * догадка, а куки JS не видит. Раньше страж отправлял на вход мгновенно, и
   * вошедший сотрудник вылетал из админки при каждом обновлении вкладки.
   */
  ready: boolean;
  setUser: (role: string | null, displayName: string | null) => void;
  restore: () => Promise<void>;
  logout: () => Promise<void>;
}

/** Ответ `/api/auth/me`: у гостя роль пустая, это нормальный ответ, а не ошибка. */
type MeResponse = Api["MeResponse"];

/**
 * Номер состояния сессии: растёт на каждом свежем ответе сервера о том, кто
 * вошёл (успешный вход, выход).
 *
 * Нужен, потому что ответы приходят не в том порядке, в каком уходили запросы.
 * Человек открывает сразу `/login`, стартовый `restore()` уходит спрашивать
 * `/api/auth/me`, человек успевает войти — и только потом приезжает 401 на тот
 * первый вопрос. Без номера этот опоздавший ответ затирал бы состоявшийся вход,
 * и страж выкидывал бы сотрудника на экран входа при живой куке. Сравнение
 * номеров отвечает на единственный нужный вопрос: этот ответ — про то состояние
 * сессии, которое действует сейчас, или про уже отменённое.
 */
let sessionEpoch = 0;

export const useAuth = create<AuthState>()(
  persist(
    (set) => ({
      role: null,
      displayName: null,
      ready: false,
      // Свежий ответ сервера о том, кто вошёл: отменяет всё, что было раньше,
      // в том числе ещё не вернувшийся стартовый `restore()`.
      setUser: (role, displayName) => {
        sessionEpoch += 1;
        set({ role, displayName });
      },
      restore: async () => {
        const epochAtStart = sessionEpoch;
        try {
          const me = await api.get<MeResponse>("/api/auth/me");
          if (sessionEpoch === epochAtStart) {
            set({ role: me.role ?? null, displayName: me.display_name ?? null });
          }
        } catch (err) {
          // Сюда попадает только настоящая поломка: сервер отвечает гостю 200 с
          // пустой ролью. Молча выдать сбой за «гостя» значит потерять
          // единственный сигнал — человек окажется на экране входа без объяснений.
          const guest = err instanceof ApiError && err.status === 401;
          if (!guest) console.warn("Не удалось проверить сессию", err);
          // Прав всё равно не выдаём: пустить в раздел по прошлой роли — значит
          // показать экран, где каждый запрос отвечает ошибкой. Но если за время
          // ожидания пользователь успел войти, его вход трогать нельзя.
          if (sessionEpoch === epochAtStart) set({ role: null, displayName: null });
        } finally {
          // Даже если сервер не ответил, приложение обязано нарисоваться:
          // иначе вместо чата человек смотрит на бесконечный спиннер.
          set({ ready: true });
        }
      },
      logout: async () => {
        // Номер поднимается до запроса: с момента нажатия «Выйти» любой ещё
        // летящий ответ про прошлую сессию уже неактуален.
        sessionEpoch += 1;
        try {
          await api.post<void>("/api/auth/logout");
        } catch {
          /* сервер не ответил — куку он не снял, но локальное состояние чистим:
             показывать сотрудника вошедшим после нажатия «Выйти» нельзя */
        }
        set({ role: null, displayName: null, ready: true });
      },
    }),
    {
      name: "valerchik-auth",
      // Версия 0 хранила в этом же ключе JWT. Он больше не нужен и не должен
      // пережить обновление вкладки: миграция выбрасывает всё, кроме роли и
      // имени, поэтому у вернувшегося пользователя старый токен исчезает из
      // localStorage сам, без ручной чистки.
      version: 1,
      migrate: (persisted) => {
        const previous = (persisted ?? {}) as Partial<Record<"role" | "displayName", unknown>>;
        return {
          role: typeof previous.role === "string" ? previous.role : null,
          displayName: typeof previous.displayName === "string" ? previous.displayName : null,
        };
      },
      // Что попадает в localStorage. `ready` — состояние текущей вкладки, а не
      // факт о пользователе: сохранись он, страж поверил бы прошлой сессии
      // раньше, чем сервер её подтвердил.
      partialize: (state) => ({ role: state.role, displayName: state.displayName }),
    },
  ),
);

// 401 от любого запроса означает, что кука кончилась. Хранилище подписывается
// на это само — api.ts про хранилище не знает, чтобы не замкнуть импорты.
setSessionHandlers(
  () => sessionEpoch,
  (epochAtStart) => {
    // Ответ про сессию, которой уже нет: за время запроса человек вошёл или
    // вышел. Он рассказывает про прошлое и права трогать не должен.
    if (epochAtStart !== sessionEpoch) return;
    // `ready` не трогаем: им распоряжается только `restore()`. Иначе 401 на
    // постороннем запросе снимал бы блокировку со стража раньше, чем сервер
    // вообще ответил, кто вошёл, — и сотрудника уводило бы на экран входа.
    useAuth.setState({ role: null, displayName: null });
  },
);

interface ThemeState {
  choice: ThemeChoice;
  theme: Theme;
  setChoice: (choice: ThemeChoice) => void;
  /** Следить за системной темой, пока выбрана «системная». Возвращает отписку. */
  watchSystem: () => () => void;
}

export const useTheme = create<ThemeState>()((set, get) => ({
  // Атрибут на <html> уже проставлен инлайновым скриптом — здесь только читаем
  // тот же источник истины, чтобы состояние React совпало с разметкой.
  choice: readThemeChoice(),
  theme: resolveTheme(readThemeChoice()),
  // Нажатие человека — с анимацией; смена системной темы в фоне (ниже) —
  // молча: волна по экрану без единого действия пользователя пугает.
  setChoice: (choice) => {
    saveThemeChoice(choice);
    set({ choice, theme: applyThemeAnimated(choice) });
  },
  watchSystem: () => onSystemThemeChange(() => {
    if (get().choice === "system") set({ theme: applyTheme("system") });
  }),
}));

/**
 * Громкость микрофона 0…1 — отдельным сторе, а не состоянием страницы чата.
 * Значение обновляется 12–20 раз в секунду: держи его в useState страницы, и
 * каждый замер перерисовывал бы всю ленту сообщений вместе с маскотом.
 */
interface VoiceLevelState {
  level: number;
  setLevel: (level: number) => void;
}

export const useVoiceLevel = create<VoiceLevelState>()((set) => ({
  level: 0,
  setLevel: (level) => set({ level }),
}));

export type LiveVoiceConfig = Api["LiveVoice"];

interface MetaState {
  voiceAvailable: boolean;
  /** Поднят профиль voice и озвучка разрешена админом — иначе кнопки быть не должно. */
  ttsAvailable: boolean;
  liveVoice: LiveVoiceConfig;
  /** Поднят профиль docs — из PDF приедут иллюстрации, иначе только текст. */
  docsParsing: boolean;
  llmConfigured: boolean;
  lines: Api["MetaResponse"]["lines"];
  feedback: FeedbackConfig;
  operatorQueue: QueueThresholds;
  /** Варианты ползунка «поставщик / заказчик»: подписи задаёт сервер. */
  audiences: AudienceOption[];
  /** Сервер заводит демо-пароли: страница входа вправе их подсказать. */
  demo: boolean;
  /** Предел документа для импорта, МБ; null — мета ещё не приехала. */
  maxDocumentMb: number | null;
  load: () => Promise<void>;
}

/** Ответ `/api/meta`. Поля необязательные: старый сервер мог их не отдавать,
 *  а интерфейс обязан подняться и без них. */
type MetaResponse = Partial<Api["MetaResponse"]>;

export type AudienceOption = Api["AudienceOption"];

/** Через сколько секунд ожидания обращение в очереди подсвечивается и краснеет. */
export type QueueThresholds = Api["QueueThresholds"];

/** Когда и какими словами чат сам просит оценить консультацию. */
export type FeedbackConfig = Api["FeedbackPrompt"];

const LIVE_VOICE_OFF: LiveVoiceConfig = { enabled: false, silence_ms: 900 };
const FEEDBACK_OFF: FeedbackConfig = { idle_prompt_seconds: 0, request_message: "" };
const QUEUE_DEFAULT: QueueThresholds = { warn_seconds: 180, alert_seconds: 600 };
// Пока мета не приехала, ползунок рисуется с теми же подписями, что на сервере
// по умолчанию: иначе на стартовом экране он появлялся бы с задержкой.
const AUDIENCES_DEFAULT: AudienceOption[] = [
  { key: "supplier", label: "Поставщик", hint: "продаю товары, работы или услуги" },
  { key: "customer", label: "Заказчик", hint: "закупаю для организации" },
];

export const useMeta = create<MetaState>()((set) => ({
  voiceAvailable: false,
  ttsAvailable: false,
  docsParsing: false,
  liveVoice: LIVE_VOICE_OFF,
  llmConfigured: false,
  lines: {},
  feedback: FEEDBACK_OFF,
  operatorQueue: QUEUE_DEFAULT,
  audiences: AUDIENCES_DEFAULT,
  demo: false,
  maxDocumentMb: null,
  load: async () => {
    try {
      // Через api.get, а не голым fetch: тот шёл мимо общих заголовков запроса
      // и мимо разбора ошибок, то есть мета жила по своим правилам.
      const data = await api.get<MetaResponse>("/api/meta");
      set({
        voiceAvailable: Boolean(data.voice_available),
        ttsAvailable: Boolean(data.tts_available),
        liveVoice: data.live_voice ?? LIVE_VOICE_OFF,
        docsParsing: Boolean(data.docs_parsing),
        llmConfigured: Boolean(data.llm_configured),
        lines: data.lines ?? {},
        feedback: data.feedback ?? FEEDBACK_OFF,
        operatorQueue: data.operator_queue ?? QUEUE_DEFAULT,
        audiences: Array.isArray(data.audiences) && data.audiences.length
          ? data.audiences : AUDIENCES_DEFAULT,
        demo: Boolean(data.demo),
        maxDocumentMb: data.max_document_mb ?? null,
      });
    } catch {
      /* мета не критична для работы чата */
    }
  },
}));
