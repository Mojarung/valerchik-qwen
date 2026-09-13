import { useCallback, useEffect, useRef, useState } from "react";
import { api, errorText, SLOW_TIMEOUT_MS, wsUrl } from "@/lib/api";
import type { Api, ChatMessage, WsEvent, OperatorCaseHint } from "@/lib/types";
import type { AssignmentEvent } from "@/lib/wsEvents";
import { connectWs } from "@/lib/ws";

export type Suggestion = Api["KbHit"];

/** Назначенное обращение: то, что показываем в попапе. */
export type Assignment = Omit<AssignmentEvent, "type" | "operator_id" | "operator_name">;

/** Черновик ответа от ИИ. `generated: false` — модель не отвечала и текст взят из
 *  статьи целиком: такой черновик нужно вычитывать полностью, и это видно оператору. */
export interface Draft extends Partial<Api["CopilotDraft"]> {
  text: string;
  generated: boolean;
}

export interface ConversationOptions {
  /** События очереди: список нужно перезапросить (с паузой — они приходят пачками). */
  onQueueChanged: () => void;
  /** Диспетчер назначил обращение этому оператору. */
  onAssigned: (assignment: Assignment) => void;
}

export interface Conversation {
  /** id открытого обращения; null — ничего не открыто. */
  selected: string | null;
  messages: ChatMessage[];
  /** Статьи-подсказки к последнему вопросу клиента. */
  suggestions: Suggestion[];
  cases: OperatorCaseHint[];
  historyState: "idle" | "loading" | "error";
  historyError: string;
  draft: Draft | null;
  draftLoading: boolean;
  /** Что клиент набирает прямо сейчас. */
  userTyping: string;
  /** Валерчик формирует ответ клиенту прямо сейчас. */
  assistantTyping: boolean;
  /** Ошибка последнего действия: взять, ответить, файл, черновик, завершить. */
  actionError: string;
  uploading: boolean;
  open: (sessionId: string) => Promise<void>;
  /** Назад к списку (на телефоне): обращение больше не открыто. */
  leave: () => void;
  retryHistory: () => void;
  take: () => Promise<boolean>;
  reply: (text: string) => Promise<boolean>;
  sendFile: (file: File, caption: string) => Promise<boolean>;
  requestDraft: () => Promise<void>;
  discardDraft: () => void;
  close: () => Promise<boolean>;
  /** Оператор печатает ответ — дёрнуть на каждое изменение поля, throttle внутри. */
  notifyTyping: () => void;
}

/** Не чаще раза в это время: гостю всё равно достаточно — таймер погасшего
 *  состояния у него самого длиннее (см. useChatSession), а не пришедший вовремя
 *  повтор просто продлит то же «печатает», а не оборвёт его раньше срока. */
const TYPING_THROTTLE_MS = 2000;

/** События сокета, после которых очередь нужно перезапросить. */
const QUEUE_EVENTS = new Set([
  "ticket_changed",
  "new_ticket", "ticket_taken", "ticket_closed", "ticket_assigned", "ticket_reclaimed", "session_violation",
]);

/**
 * Открытое обращение оператора и сокет операторской: подписка на комнаты
 * диалога, переписка и подсказки, черновик копилота, действия с обращением.
 */
