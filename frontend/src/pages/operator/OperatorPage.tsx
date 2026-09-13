import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { useAuth, useMeta } from "@/lib/store";
import type { Api } from "@/lib/types";
import { AssignmentPopup } from "./AssignmentPopup";
import { ConversationColumn } from "./ConversationColumn";
import { HintsColumn } from "./HintsColumn";
import { QueueColumn } from "./QueueColumn";
import { ReplyComposer } from "./ReplyComposer";
import { SupportActions } from "./SupportActions";
import { useConversation, type Assignment } from "./useConversation";
import { useOperatorQueue } from "./useOperatorQueue";
import { directionName } from "./presentation";

/**
 * Рабочее место оператора: очередь, переписка открытого обращения и подсказки
 * из базы знаний. Данные и сокет живут в useOperatorQueue и useConversation;
 * здесь — поле ответа и то, что связывает колонки.
 */
export default function OperatorPage() {
  const lines = useMeta((s) => s.lines);
  const thresholds = useMeta((s) => s.operatorQueue);
  const queue = useOperatorQueue();
  const role = useAuth((s) => s.role);
  // Линию спрашиваем у сервера при каждом открытии страницы, а не храним в сессии
  // браузера: админ меняет её на лету, и очередь сервер уже фильтрует по новой.
  const [myLine, setMyLine] = useState<number | null | undefined>(undefined);
  useEffect(() => {
    let active = true;
    api.get<Api["MeResponse"]>("/api/auth/me")
      .then((me) => { if (active) setMyLine(me.line ?? null); })
      .catch(() => undefined);
    return () => { active = false; };
  }, []);
  // Обращение, назначенное диспетчером: попап висит, пока оператор не отреагирует
  const [assignment, setAssignment] = useState<Assignment | null>(null);
  const conversation = useConversation({ onQueueChanged: queue.scheduleReload, onAssigned: setAssignment });
  const [inputs, setInputs] = useState<Record<string, string>>({});
  const input = inputs[conversation.selected ?? ""] ?? "";
  const setInput = (value: string) => {
    const sid = conversation.selected;
    if (sid) setInputs(previous => ({...previous, [sid]: value}));
  };
  // На телефоне третья колонка не помещается рядом с перепиской, а без неё у
  // оператора нет ни подсказок из базы знаний, ни поиска по ней. Показываем её
  // тем же приёмом, что и очередь: вместо переписки, с возвратом стрелкой.
  const [hints, setHints] = useState(false);
  const { selected, suggestions } = conversation;
  const current = queue.sessions.find((s) => s.session_id === selected);
  const isMine = current?.available_actions?.includes("message") ?? false;

  const take = async () => {
    if (await conversation.take()) queue.reload();
  };
  const send = async () => {
    const sid = selected;
    if (await conversation.reply(input)) {
      void draftFeedback(input === conversation.draft?.text ? "sent" : "edited");
      if (sid) setInputs(previous => ({...previous, [sid]: ""}));
    }
  };
  const draftFeedback = async (action: "inserted" | "discarded" | "sent" | "edited") => {
    if (!selected || !conversation.draft?.run_id) return;
    await api.post(`/api/operator/sessions/${selected}/copilot-feedback`,
      {run_id: conversation.draft.run_id, action, text: input}).catch(() => undefined);
  };
  const sendFile = async (file: File) => {
    if (await conversation.sendFile(file, input)) setInput("");
  };
  const onInput = (text: string) => {
    setInput(text);
    conversation.notifyTyping();
  };
  const insert = (text: string) => {
    setInput(text);
    setHints(false);
  };
  return (
    <div className={`mx-auto grid h-full w-full max-w-7xl grid-cols-1 gap-3 p-3 ${
      hints ? "lg:grid-cols-[280px_minmax(0,1fr)_320px]" : "lg:grid-cols-[320px_minmax(0,1fr)]"
    }`}>
      <h1 className="sr-only">Рабочее место оператора</h1>
      <QueueColumn filter={queue.filter} onFilter={queue.setFilter} sessions={queue.sessions}
        error={queue.error} onRetry={queue.reload} selected={selected}
        onOpen={(sid) => void conversation.open(sid)} waitingOf={queue.waitingOf}
        thresholds={thresholds} lines={lines}
        myLine={myLine === undefined ? undefined
          : role === "admin" ? "Все линии"
          : myLine ? `Линия ${myLine} · ${directionName(lines, myLine)}` : "Линия не назначена"} />

      <ConversationColumn current={current} visible={Boolean(selected) && !hints}
        actions={current && <SupportActions key={current.session_id} ticket={current} onChanged={queue.reload} />}
        lineLabel={current?.line ? lines[String(current.line)]?.label : undefined}
        isMine={isMine} conversation={conversation}
        onBack={() => { conversation.leave(); setHints(false); }}
        onShowHints={() => setHints(true)}
        onTake={() => void take()}>
        <ReplyComposer input={input} onInput={onInput} onSend={() => void send()}
          onFile={(file) => void sendFile(file)} uploading={conversation.uploading}
          actionError={conversation.actionError} draft={conversation.draft}
          draftLoading={conversation.draftLoading} onRequestDraft={() => void conversation.requestDraft()}
          onDraftAction={(action) => { void draftFeedback(action); if (action === "discarded") conversation.discardDraft(); }} />
      </ConversationColumn>

      <HintsColumn open={hints} onBack={() => setHints(false)} suggestions={suggestions} cases={conversation.cases} onInsert={insert} />

      {assignment && (
        <AssignmentPopup assignment={assignment}
          direction={assignment.line ? lines[String(assignment.line)]?.label : undefined}
          onOpen={() => { setAssignment(null); void conversation.open(assignment.session_id); }}
          onLater={() => setAssignment(null)} />
      )}
    </div>
  );
}
