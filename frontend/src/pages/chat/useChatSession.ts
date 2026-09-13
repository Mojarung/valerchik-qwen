import { useCallback, useEffect, useMemo, useRef, useState, type RefObject } from "react";
import { api, errorText, wsUrl } from "@/lib/api";
import { readAudience, useAudience, type Audience } from "@/lib/audience";
import { clearChatSession, readChatSession, saveChatSession } from "@/lib/chatSession";
import { pageContext } from "@/lib/pageContext";
import { markRequestSeen, rememberRequest } from "@/lib/requestHistory";
import { speak } from "@/lib/speech";
import type { Api, ChatMessage, MascotMood, WsEvent } from "@/lib/types";
import { connectWs, ownerProtocols } from "@/lib/ws";
import type { Addressee } from "./addressee";

export type SessionInfo = Api["SessionCreated"];

/** Ответ на восстановление диалога: чем он отличается от только что созданного. */
type ResumedSession = Api["SessionState"];

const isAudience = (value: unknown): value is Audience =>
  value === "supplier" || value === "customer";

/** Исправление от сервера: какое сообщение, новый текст, прежний и что чинили. */
export interface LayoutFix {
  messageId: string;
  text: string;
  original: string;
  /** "layout" — забытая раскладка, "typo" — опечатки; бывают оба сразу. */
  kinds: string[];
}

/** Реплика, которую сервер ещё не показал в ленте: набрана, пока диалог открывался. */
export interface PendingMessage {
  id: string;
  text: string;
  /** Ещё ждёт диалога; false — уже ушла в сокет, ждём её в ленте. */
  queued: boolean;
}

/** Сервер показал реплику в ленте — её бледная копия больше не нужна. Ищем по
 *  тексту, иначе берём самую раннюю из ушедших: сокет доставляет их по порядку. */
function settle(pending: PendingMessage[], content: string): PendingMessage[] {
  const sent = pending.filter((p) => !p.queued);
  const done = sent.find((p) => p.text === content) ?? sent[0];
  return done ? pending.filter((p) => p !== done) : pending;
}

export interface ChatSessionOptions {
  /** Озвучивать ли ответ прямо сейчас. Ref, а не значение: обработчик сокета
   *  захватывается один раз на весь диалог. */
  speakRef: RefObject<boolean>;
  /** Сервер исправил раскладку: показать починку и применить её (apply) в нужный миг. */
  onLayoutFixed: (fix: LayoutFix, apply: () => void) => void;
}

export interface ChatSession {
  session: SessionInfo | null;
  messages: ChatMessage[];
  /** Ответ, который ещё набегает по сокету. */
  streamText: string;
  mood: MascotMood;
  /** Почему диалог закрыт (resolved, violation); null — открыт. */
  closed: string | null;
  /** Линия, которой передано обращение; null — ведёт Валерчик. */
  escalatedLine: number | null;
  /** Кому передано обращение — словами; null — ведёт Валерчик. */
  addressee: Addressee | null;
  /** Имя оператора, который уже подключился; null — либо ведёт Валерчик, либо обращение ещё в очереди. */
  operatorName: string | null;
  /** Оператор печатает ответ прямо сейчас — гаснет само через паузу без нового сигнала. */
  operatorTyping: boolean;
  /** Реплики, набранные до открытия диалога: видны в ленте, пока сервер их не покажет. */
  pending: PendingMessage[];
  /** Диалог открывается: создаётся новый или возвращается прежний. */
  connecting: boolean;
  error: string | null;
  sendingImage: boolean;
  /** Номер начала диалога: растёт с каждым стартом, чтобы состояние вне хука
   *  (предложение оценки) сбрасывалось вместе с лентой. */
  generation: number;
  setError: (error: string | null) => void;
  /** Настроение маскота; с ms — на время, потом снова спокоен. */
  setMoodFor: (mood: MascotMood, ms?: number) => void;
  startSession: (fresh?: boolean) => Promise<void>;
  /** Открыть прошлое обращение из «Моих обращений» вместо текущего диалога. */
  openSession: (sessionId: string, owner: string) => void;
  /** Отправить реплику; false — пусто или диалог закрыт. Пока диалог открывается,
   *  реплика ждёт в очереди и уходит, как только он готов. */
  send: (text: string) => boolean;
  changeAudience: (next: Audience | null) => Promise<void>;
  sendImage: (file: File) => Promise<void>;
  /** Отправить оценку; false — не дошла, причина в error. */
  submitRating: (rating: number, comment: string, answers?: FeedbackAnswers) => Promise<boolean>;
}

