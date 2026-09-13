import { useCallback, useEffect, useRef, useState } from "react";
import { CircleAlert, Headset } from "lucide-react";
import { gsap } from "gsap";
import { useGSAP } from "@gsap/react";
import { useAudience, type Audience } from "@/lib/audience";
import { useRequestsSheet } from "@/lib/requestsSheet";
import { useMeta } from "@/lib/store";
import type { ClarifyOption } from "@/lib/types";
import { Mascot } from "@/components/Mascot";
import { PanelToggle } from "@/components/RequestsButton";
import { Modal } from "@/components/Modal";
import { Bubble } from "@/pages/chat/Bubble";
import { transferText } from "@/pages/chat/addressee";
import { ChatHeader } from "@/pages/chat/ChatHeader";
import { ChatHero } from "@/pages/chat/ChatHero";
import { Composer } from "@/pages/chat/Composer";
import { SupportNotice } from "@/pages/chat/SupportNotice";
import { liveOperatorOffer } from "@/pages/chat/operatorOffer";
import { PendingBubble } from "@/pages/chat/PendingBubble";
import { RatedNote, RatingCard } from "@/pages/chat/Rating";
import { RequestList } from "@/pages/chat/RequestList";
import { useChatSession } from "@/pages/chat/useChatSession";
import { useLayoutFixer } from "@/pages/chat/useLayoutFixer";
import { useRatingPrompt } from "@/pages/chat/useRatingPrompt";
import { isOpen, useRequestHistory, type RequestCard } from "@/pages/chat/useRequestHistory";
import { useSpeechOutput } from "@/pages/chat/useSpeechOutput";
import { useSuggest } from "@/pages/chat/useSuggest";
import { useVoiceInput } from "@/pages/chat/useVoiceInput";

gsap.registerPlugin(useGSAP);

/**
 * Чат гостя с Валерчиком. Страница только собирает части: диалог и сокет —
 * useChatSession, голос — useVoiceInput и useSpeechOutput, предложение оценки —
 * useRatingPrompt, подсказки ввода — useSuggest, починка раскладки — useLayoutFixer.
 */
