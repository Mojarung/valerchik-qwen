import { useState, type ReactNode } from "react";
import { AlertTriangle, ArrowRightLeft, CheckCircle2, HelpCircle, UserRound, UsersRound } from "lucide-react";
import { api, errorText } from "@/lib/api";
import type { OperatorSession } from "@/lib/types";
import { useApiData } from "@/lib/useApiData";
import { escalationSummary } from "./presentation";

export function SupportActions({ ticket, onChanged }: { ticket: OperatorSession; onChanged: () => void }) {
  const [action, setAction] = useState<"wait" | "resolve" | "transfer" | "reassign" | null>(null);
  const [reason, setReason] = useState("");
  const [line, setLine] = useState(ticket.line === 1 ? 2 : 1);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const allowed = ticket.available_actions ?? [];
  const staff = useApiData<{id: string; name: string}[]>(allowed.includes("reassign") ? `/api/operator/sessions/${ticket.session_id}/staff` : null);
  const [operatorId, setOperatorId] = useState("");
  const summary = escalationSummary(ticket.reason);
  const topic = typeof ticket.routing?.topic === "string" ? ticket.routing.topic.trim() : "";
  const audience = ticket.audience === "customer" ? "Заказчик"
    : ticket.audience === "supplier" ? "Поставщик" : "";
  const run = async (command: string) => {
    setBusy(true); setError("");
    try {
      await api.post(`/api/operator/sessions/${ticket.session_id}/actions/${command}`,
        { version: ticket.version, reason: command === "resolve" ? "" : reason, line, operator_id: operatorId || undefined });
      setReason(""); setOperatorId(""); setAction(null); onChanged();
    } catch (e) { setError(errorText(e, "Действие не выполнено")); onChanged(); }
    finally { setBusy(false); }
  };
  return <section className="border-b border-(--color-line) bg-(--color-surface-2) px-3 py-2.5 text-xs" aria-label="Сведения об обращении">
    <div className="flex flex-wrap items-center gap-1.5">
      {audience && (
        <span className="inline-flex items-center gap-1 rounded-full bg-(--color-surface) px-2.5 py-1 text-(--color-ink-soft)">
          <UserRound size={12} /> {audience}
        </span>
      )}
      {topic && <span className="rounded-full bg-(--color-surface) px-2.5 py-1 text-(--color-ink-soft)">{topic}</span>}
      {summary.cause && <span className="rounded-full bg-(--color-brand-soft) px-2.5 py-1 text-(--color-brand-ink)">{summary.cause}</span>}
    </div>
    {summary.question && (
      <p className="mt-2 text-sm leading-snug text-pretty"><span className="font-semibold">Запрос:</span> {summary.question}</p>
    )}
    {ticket.routing?.needs_triage === true && (
      <p className="mt-2 flex items-start gap-1.5 rounded-lg bg-(--color-warn-soft) px-2.5 py-2 text-(--color-warn-ink)">
        <AlertTriangle size={14} className="mt-0.5 shrink-0" />
        Направление определено предварительно. Проверьте его перед ответом.
      </p>
    )}
    {allowed.includes("decline") && <div className="mt-2 flex flex-wrap items-center gap-2">
      <button className="btn btn-primary !min-h-10 !py-2 text-xs" disabled={busy} onClick={() => void run("take")}>Принять обращение</button>
      <button className="btn btn-ghost !min-h-10 !py-2 text-xs" disabled={busy} onClick={() => void run("decline")}>Вернуть в очередь</button>
      <span className="text-(--color-ink-soft)">Назначение действует 60 секунд</span>
    </div>}
    {(allowed.includes("message") || allowed.includes("reassign")) && <>
      <div className="mt-2 flex flex-wrap gap-1.5" aria-label="Действия с обращением">
        {allowed.includes("message") && <>
          <ActionButton active={action === "wait"} icon={<HelpCircle size={13} />} onClick={() => setAction(action === "wait" ? null : "wait")}>Уточнить</ActionButton>
          <ActionButton active={action === "resolve"} icon={<CheckCircle2 size={13} />} onClick={() => setAction(action === "resolve" ? null : "resolve")}>Завершить</ActionButton>
          <ActionButton active={action === "transfer"} icon={<ArrowRightLeft size={13} />} onClick={() => setAction(action === "transfer" ? null : "transfer")}>Передать</ActionButton>
        </>}
        {allowed.includes("reassign") && (
          <ActionButton active={action === "reassign"} icon={<UsersRound size={13} />} onClick={() => setAction(action === "reassign" ? null : "reassign")}>Другому сотруднику</ActionButton>
        )}
      </div>
      {action && (
        <div className="mt-2 flex flex-col gap-2 rounded-lg border border-(--color-line) bg-(--color-surface) p-2.5">
          {action === "transfer" && (
            <select className="input !min-h-10 !rounded-lg !py-2 text-xs" aria-label="Новое направление" value={line} onChange={e => setLine(Number(e.target.value))}>
              {ticket.line !== 1 && <option value={1}>Консультации</option>}
              {ticket.line !== 2 && <option value={2}>Техническая поддержка</option>}
              {ticket.line !== 3 && <option value={3}>Закупки и контракты</option>}
            </select>
          )}
          {action === "reassign" && (
            <select className="input !min-h-10 !rounded-lg !py-2 text-xs" aria-label="Сотрудник для переназначения" value={operatorId} onChange={e => setOperatorId(e.target.value)}>
              <option value="">Выберите сотрудника</option>
              {(staff.data ?? []).map(person => <option key={person.id} value={person.id}>{person.name}</option>)}
            </select>
          )}
          {action === "wait" ? (
            <textarea className="input min-h-16 !rounded-lg text-xs" aria-label="Текст действия"
              placeholder="Что нужно уточнить у пользователя"
              value={reason} onChange={e => setReason(e.target.value)} />
          ) : action === "resolve" ? (
            <p className="rounded-lg bg-(--color-surface-2) px-2.5 py-2 leading-relaxed text-(--color-ink-soft)">
              Пользователь подтвердит последний отправленный ответ: «Помогло» или «Не решено».
            </p>
          ) : (
            <input className="input !min-h-10 !rounded-lg !py-2 text-xs" aria-label="Комментарий к передаче"
              placeholder="Комментарий — необязательно" value={reason} onChange={e => setReason(e.target.value)} />
          )}
          <button className="btn btn-primary !min-h-10 !py-2 text-xs"
            disabled={busy || (action === "wait" && !reason.trim()) || (action === "reassign" && !operatorId)}
            onClick={() => void run(action)}>
            {action === "wait" ? "Отправить вопрос" : action === "resolve" ? "Спросить «Помогло?»" : "Передать"}
          </button>
          {staff.error && action === "reassign" && <p role="alert">Не удалось загрузить сотрудников</p>}
        </div>
      )}
    </>}
    {error && <p role="alert" className="mt-2 rounded-lg bg-(--color-brand-soft) px-2.5 py-2 text-(--color-brand-ink)">{error}</p>}
  </section>;
}

function ActionButton({ active, icon, children, onClick }: {
  active: boolean;
  icon: ReactNode;
  children: ReactNode;
  onClick: () => void;
}) {
  return (
    <button type="button" onClick={onClick}
      className={`inline-flex min-h-10 items-center gap-1.5 rounded-lg border px-2.5 font-medium transition-colors active:scale-[.98] ${
        active
          ? "border-(--color-brand) bg-(--color-brand-soft) text-(--color-brand-ink)"
          : "border-(--color-line) bg-(--color-surface) text-(--color-ink-soft) hover:text-(--color-ink)"
      }`}>
      {icon}{children}
    </button>
  );
}
