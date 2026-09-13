import { useEffect, useState } from "react";
import { api, errorText } from "@/lib/api";
import type { ChatMessage } from "@/lib/types";

type State = {status: string; version: number; waiting_reason: string | null; unread: number; line: number | null};

export function SupportNotice({ sessionId, messages, onConfirmed }: {
  sessionId: string; messages: ChatMessage[]; onConfirmed: () => void;
}) {
  const [state, setState] = useState<State | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    let active = true;
    const refresh = async () => {
      try { const next = await api.get<State>(`/api/chat/sessions/${sessionId}/support`); if (active) setState(previous => previous && previous.version > next.version ? previous : next); }
      catch { /* The main chat already reports connection errors. */ }
    };
    void refresh(); const timer = setInterval(() => void refresh(), 3000);
    return () => { active = false; clearInterval(timer); };
  }, [sessionId, messages.length]);
  useEffect(() => { setState(null); setError(""); }, [sessionId]);
  useEffect(() => {
    const mark = () => {
      const last = messages.at(-1);
      if (last && document.visibilityState === "visible") {
        void api.post(`/api/chat/sessions/${sessionId}/read`, {message_id: last.id}).catch(() => undefined);
      }
    };
    mark(); document.addEventListener("visibilitychange", mark);
    return () => document.removeEventListener("visibilitychange", mark);
  }, [sessionId, messages]);
  // Статус очереди и направление дублировали шапку чата и только мешали: плашка
  // нужна, лишь когда оператор просит подтвердить решение.
  if (!state || state.status !== "waiting_user" || state.waiting_reason !== "solution") return null;
  const confirm = async (solved: boolean) => {
    setBusy(true); setError("");
    try {
      await api.post(`/api/chat/sessions/${sessionId}/solution`, {solved, version: state.version});
      onConfirmed();
    } catch (e) { setError(errorText(e, "Не удалось сохранить ответ")); }
    finally { setBusy(false); }
  };
  return <section className="mx-3 mb-2 rounded-xl bg-(--color-surface-2) p-3 text-sm" aria-label="Подтверждение решения">
    <p>Вопрос решён?</p>
    <div className="mt-2 flex gap-2">
      <button className="btn btn-primary" disabled={busy} onClick={() => void confirm(true)}>Помогло</button>
      <button className="btn btn-ghost" disabled={busy} onClick={() => void confirm(false)}>Не решено</button>
    </div>
    {error && <p role="alert">{error}</p>}
  </section>;
}