/** Ответы на «Проблема решена?» и «Понятно объяснили?»; null — вопрос пропущен. */
export interface FeedbackAnswers {
  solved: boolean | null;
  clear: boolean | null;
}

/**
 * Диалог гостя с Валерчиком: создание или возврат прежнего, сокет, лента и
 * поток ответа, отправка, роль, скриншот оператору и оценка.
 */
export function useChatSession({ speakRef, onLayoutFixed }: ChatSessionOptions): ChatSession {
  const [session, setSession] = useState<SessionInfo | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [streamText, setStreamText] = useState("");
  const [mood, setMood] = useState<MascotMood>("idle");
  const [closed, setClosed] = useState<string | null>(null);
  const [addressee, setAddressee] = useState<Addressee | null>(null);
  const escalatedLine = addressee?.line ?? null;
  // Оператор печатает прямо сейчас — сигнал throttled с его стороны, а гаснет
  // сам через паузу здесь: реального события «перестал печатать» сервер не шлёт.
  const [operatorTyping, setOperatorTyping] = useState(false);
  const [pending, setPending] = useState<PendingMessage[]>([]);
  // С первого кадра: диалог начинает открываться сразу при монтировании
  const [connecting, setConnecting] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [sendingImage, setSendingImage] = useState(false);
  const [generation, setGeneration] = useState(0);
  const wsRef = useRef<ReturnType<typeof connectWs> | null>(null);
  // Кадры реплик, набранных до открытия диалога, — с id их копии в ленте
  const queueRef = useRef<{ id: string; frame: object }[]>([]);
  const pendingSeq = useRef(0);
  const supportVersion = useRef(0);
  const seenMessages = useRef(new Set<string>());
  // Номер последнего запуска startSession: ответ устаревшего (двойной эффект
  // StrictMode, «Новый диалог» посреди открытия) не подменит диалог и не
  // заберёт себе реплики из очереди.
  const startSeq = useRef(0);
  const moodTimer = useRef<ReturnType<typeof setTimeout>>(null);
  const operatorTypingTimer = useRef<ReturnType<typeof setTimeout>>(null);
  // Роль «поставщик / заказчик» живёт в куке и своём сторе: её меняет и ползунок,
  // и сервер, распознавший «я заказчик» в сообщении.
  const setAudience = useAudience((s) => s.setAudience);
  const onLayoutFixedRef = useRef(onLayoutFixed);
  useEffect(() => {
    onLayoutFixedRef.current = onLayoutFixed;
  });

  const setMoodFor = useCallback((m: MascotMood, ms?: number) => {
    if (moodTimer.current) clearTimeout(moodTimer.current);
    setMood(m);
    if (ms) moodTimer.current = setTimeout(() => setMood("idle"), ms);
  }, []);

  /** «Печатает» на 2.5с от последнего сигнала — оператор шлёт их не на каждую
   *  клавишу, а этот таймер и есть настоящее «перестал», которого от сервера не будет. */
  const pulseOperatorTyping = useCallback(() => {
    if (operatorTypingTimer.current) clearTimeout(operatorTypingTimer.current);
    setOperatorTyping(true);
    operatorTypingTimer.current = setTimeout(() => setOperatorTyping(false), 2500);
  }, []);

  /** Подменяет текст сообщения на исправленный, сохраняя оригинал для показа пользователю. */
  const applyLayoutFix = useCallback(({ messageId, text, original, kinds }: LayoutFix) => {
    // layout → layout_fixed, typo → typo_fixed: те же флаги, что бэкенд пишет в meta
    const flags = Object.fromEntries(kinds.map((kind) => [`${kind}_fixed`, true]));
    setMessages((prev) => prev.map((m) => (
      m.id === messageId
        ? { ...m, content: text, meta: { ...m.meta, ...flags, original } }
        : m
    )));
  }, []);

  const handleEvent = useCallback((event: WsEvent) => {
    switch (event.type) {
      case "support_state":
        if (event.version < supportVersion.current) break;
        supportVersion.current = event.version;
        if (event.status === "resolved") setClosed("resolved");
        else if (event.status !== "closed_violation") setClosed(null);
        break;
      case "typing":
        setMoodFor("thinking");
        break;
      case "assistant_chunk":
        setStreamText((t) => t + (event.text as string));
        setMoodFor("speaking");
        break;
      case "message": {
        const msg = event.message as ChatMessage;
        if (seenMessages.current.has(msg.id)) break;
        seenMessages.current.add(msg.id);
        setMessages((prev) => (prev.some((m) => m.id === msg.id) ? prev : [...prev, msg]));
        if (msg.role === "user" && msg.content) setPending((prev) => settle(prev, msg.content));
        if (msg.role === "assistant") {
          setStreamText("");
          const meta = msg.meta as { answered?: boolean; escalated?: boolean };
          if (meta.escalated) setMoodFor("idle");
          else if (meta.answered === false) setMoodFor("sorry", 4000);
          else setMoodFor("happy", 1800);
          // Озвучиваем только в голосовом режиме: человек, который печатает,
          // читает быстрее, чем слушает, и внезапный голос из вкладки его
          // скорее напугает. Маскот «говорит», пока идёт звук.
          if (speakRef.current && msg.content) {
            setMoodFor("speaking");
            void speak(msg.content).finally(() => setMoodFor("idle"));
          }
        }
        if (msg.role === "system" && (msg.meta as { kind?: string }).kind === "violation") {
          setMoodFor("alert");
        }
        // Оператор пишет своими сообщениями (role "operator"), а не через
        // assistant_chunk/typing — без этого маскот застревал бы в «thinking»
        // с последнего вопроса, заданного перед тем, как оператор ответил.
        if (msg.role === "operator" || (msg.role === "system" && (msg.meta as { kind?: string }).kind === "operator_joined")) {
          setStreamText("");
          setMoodFor("idle");
        }
        // Реплика оператора и есть то самое «дописал» — индикатор гаснет сразу,
        // не дожидаясь тайм-аута, иначе точки ещё пляшут под уже пришедшим ответом.
        if (msg.role === "operator") {
          if (operatorTypingTimer.current) clearTimeout(operatorTypingTimer.current);
          setOperatorTyping(false);
        }
        break;
      }
      case "layout_fixed": {
        const fix: LayoutFix = {
          messageId: event.message_id as string, text: event.text as string, original: event.original as string,
          // Сервер до опечаток слал событие без kinds — это была раскладка
          kinds: event.kinds?.length ? event.kinds : ["layout"],
        };
        // Ждём, пока уляжется плавная прокрутка ленты к новому сообщению: замер позиции
        // пузыря сразу после отрисовки даёт координаты, которые тут же устаревают.
        setTimeout(() => onLayoutFixedRef.current(fix, () => applyLayoutFix(fix)), 420);
        break;
      }
      case "escalated":
        setAddressee({ line: event.line, lineLabel: event.line_label, externalLabel: event.addressee_label });
        break;
      case "session_closed":
        setClosed(event.reason as string);
        if (event.reason === "violation") setMoodFor("alert");
        break;
      case "audience":
        // Сервер понял роль из слов человека — ползунок должен показать то же
        if (isAudience(event.audience)) setAudience(event.audience);
        break;
      case "operator_typing":
        pulseOperatorTyping();
        break;
      case "error":
        // Сервер отказал реплике (лимит сообщений, диалог уже закрыт): говорим
        // почему, а не оставляем «думающего» маскота и её копию висеть без ответа
        setError(event.detail);
        setPending((prev) => {
          const first = prev.findIndex((p) => !p.queued);
          return first < 0 ? prev : prev.filter((_, i) => i !== first);
        });
        setMoodFor("idle");
        break;
    }
  }, [setMoodFor, applyLayoutFix, setAudience, speakRef, pulseOperatorTyping]);

  /** Статус диалога с сервера → состояние экрана. */
  const applyStatus = useCallback((state: ResumedSession) => {
    if ((state.version ?? 0) < supportVersion.current) return;
    supportVersion.current = state.version ?? 0;
    if (state.status === "resolved") setClosed("resolved");
    else if (state.status === "closed_violation") setClosed("violation");
    else if (["waiting_operator", "operator_assigned", "operator_active", "waiting_user"].includes(state.status)) {
      setClosed(null);
      setAddressee({ line: state.line ?? 1, lineLabel: state.line_label, externalLabel: state.addressee_label });
    }
  }, []);

  /** Реплики, набранные, пока диалог открывался, — в сокет в порядке набора.
   *  Дальше, пока сокет соединяется, их держит его собственная очередь (lib/ws.ts):
   *  здесь ждали только самого диалога, которого до сих пор не было. */
  const flushQueue = useCallback(() => {
    const queued = queueRef.current.splice(0);
    if (!queued.length) return;
    for (const { frame } of queued) wsRef.current?.send(frame);
    const sent = new Set(queued.map((q) => q.id));
    setPending((prev) => prev.map((p) => (sent.has(p.id) ? { ...p, queued: false } : p)));
  }, []);

  /**
   * Открыть диалог: продолжить прежний из куки или завести новый.
   *
   * `fresh = true` — по кнопке «Новый диалог»: тогда прежний не восстанавливаем
   * даже если он ещё жив.
   */
  const startSession = useCallback(async (fresh = false) => {
    const run = ++startSeq.current;
    const current = () => run === startSeq.current;
    supportVersion.current = 0;
    seenMessages.current.clear();
    setGeneration((g) => g + 1);
    setMessages([]);
    setStreamText("");
    setClosed(null);
    setAddressee(null);
    setError(null);
    wsRef.current?.close();
    // До нового сокета реплики копятся в очереди, а не уходят в закрытый старый
    wsRef.current = null;
    // Ушедшие в прежний диалог копии остались в нём; ждущие очереди поедут в новый
    setPending((prev) => prev.filter((p) => p.queued));
    setConnecting(true);

    // Токен владельца сохраняем ДО первого запроса: его подставляет api-клиент,
    // а сокет предъявляет подпротоколом — без него сервер откажет и в записи, и
    // в подключении.
    const attach = async (sessionId: string, owner: string, open: boolean) => {
      saveChatSession(sessionId, owner);
      const history = await api.get<ChatMessage[]>(`/api/chat/sessions/${sessionId}/messages`);
      if (!current()) return;
      setMessages(history);
      wsRef.current = connectWs(
        wsUrl(`/ws/chat/${sessionId}`), handleEvent, (connected) => {
          if (!connected) return;
          void Promise.all([
            api.get<ChatMessage[]>(`/api/chat/sessions/${sessionId}/messages`),
            api.get<ResumedSession>(`/api/chat/sessions/${sessionId}`),
          ]).then(([history, state]) => {
            if (!current()) return;
            setMessages(prev => [...new Map([...prev, ...history].map(m => [m.id, m])).values()]
              .sort((a, b) => a.created_at.localeCompare(b.created_at)));
            applyStatus(state);
          }).catch(() => undefined);
        }, ownerProtocols(owner),
      );
      setConnecting(false);
      // В завершённый диалог набранное не шлём — сервер откажет; оно дождётся
      // нового, а маскот перестаёт «думать» над вопросом, который никуда не ушёл
      if (open) flushQueue();
      else setMoodFor("idle");
    };

    // Сначала пробуем вернуть человека туда, где он был: диалог мог остаться в
    // очереди к оператору, и начинать заново — значит потерять его обращение.
    const saved = fresh ? null : readChatSession();
    if (saved) {
      try {
        const resumed = await api.get<ResumedSession>(`/api/chat/sessions/${saved.sessionId}`);
        if (!current()) return;
        setSession({
          // Приветствия в ответе нет: оно уже лежит первым сообщением истории,
          // и на стартовом экране страница возьмёт его оттуда.
          session_id: resumed.session_id, greeting: "", chips: resumed.chips,
          audience: resumed.audience, owner: saved.owner,
        });
        applyStatus(resumed);
        // Диалог помнит роль, которую человек назвал словами; кука при этом
        // могла остаться пустой — восстанавливаем, но выбор из куки не перебиваем
        if (!readAudience() && isAudience(resumed.audience)) setAudience(resumed.audience);
        await attach(resumed.session_id, saved.owner,
                     resumed.status !== "resolved" && resumed.status !== "closed_violation");
        return;
      } catch {
        // Устаревший запуск куку не трогает: её уже мог записать новый
        if (!current()) return;
        // Диалога уже нет (база пересоздана, срок вышел) — молча заводим новый
        clearChatSession();
      }
    }

    try {
      // Роль читаем из стора, а не из замыкания: иначе она попала бы в
      // зависимости колбэка, и каждый щелчок ползунка заводил бы новый диалог
      const role = useAudience.getState().audience;
      const info = await api.post<SessionInfo>("/api/chat/sessions",
                                                role ? { audience: role } : undefined);
      if (!current()) return;
      setSession(info);
      await attach(info.session_id, info.owner, true);
    } catch (e) {
      if (!current()) return;
      setError(errorText(e, "Не удалось создать диалог"));
      setConnecting(false);
      setMoodFor("idle");
    }
  }, [handleEvent, applyStatus, setAudience, flushQueue, setMoodFor]);

  useEffect(() => {
    void startSession();
    return () => wsRef.current?.close();
  }, [startSession]);

  /** Прошлое обращение открывается тем же путём, что и диалог после перезагрузки:
   *  его пара ложится в куку, а startSession возвращает историю, статус и сокет. */
  const openSession = useCallback((sessionId: string, owner: string) => {
    if (session?.session_id === sessionId && !connecting) return;
    saveChatSession(sessionId, owner);
    void startSession();
  }, [session, connecting, startSession]);

  // Диалог становится обращением с первой реплики человека: пустые диалоги с одним
  // приветствием заводятся на каждом заходе и вытесняли бы из списка настоящие.
  const sessionId = session?.session_id;
  const owner = session?.owner;
  const asked = messages.some((m) => m.role === "user");
  useEffect(() => {
    if (sessionId && owner && asked) rememberRequest(sessionId, owner);
  }, [sessionId, owner, asked]);

  // Что человек уже видел — по часам сервера, чтобы «новый ответ» в списке не
  // зависел от того, спешат ли часы у него на компьютере.
  const lastSeenAt = messages[messages.length - 1]?.created_at;
  useEffect(() => {
    if (sessionId && lastSeenAt) markRequestSeen(sessionId, lastSeenAt);
  }, [sessionId, lastSeenAt]);

  const send = useCallback((text: string) => {
    const trimmed = text.trim();
    if (!trimmed || closed) return false;
    // маскот реагирует сразу, не дожидаясь ответного typing по сети: иначе на медленном
    // соединении между нажатием и любой реакцией интерфейса зияет пауза.
    // Но только пока отвечает Валерчик — после эскалации это уже не поиск
    // в базе знаний, а ожидание человека, и «thinking» никто не снимет:
    // оператор шлёт message, а не typing/assistant_chunk.
    if (escalatedLine === null) setMoodFor("thinking");
    // Раздел портала едет с каждым сообщением, а не с диалогом: человек ходит по
    // сайту с открытым виджетом, и вопрос про оферту задаёт со страницы оферт,
    // даже если разговор начал в личном кабинете. Вне фрейма контекста нет — там
    // это просто null и поиск работает как раньше. Роль — по той же причине:
    // положение ползунка в момент отправки, а не в момент создания диалога.
    const frame = {
      type: "message", request_id: crypto.randomUUID(), text: trimmed, page: pageContext(),
      audience: useAudience.getState().audience ?? undefined,
    };
    if (wsRef.current) {
      wsRef.current.send(frame);
      return true;
    }
    // Диалог ещё открывается (холодный API, медленная сеть). Раньше Enter здесь
    // молча ничего не делал; теперь реплика ждёт в очереди, видна в ленте и
    // уходит, как только диалог готов. Открыть его не вышло — новая реплика
    // и есть повод попробовать ещё раз.
    const id = `pending-${++pendingSeq.current}`;
    queueRef.current.push({ id, frame });
    setPending((prev) => [...prev, { id, text: trimmed, queued: true }]);
    if (!connecting) void startSession();
    return true;
  }, [closed, escalatedLine, connecting, setMoodFor, startSession]);

  /** Имя оператора, который уже написал в чат или явно подключился — из истории,
   *  а не из отдельного состояния: тогда оно верно и для восстановленного диалога. */
  const operatorName = useMemo(() => {
    const joined = [...messages].reverse().find((m) =>
      m.role === "system" && (m.meta as { kind?: string }).kind === "operator_joined");
    if (!joined) return null;
    // meta.operator_name — не для старых обращений: они эскалировались до того,
    // как сервер начал класть имя в meta отдельным полем. Для них разбираем
    // то же самое имя из текста системного сообщения, а не показываем безликое «Оператор».
    return (joined.meta as { operator_name?: string }).operator_name
      ?? /^Оператор (.+) подключился к диалогу\.?$/.exec(joined.content)?.[1]
      ?? "Оператор";
  }, [messages]);

  /** Ползунок: роль — в куку сразу, диалогу — следом, и подсказки под новую роль. */
  const changeAudience = useCallback(async (next: Audience | null) => {
    setAudience(next);
    if (!session) return;
    try {
      const updated = await api.post<Api["AudienceState"]>(
        `/api/chat/sessions/${session.session_id}/audience`, { audience: next ?? "all" },
      );
      setSession((current) => (current ? { ...current, chips: updated.chips } : current));
    } catch {
      /* роль уже в куке и уйдёт со следующим сообщением — подсказки просто не обновятся */
    }
  }, [session, setAudience]);

  /** Скриншот оператору. Сообщение вернётся по websocket — дописывать нечего. */
  const sendImage = useCallback(async (file: File) => {
    if (!session) return;
    setError(null);
    setSendingImage(true);
    try {
      const form = new FormData();
      form.append("file", file);
      await api.upload(`/api/chat/sessions/${session.session_id}/attachment`, form);
    } catch (e) {
      setError(errorText(e, "Не удалось отправить картинку"));
    } finally {
      setSendingImage(false);
    }
  }, [session]);

  const submitRating = useCallback(async (rating: number, comment: string, answers?: FeedbackAnswers) => {
    if (!session) return false;
    try {
      // «Решено?» и «Понятно?» — два измерения методики оценки; пропущенный вопрос
      // уходит null, и бэкенд его просто не учитывает.
      await api.post(`/api/chat/sessions/${session.session_id}/feedback`, { rating, comment, ...answers });
    } catch (e) {
      // Молчаливый провал выглядел бы как «оценку съело»: карточка исчезала бы
      // и без успешной отправки, потому что состояние менялось до ответа сервера.
      setError(errorText(e, "Не удалось отправить оценку"));
      return false;
    }
    setMoodFor("happy", 2000);
    return true;
  }, [session, setMoodFor]);

  return {
    session, messages, streamText, mood, closed, escalatedLine, addressee, operatorName, operatorTyping,
    pending, connecting, error, sendingImage, generation,
    setError, setMoodFor, startSession, openSession, send, changeAudience, sendImage, submitRating,
  };
}