export function useConversation({ onQueueChanged, onAssigned }: ConversationOptions): Conversation {
  const [selected, setSelected] = useState<string | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [suggestions, setSuggestions] = useState<Suggestion[]>([]);
  const [cases, setCases] = useState<OperatorCaseHint[]>([]);
  // Загрузка переписки: без явного состояния отказ сервера выглядел как пустой
  // диалог, и оператор отвечал, не видя вопроса.
  const [historyState, setHistoryState] = useState<"idle" | "loading" | "error">("idle");
  const [historyError, setHistoryError] = useState("");
  const [draft, setDraft] = useState<Draft | null>(null);
  const [draftLoading, setDraftLoading] = useState(false);
  const [userTyping, setUserTyping] = useState("");
  const [assistantTyping, setAssistantTyping] = useState(false);
  const [actionError, setActionError] = useState("");
  const [uploading, setUploading] = useState(false);
  const wsRef = useRef<ReturnType<typeof connectWs> | null>(null);
  // Открытое обращение читаем через ref: ответ сервера приходит позже, чем
  // оператор успевает открыть другое, и сверять его нужно с текущим, а не с тем,
  // что было в замыкании на момент запроса.
  const selectedRef = useRef<string | null>(null);
  // id сообщения клиента, ответ на которое мы ждём от копайлота
  const awaitedDraftRef = useRef("");
  const latestVersionRef = useRef(0);
  const pendingSendRef = useRef<{sid: string; text: string; id: string} | null>(null);
  const lastTypingSentRef = useRef(0);
  const callbacks = useRef({ onQueueChanged, onAssigned });
  useEffect(() => {
    callbacks.current = { onQueueChanged, onAssigned };
  });

  const handleEvent = useCallback((event: WsEvent) => {
    if (QUEUE_EVENTS.has(event.type)) {
      callbacks.current.onQueueChanged();
      return;
    }
    switch (event.type) {
      case "support_state":
        if (event.session_id === selectedRef.current) {
          latestVersionRef.current = Math.max(latestVersionRef.current, event.version);
          setDraft(d => d && (d.version ?? 0) < event.version ? {...d, status: "stale"} : d);
        }
        callbacks.current.onQueueChanged();
        break;
      case "reclaimed":
        // Диалог отняли у нас: оператор мог отойти и, вернувшись, начал бы
        // отвечать в обращение, которое уже ведёт кто-то другой.
        if (event.session_id === selectedRef.current) {
          setActionError("Обращение вернулось в очередь: ответа от вас долго не было");
        }
        callbacks.current.onQueueChanged();
        break;
      case "assigned":
        // Адресное событие: сюда попадает только тот оператор, которому
        // обращение и досталось.
        callbacks.current.onAssigned(event as unknown as Assignment);
        callbacks.current.onQueueChanged();   // общее ticket_assigned придёт следом — пауза склеит
        break;
      case "typing":
        setAssistantTyping(true);
        break;
      case "message": {
        const msg = event.message as ChatMessage;
        setMessages((prev) => (prev.some((m) => m.id === msg.id) ? prev : [...prev, msg]));
        setUserTyping("");
        setAssistantTyping(false);
        break;
      }
      case "copilot":
        setSuggestions(event.suggestions as Suggestion[]);
        setCases(event.cases ?? []);
        // Клиент написал новое — прежний черновик отвечает уже не на тот вопрос.
        // Ждём следующий и запоминаем, на какое сообщение он должен ссылаться.
        setDraft(null);
        setDraftLoading(Boolean(event.draft_pending));
        awaitedDraftRef.current = (event.reply_to as string) ?? "";
        break;
      case "copilot_draft":
        // Генерация идёт секунды, за которые клиент мог дописать ещё реплику:
        // без этой проверки в панель прилетал бы ответ на позапрошлый вопрос.
        if ((event.reply_to as string) === awaitedDraftRef.current) {
          const text = event.draft as string;
          setDraft({ ...event, text, generated: Boolean(event.generated),
            status: (event.version ?? 0) < latestVersionRef.current ? "stale" : event.status });
          setDraftLoading(false);
        }
        break;
      case "user_typing":
        setUserTyping(event.text as string);
        break;
    }
  }, []);

  // Сокет открывается один раз за жизнь страницы: обработчик стабилен, а
  // пересоздание сокета теряло бы подписки на комнаты диалога. Права
  // подтверждает кука, которую браузер приложит к рукопожатию сам: раньше здесь
  // стоял `?token=`, и токен оператора оседал в access-логах nginx.

  /** Переписка и стартовые подсказки — через операторские эндпоинты: гостевой
   *  читает диалог только с токеном владельца, которого у сотрудника нет. */
  const loadHistory = useCallback(async (sid: string) => {
    setHistoryState("loading");
    setHistoryError("");
    try {
      const history = await api.get<ChatMessage[]>(`/api/operator/sessions/${sid}/messages`);
      if (selectedRef.current !== sid) return;
      setMessages(history);
      setHistoryState("idle");
    } catch (e) {
      if (selectedRef.current !== sid) return;
      setHistoryError(errorText(e, "сервер не ответил"));
      setHistoryState("error");
      return;
    }
    // Подсказки — не повод держать переписку: грузим следом и без ошибки на экране.
    try {
      const cards = await api.get<Suggestion[]>(`/api/operator/sessions/${sid}/suggestions`);
      if (selectedRef.current === sid) setSuggestions((live) => (live.length ? live : cards));
    } catch {
      /* без стартовых подсказок колонка покажет поиск, живые придут по сокету */
    }
  }, []);

  useEffect(() => {
    wsRef.current = connectWs(wsUrl("/ws/operator"), handleEvent, connected => {
      if (!connected) return;
      callbacks.current.onQueueChanged();
      if (selectedRef.current) void loadHistory(selectedRef.current);
    });
    return () => wsRef.current?.close();
  }, [handleEvent, loadHistory]);

  const open = useCallback(async (sid: string) => {
    const prev = selectedRef.current;
    if (prev) {
      wsRef.current?.send({ type: "unsubscribe", room: `session:${prev}` });
      wsRef.current?.send({ type: "unsubscribe", room: `copilot:${prev}` });
    }
    selectedRef.current = sid;
    latestVersionRef.current = 0;
    setSelected(sid);
    setSuggestions([]);
    setCases([]);
    setDraft(null);
    setDraftLoading(false);
    setActionError("");   // ошибка прежнего обращения к новому не относится
    awaitedDraftRef.current = "";
    setUserTyping("");
    setAssistantTyping(false);
    lastTypingSentRef.current = 0;
    wsRef.current?.send({ type: "subscribe", room: `session:${sid}` });
    wsRef.current?.send({ type: "subscribe", room: `copilot:${sid}` });
    setMessages([]);
    await loadHistory(sid);
  }, [loadHistory]);

  const leave = useCallback(() => {
    selectedRef.current = null;
    setSelected(null);
  }, []);

  const retryHistory = useCallback(() => {
    if (selectedRef.current) void loadHistory(selectedRef.current);
  }, [loadHistory]);

  /** Действие с открытым обращением: ошибка прежнего снимается, новая — видна. */
  const act = useCallback(async (fallback: string, run: (sid: string) => Promise<unknown>) => {
    const sid = selectedRef.current;
    if (!sid) return false;
    setActionError("");
    try {
      await run(sid);
      return true;
    } catch (e) {
      setActionError(errorText(e, fallback));
      return false;
    }
  }, []);

  const take = useCallback(
    () => act("Обращение не удалось взять", (sid) => api.post(`/api/operator/sessions/${sid}/take`)),
    [act],
  );

  // Раньше ошибка отправки уходила в никуда: текст оставался в поле, но
  // оператор был уверен, что ответил. Особенно важно, когда реплику может не
  // пропустить проверка на мат.
  const reply = useCallback(async (text: string) => {
    const trimmed = text.trim();
    if (!trimmed) return false;
    const sid = selectedRef.current ?? "";
    if (pendingSendRef.current?.text !== trimmed || pendingSendRef.current?.sid !== sid) {
      pendingSendRef.current = {sid, text: trimmed, id: crypto.randomUUID()};
    }
    const sent = await act("Сообщение не отправлено",
      (id) => api.post(`/api/operator/sessions/${id}/message`, { text: trimmed, request_id: pendingSendRef.current?.id }));
    if (sent) pendingSendRef.current = null;
    return sent;
  }, [act]);

  /** Скриншот или документ пользователю. Текст из поля уходит подписью — так
   *  «вот форма, о которой я говорил» и сама картинка остаются одним сообщением.
   *  Само сообщение вернётся по сокету, дописывать нечего. */
  const sendFile = useCallback(async (file: File, caption: string) => {
    setUploading(true);
    try {
      return await act("Не удалось отправить файл", (sid) => {
        const form = new FormData();
        form.append("file", file);
        if (caption.trim()) form.append("caption", caption.trim());
        return api.upload(`/api/operator/sessions/${sid}/attachment`, form);
      });
    } finally {
      setUploading(false);
    }
  }, [act]);

  /** Черновик по запросу: автогенерация выключена либо предложенный вариант не подошёл. */
  const requestDraft = useCallback(async () => {
    setDraftLoading(true);
    try {
      await act("Черновик не сгенерирован", async (sid) => {
        const res = await api.post<Api["CopilotDraft"]>(
          `/api/operator/sessions/${sid}/draft`, undefined, { timeoutMs: SLOW_TIMEOUT_MS });
        if (selectedRef.current !== sid) return;
        awaitedDraftRef.current = "";
        setDraft({...res, text: res.draft, status: (res.version ?? 0) < latestVersionRef.current ? "stale" : res.status});
        setSuggestions(res.sources);
        if (res.error) setActionError(res.error);
      });
    } finally {
      setDraftLoading(false);
    }
  }, [act]);

  const close = useCallback(async () => {
    const closed = await act("Обращение не удалось закрыть",
      (sid) => api.post(`/api/operator/sessions/${sid}/close`));
    if (closed) leave();
    return closed;
  }, [act, leave]);

  /** На каждое изменение поля ответа — но дальше сокета не чаще, чем раз в
   *  TYPING_THROTTLE_MS: без этого каждая нажатая клавиша была бы кадром сокета. */
  const notifyTyping = useCallback(() => {
    const sid = selectedRef.current;
    if (!sid) return;
    const now = Date.now();
    if (now - lastTypingSentRef.current < TYPING_THROTTLE_MS) return;
    lastTypingSentRef.current = now;
    wsRef.current?.send({ type: "typing", room: `session:${sid}` });
  }, []);

  return {
    selected, messages, suggestions, cases, historyState, historyError, draft, draftLoading, userTyping, assistantTyping,
    actionError, uploading, open, leave, retryHistory, take, reply, sendFile, requestDraft, close,
    notifyTyping, discardDraft: () => setDraft(null),
  };
}