export default function ChatPage() {
  const rootRef = useRef<HTMLDivElement>(null);
  const bottomRef = useRef<HTMLDivElement>(null);
  // «ремонтник» — отдельный маскот поверх страницы: убегает из шапки к сообщению
  // с забытой раскладкой, чинит его и возвращается
  const homeMascotRef = useRef<HTMLDivElement>(null);
  const fixerRef = useRef<HTMLDivElement>(null);
  // Озвучивать ли ответы прямо сейчас: решает useSpeechOutput, читает обработчик сокета
  const speakRef = useRef(false);
  const [input, setInput] = useState("");
  const [inputFlash, setInputFlash] = useState(false);
  const ttsAvailable = useMeta((s) => s.ttsAvailable);
  const feedbackCfg = useMeta((s) => s.feedback);
  // Роль «поставщик / заказчик» живёт в куке и своём сторе: её меняет и ползунок,
  // и сервер, распознавший «я заказчик» в сообщении.
  const audience = useAudience((s) => s.audience);
  const setAudience = useAudience((s) => s.setAudience);

  const fixer = useLayoutFixer(homeMascotRef, fixerRef);
  const {
    session, messages, streamText, mood, closed, escalatedLine, addressee, operatorName, operatorTyping,
    pending, connecting, error, sendingImage, generation,
    setError, startSession, openSession, send: sendMessage, changeAudience, sendImage, submitRating,
  } = useChatSession({ speakRef, onLayoutFixed: fixer.play });

  const userMessageCount = messages.filter((m) => m.role === "user").length;
  // Реплика, набранная до открытия диалога, — уже разговор: стартовый экран
  // спрятал бы её, и Enter снова выглядел бы как «ничего не произошло»
  const isFresh = userMessageCount === 0 && pending.length === 0 && !closed;
  // Текст под заголовком стартового экрана. У восстановленного диалога поле
  // greeting пустое (сервер его не повторяет), и абзац исчезал, оставляя дыру
  // между заголовком и переключателем роли — берём приветствие из истории.
  const heroGreeting = session?.greeting
    || messages.find((m) => m.role === "assistant")?.content
    || "";
  // Лента без приветствия: оно уже показано на стартовом экране, а в переписке
  // над первым вопросом читалось как реплика, которой человек не видел, когда
  // спрашивал. Разговор начинает он сам.
  const feed = messages.filter((m) => (m.meta as { kind?: string }).kind !== "greeting");
  // Обращение передано человеку: с этого момента можно прислать скриншот
  const withOperator = escalatedLine !== null;

  // «Мои обращения»: перечитываются, когда у открытого диалога меняется то, что
  // видно в карточке, — появился вопрос, передан специалисту, подключился, закрыт
  const requests = useRequestHistory({
    currentId: session?.session_id ?? null,
    refreshKey: [session?.session_id, userMessageCount > 0, escalatedLine, operatorName, closed].join("|"),
  });
  // Модалку открывает кнопка в навигации сайта или в подвале виджета — через стор
  const requestsOpen = useRequestsSheet((s) => s.open);
  const setRequestsOpen = useRequestsSheet((s) => s.setOpen);
  const setRequestsSummary = useRequestsSheet((s) => s.setSummary);
  const panelCollapsed = useRequestsSheet((s) => s.panelCollapsed);
  useEffect(() => {
    setRequestsSummary(requests.items.filter((item) => isOpen(item.status)).length,
                       requests.items.some((item) => item.unread));
  }, [requests.items, setRequestsSummary]);
  const openRequest = useCallback((item: RequestCard) => {
    setRequestsOpen(false);
    openSession(item.session_id, item.owner);
  }, [openSession, setRequestsOpen]);
  const newDialog = useCallback(() => {
    setRequestsOpen(false);
    void startSession(true);
  }, [startSession, setRequestsOpen]);
  const requestList = (inPanel: boolean) => (
    <RequestList items={requests.items} loading={requests.loading} error={requests.error}
      currentId={session?.session_id ?? null} onOpen={openRequest} onNew={newDialog}
      onRetry={() => void requests.refresh()} heading={inPanel}
      collapsed={inPanel && panelCollapsed}
      toggle={inPanel ? <PanelToggle /> : undefined} />
  );

  const { askRating, rated, ratedStars, hide: hideRating, decline: declineRating, markRated } = useRatingPrompt({
    generation,
    idleSeconds: feedbackCfg.idle_prompt_seconds || 0,
    busy: mood === "thinking" || Boolean(streamText) || input.trim().length > 0,
    // При эскалации молчим: человек ждёт оператора, и такой диалог закрывает
    // оператор — тогда карточка покажется по session_closed.
    blocked: Boolean(closed) || escalatedLine !== null || !messages.some((m) => m.role === "assistant"),
    lastMessageId: messages[messages.length - 1]?.id,
  });
  const { suggestions, hideFor } = useSuggest(input);

  const send = useCallback((text: string) => {
    if (!sendMessage(text)) return;
    setInput("");     // слот с кнопкой схлопнется, композер плавно расширится обратно
    hideRating();     // разговор продолжается — карточка оценки убирается сама
  }, [sendMessage, hideRating]);

  const voice = useVoiceInput({
    closed: Boolean(closed),
    onDictation: (text) => {
      setInput((prev) => (prev ? `${prev} ${text}` : text));
      // подсветка поля: показываем, куда именно приехал распознанный текст
      setInputFlash(true);
      setTimeout(() => setInputFlash(false), 900);
    },
    onUtterance: send,
    onError: setError,
  });
  const speech = useSpeechOutput(speakRef, { ttsAvailable, liveState: voice.liveState, listening: voice.listening });

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
    // Карточка оценки и «Диалог завершён» появляются по закрытию или просьбе
    // оценить, а не по новому сообщению: без этих зависимостей звёзды оставались
    // под краем ленты, и до них надо было догадаться доскроллить.
  }, [messages, streamText, mood, operatorTyping, closed, askRating, rated]);

  // Что объявить незрячему собеседнику: последняя реплика не от него самого.
  // Свои сообщения он и так знает — их зачитывание только мешает.
  const [announcement, setAnnouncement] = useState("");
  useEffect(() => {
    const last = messages[messages.length - 1];
    if (!last || last.role === "user") return;
    const who = last.role === "operator" ? "Оператор" : last.role === "system" ? "Система" : "Валерчик";
    setAnnouncement(`${who}: ${last.content}`);
  }, [messages]);

  // «слаймовые» анимации: приветственный экран и чипсы (пружинный stagger).
  // fromTo + clearProps: даже при лагах элементы гарантированно приходят в финальное состояние
  useGSAP(() => {
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    if (isFresh && session) {
      gsap.fromTo(".hero-mascot", { scale: 0.55, autoAlpha: 0 },
        { scale: 1, autoAlpha: 1, duration: 0.5, ease: "back.out(1.8)", clearProps: "all" });
      gsap.fromTo(".hero-text", { y: 16, autoAlpha: 0 },
        { y: 0, autoAlpha: 1, duration: 0.4, delay: 0.1, ease: "back.out(1.6)", clearProps: "all" });
      gsap.fromTo(".hero-chip", { y: 14, scale: 0.8, autoAlpha: 0 },
        { y: 0, scale: 1, autoAlpha: 1, duration: 0.4, delay: 0.16,
          stagger: 0.05, ease: "back.out(2)", clearProps: "all" });
    }
  }, { scope: rootRef, dependencies: [isFresh, session?.session_id] });

  /** Вариант уточнения: у уточнения роли нажатие ещё и переводит ползунок. */
  const pickOption = useCallback((text: string, option?: ClarifyOption) => {
    if (option?.audience) setAudience(option.audience);
    send(text);
  }, [send, setAudience]);

  // Под каким ответом кнопки «Позвать оператора» ещё действуют. Не под любым
  // с флагом: обращение уже у оператора или закрыто — звать некого; ждём
  // ответа — нажатие ушло бы вдогонку ещё не отвеченному вопросу.
  const offerId = liveOperatorOffer(messages, {
    closed: Boolean(closed),
    escalated: escalatedLine !== null,
    busy: mood === "thinking" || Boolean(streamText),
  });

  /** Кнопка «Позвать оператора» — та же отправка, что и из поля ввода, только
   *  набранный черновик не стирается: человек нажал кнопку, а не отправил текст. */
  const callOperator = useCallback((text: string) => {
    if (sendMessage(text)) hideRating();
  }, [sendMessage, hideRating]);

  /** «Нет, спрошу иначе» — просто вернуть человека в поле вопроса. Поле ищем
   *  по разметке: Composer не отдаёт наружу ссылку на него. */
  const askAgain = useCallback(() => {
    rootRef.current?.querySelector<HTMLTextAreaElement>('textarea[aria-label="Сообщение"]')?.focus();
  }, []);

  const rate = useCallback(async (stars: number, comment: string, answers?: { solved: boolean | null; clear: boolean | null }) => {
    if (await submitRating(stars, comment, answers)) markRated(stars);
  }, [submitRating, markRated]);

  const onAudience = (next: Audience | null) => void changeAudience(next);

  return (
    <div className="mx-auto flex h-full w-full max-w-6xl gap-5 px-3 sm:px-4">
      {/* Боковая панель — только там, где рядом с ней чату хватает ширины. Уже —
          тот же список открывается кнопкой в шапке чата. */}
      {/* Свёрнутая панель остаётся узкой полоской: переключатель на том же месте,
          под ним «Новый диалог» — как в чатах нейросетей. Разметка у обоих
          состояний одна: панель едет по ширине, а список и заголовок схлопываются
          внутри неё. Две ветки подменяли поддерево в одном кадре — открытие и
          закрытие были рваными. */}
      <aside id="requests-panel" aria-label="Мои обращения"
        className={`hidden shrink-0 flex-col overflow-hidden pt-3 transition-[width] duration-300 ease-(--ease-out-soft) lg:flex ${
          panelCollapsed ? "w-9" : "w-72"}`}>
        {requestList(true)}
      </aside>
      <div ref={rootRef} className="mx-auto flex h-full w-full min-w-0 max-w-3xl flex-1 flex-col">
        {/* Ремонтник поверх всей страницы: fixed, чтобы координаты не зависели от прокрутки ленты.
            Видимостью управляет GSAP через autoAlpha, а не React: при переключении display
            состоянием первые кадры пути терялись — маскот успевал уехать, пока шёл ререндер,
            и со стороны выглядело так, будто он просто исчез из шапки. */}
        <div ref={fixerRef} aria-hidden className="mascot-fixer pointer-events-none fixed z-40">
          <Mascot mood={fixer.fixerMood} size={56} />
        </div>

        {/* Живая область объявлений.
            Есть в разметке с первого кадра, ещё до первого сообщения: область,
            добавленную вместе с текстом, скринридер не читает — он объявляет
            только изменения того, что уже существовало. Сюда кладём готовый ответ
            целиком, а не поток кусками: иначе стриминг читался бы по слогам.
            Сама лента при этом не живая — иначе каждое обновление чипсов-источников
            зачитывалось бы заново. */}
        <div className="sr-only" role="status" aria-live="polite" aria-atomic="true">
          {announcement}
        </div>

        {!isFresh && (
          <ChatHeader homeRef={homeMascotRef} mascotAway={fixer.mascotAway} mood={mood}
            listening={voice.listening} closed={Boolean(closed)} addressee={addressee}
            operatorName={operatorName} operatorTyping={operatorTyping} audience={audience}
            onAudience={onAudience} onRestart={() => void startSession(true)} />
        )}
        {requestsOpen && (
          <Modal title="Мои обращения" onClose={() => setRequestsOpen(false)}>
            {requestList(false)}
          </Modal>
        )}

        {addressee && !operatorName && !closed && (
          <div className="card animate-(--animate-fade-up) mb-2 flex items-center gap-2 border-l-4 border-l-(--color-warn) px-4 py-2.5 text-xs">
            <Headset size={15} className="shrink-0 text-(--color-warn-ink)" />
            {transferText(addressee)}
          </div>
        )}
        {error && (
          <div className="card mb-2 flex items-center gap-2 border-l-4 border-l-(--color-brand) px-4 py-2.5 text-xs">
            <CircleAlert size={15} className="shrink-0 text-(--color-brand-ink)" /> {error}
            <button className="ml-auto underline" onClick={() => setError(null)}>скрыть</button>
          </div>
        )}

        {/* приветственный экран или лента сообщений */}
        {isFresh && session ? (
          <ChatHero greeting={heroGreeting} chips={session.chips} audience={audience}
            listening={voice.listening} onAudience={onAudience} onPick={send} />
        ) : (
          <div className="min-h-0 flex-1 overflow-y-auto pb-3">
            <h1 className="sr-only">Чат с Валерчиком</h1>
            {/* Короткий диалог прижимаем к композеру, а не к шапке: иначе между
                последним ответом и полем ввода зияла пустая половина экрана.
                min-h-full + justify-end вместо justify-end на скролл-контейнере —
                иначе при переполнении верх ленты обрезается и до него не доскроллить. */}
            <div className="flex min-h-full flex-col justify-end space-y-3"
                 role="log" aria-label="Переписка с Валерчиком">
              {feed.map((m) => (
                // Колбэки предложения — только живому ответу: у остальных пропсы не
                // меняются, и memo пузыря не пересчитывает всю ленту на каждом чанке.
                <Bubble key={m.id} msg={m} sessionId={session?.session_id} onPick={pickOption}
                  onOperator={m.id === offerId ? callOperator : undefined}
                  onAskAgain={m.id === offerId ? askAgain : undefined}
                  canRecover={!closed && !withOperator && mood !== "thinking" && !streamText
                    && messages[messages.length - 1]?.id === m.id} />
              ))}
              {/* Набранное, пока диалог открывался: видно сразу, с подписью, что с ним.
                  В завершённый диалог оно не уходит — «повторить» открывает новый. */}
              {pending.map((p) => (
                <PendingBubble key={p.id} text={p.text}
                  state={!p.queued ? "sending" : connecting ? "waiting" : "failed"}
                  onRetry={() => void startSession(Boolean(closed))} />
              ))}
              {((mood === "thinking" && !streamText) || operatorTyping) && (
                <div className="flex justify-start">
                  <div className="card flex items-center gap-1.5 rounded-3xl rounded-bl-lg px-5 py-4">
                    <span className="typing-dot" /><span className="typing-dot" /><span className="typing-dot" />
                  </div>
                </div>
              )}
              {streamText && (
                <div className="flex justify-start">
                  <div className="card max-w-[85%] rounded-3xl rounded-bl-lg px-4 py-3 text-sm whitespace-pre-wrap">
                    {streamText}
                    <span className="ml-0.5 inline-block h-3.5 w-1.5 animate-pulse rounded-full bg-(--color-brand) align-middle" />
                  </div>
                </div>
              )}
              {(closed === "resolved" || askRating) && !rated && (
                <RatingCard onRate={rate}
                  note={askRating && !closed ? feedbackCfg.request_message : ""}
                  onDismiss={askRating && !closed ? declineRating : undefined} />
              )}
              {/* Карточка просто исчезала после отправки — со стороны неотличимо от
                  «кнопка не сработала». Оставляем след: что именно ушло. */}
              {rated && <RatedNote stars={ratedStars} />}
              {closed && (
                <div className="py-2 text-center text-xs text-(--color-ink-soft)">
                  {closed === "violation"
                    ? "Обращение завершено за нарушение правил общения."
                    : "Диалог завершён. Спасибо за обращение!"}
                  {" "}
                  <button className="font-medium text-(--color-brand-ink) underline" onClick={() => void startSession(true)}>
                    Начать новый
                  </button>
                </div>
              )}
              <div ref={bottomRef} />
            </div>
          </div>
        )}

        {/* автодополнение */}
        {suggestions.length > 0 && !closed && (
          <div className="card animate-(--animate-fade-up) mb-1.5 max-h-40 overflow-y-auto py-1.5 text-sm">
            {suggestions.map((s) => (
              <button
                key={s}
                className="block w-full px-4 py-2 text-left transition-colors hover:bg-(--color-brand-soft) hover:text-(--color-brand-ink)"
                onClick={() => { setInput(s); hideFor(s); }}
              >
                {s}
              </button>
            ))}
          </div>
        )}

        {session && <SupportNotice key={session.session_id} sessionId={session.session_id} messages={messages}
          onConfirmed={() => void startSession()} />}
        <Composer input={input} onInput={setInput} onSend={send} closed={Boolean(closed)} flash={inputFlash}
          voice={voice} speech={speech} withOperator={withOperator} sendingImage={sendingImage}
          onImage={(file) => void sendImage(file)} />
      </div>
    </div>
  );
}
