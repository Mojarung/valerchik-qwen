import { useRef, useState } from "react";
import { Check, Headset, LoaderCircle, ThumbsDown, ThumbsUp } from "lucide-react";
import { api, errorText } from "@/lib/api";
import type { Api, ChatMessage } from "@/lib/types";

type Reason = Api["AnswerRatingRequest"]["reason"];
type Recovery = Api["AnswerRecoveryRequest"]["action"];

export const FEEDBACK_REASONS = [
  ["unclear", "Непонятно"],
  ["irrelevant", "Не на мой вопрос"],
  ["incorrect", "Ошибка / устарело"],
  ["not_working", "Сделал, но не работает"],
  ["other", "Другое"],
] as const;

export function AnswerRating({ msg, sessionId, canRecover }: {
  msg: ChatMessage; sessionId: string; canRecover: boolean;
}) {
  const [saved, setSaved] = useState({
    value: Number(msg.meta.rating) || 0,
    reason: (msg.meta.rating_reason ?? "") as Reason,
    comment: String(msg.meta.rating_comment ?? ""),
  });
  const [reason, setReason] = useState<Reason>(saved.reason);
  const [comment, setComment] = useState(saved.comment);
  const [detail, setDetail] = useState("");
  const [editing, setEditing] = useState(false);
  const [clarifying, setClarifying] = useState(false);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [note, setNote] = useState("");
  const [continued, setContinued] = useState(false);
  const pending = useRef(false);
  const base = `/api/chat/sessions/${sessionId}/messages/${msg.id}`;

  const persist = async (value: -1 | 0 | 1) => {
    const next = await api.post<Api["AnswerRatingSaved"]>(`${base}/rating`, {
      value, reason: value === -1 ? reason : "", comment: value === -1 ? comment : "",
    });
    setSaved(next);
    if (value !== -1) { setReason(""); setComment(""); setClarifying(false); }
  };

  const rate = async (value: -1 | 0 | 1) => {
    if (pending.current) return;
    pending.current = true;
    setBusy("rating"); setError(""); setNote("");
    try {
      await persist(value);
      setEditing(value === -1);
      setNote(value === -1 ? "Замечание добавлено на проверку" : value === 1 ? "Спасибо за оценку" : "Оценка снята");
    } catch (e) {
      setError(errorText(e, "Оценка не сохранилась"));
    } finally { pending.current = false; setBusy(""); }
  };

  const recover = async (action: Recovery) => {
    if (pending.current) return;
    pending.current = true;
    setBusy(action); setError(""); setNote("");
    try {
      await persist(-1);
      const result = await api.post<Api["ChatReply"]>(`${base}/recovery`, { action, detail }, { timeoutMs: 150_000 });
      setContinued(true); setEditing(false);
      setNote(result.status === "forwarded_to_operator" || result.escalated
        ? "Диалог передан оператору вместе с вашим замечанием"
        : "Продолжение добавлено в диалог — оцените, помогло ли оно");
    } catch (e) {
      setError(errorText(e, "Не удалось продолжить диалог"));
    } finally { pending.current = false; setBusy(""); }
  };

  return (
    <div className="mt-2 space-y-2 text-xs text-(--color-ink-soft)">
      <div className="flex flex-wrap items-center gap-1">
        <span className="mr-1">{msg.meta.recovery_of ? "Теперь ответ помог?" : "Ответ помог?"}</span>
        <button type="button" className={`rate-btn ${saved.value === 1 ? "text-(--color-ok-ink)" : ""}`}
          aria-label="Ответ помог" aria-pressed={saved.value === 1} disabled={Boolean(busy)}
          onClick={() => void rate(saved.value === 1 ? 0 : 1)}><ThumbsUp size={14} /></button>
        <button type="button" className={`rate-btn ${saved.value === -1 ? "text-(--color-brand-ink)" : ""}`}
          aria-label="Ответ не помог" aria-pressed={saved.value === -1} disabled={Boolean(busy)}
          onClick={() => void rate(saved.value === -1 ? 0 : -1)}><ThumbsDown size={14} /></button>
        {saved.value === -1 && !editing && (
          <button type="button" className="ml-1 underline underline-offset-2" onClick={() => setEditing(true)}>
            Уточнить причину
          </button>
        )}
        {busy && <LoaderCircle size={13} className="ml-1 animate-spin" aria-label="Отправка" />}
      </div>
      {note && <p role="status" className="flex items-start gap-1"><Check size={13} className="mt-0.5 shrink-0" />{note}</p>}
      {saved.value === -1 && editing && (
        <div className="space-y-3 rounded-2xl border border-(--color-line) bg-(--color-surface) p-3">
          <div>
            <p className="mb-2 font-semibold text-(--color-ink)">Что не получилось? <span className="font-normal text-(--color-ink-soft)">Необязательно</span></p>
            <div className="flex flex-wrap gap-1.5" role="group" aria-label="Причина оценки">
              {FEEDBACK_REASONS.map(([value, label]) => (
                <button type="button" key={value} disabled={Boolean(busy)} aria-pressed={reason === value}
                  onClick={() => setReason(reason === value ? "" : value)}
                  className={`rounded-full border px-2.5 py-1.5 transition-colors ${reason === value
                    ? "border-(--color-brand) bg-(--color-brand-soft) text-(--color-brand-ink)"
                    : "border-(--color-line) hover:bg-(--color-surface-2)"}`}>{label}</button>
              ))}
            </div>
          </div>
          <textarea className="input resize-none" rows={2} maxLength={2000} disabled={Boolean(busy)}
            aria-label="Комментарий к ответу" placeholder="Что стоит исправить? (необязательно)"
            value={comment} onChange={(e) => setComment(e.target.value)} />
          <div className="flex flex-wrap gap-2">
            <button type="button" className="btn btn-ghost text-xs" disabled={Boolean(busy)} onClick={() => void rate(-1)}>
              Сохранить уточнение
            </button>
            <button type="button" className="btn btn-ghost text-xs" disabled={Boolean(busy)} onClick={() => setEditing(false)}>Свернуть</button>
          </div>
          {canRecover && !continued && (
            <div className="space-y-2 border-t border-(--color-line) pt-3">
              <p className="font-semibold text-(--color-ink)">Продолжим решать вопрос</p>
              <div className="flex flex-wrap gap-2">
                {(!reason || reason === "unclear") && (
                  <button type="button" className="btn btn-primary text-xs" disabled={Boolean(busy)}
                    onClick={() => void recover("explain")}>Объяснить по шагам</button>
                )}
                <button type="button" className="btn btn-ghost text-xs" disabled={Boolean(busy)}
                  onClick={() => setClarifying((v) => !v)}>Уточнить вопрос</button>
                <button type="button" className="btn btn-ghost text-xs" disabled={Boolean(busy)}
                  onClick={() => void recover("operator")}><Headset size={13} /> Позвать оператора</button>
              </div>
              {clarifying && (
                <div className="space-y-2">
                  <textarea className="input resize-none" rows={2} maxLength={2000} disabled={Boolean(busy)}
                    aria-label="Уточнение вопроса" placeholder="Что вы хотите сделать и на каком шаге застряли?"
                    value={detail} onChange={(e) => setDetail(e.target.value)} />
                  <button type="button" className="btn btn-primary text-xs" disabled={Boolean(busy) || !detail.trim()}
                    onClick={() => void recover("clarify")}>Продолжить с уточнением</button>
                </div>
              )}
            </div>
          )}
        </div>
      )}
      {error && <p role="alert" className="text-(--color-brand-ink)">{error}</p>}
    </div>
  );
}
