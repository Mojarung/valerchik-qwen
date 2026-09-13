import { useState } from "react";
import { AlertTriangle, Bot, RefreshCcw, UserRound } from "lucide-react";
import { api, errorText } from "@/lib/api";
import type { Api } from "@/lib/types";
import { useApiData } from "@/lib/useApiData";
import { LoadError } from "@/components/LoadError";
import { plural } from "@/lib/plural";
import { AnswerFeedbackQueue } from "./AnswerFeedbackQueue";

type Report = Api["FeedbackReport"];
type Specialist = Api["SpecialistScore"];

const PROBLEM_KINDS: Record<string, string> = {
  specialist: "специалист",
  topic: "тема",
  article: "статья",
  trend: "динамика",
  complaint: "жалоба",
};

const pct = (value: number | null | undefined) =>
  value === null || value === undefined ? "—" : `${Math.round(value * 100)}%`;

export default function FeedbackPage() {
  const summary = useApiData<Report>("/api/admin/feedback/report");
  const report = summary.data;
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState("");

  const regenerate = async () => {
    setBusy(true);
    setActionError("");
    try {
      await api.post("/api/admin/feedback/report");
      summary.reload();
    } catch (e) {
      setActionError(errorText(e, "Отчёт не удалось поставить в очередь"));
    } finally {
      setBusy(false);
    }
  };

  const s = report?.summary;
  return (
    <div className="space-y-3 pb-3">
      <section className="card overflow-hidden">
        <div className="flex flex-wrap items-start justify-between gap-3 p-5">
          <div>
            <div className="text-xs font-semibold uppercase tracking-wide text-(--color-brand-ink)">Качество поддержки</div>
            <h2 className="mt-1 text-xl font-bold tracking-tight">Состояние и задачи</h2>
            {report?.created_at && (
              <p className="mt-1 text-xs text-(--color-ink-soft)">
                Данные обновлены {new Date(report.created_at).toLocaleString("ru")}
              </p>
            )}
          </div>
          <button className="btn btn-ghost !min-h-0 !px-3 !py-2 text-xs" onClick={() => void regenerate()} disabled={busy}>
            <RefreshCcw size={13} className={busy ? "animate-spin" : ""} />
            {report?.exists ? "Обновить данные" : "Сформировать отчёт"}
          </button>
        </div>

        {report?.exists && s && (
          <div className="grid grid-cols-2 gap-px border-t border-(--color-line) bg-(--color-line) xl:grid-cols-4">
            <SummaryMetric label="Отзывов" value={s.count}
              hint={s.avg_rating === null ? "" : `средняя ${s.avg_rating} из 5`} />
            <SummaryMetric label="Довольны" value={pct(s.positive_share)} hint="оценки 4–5" />
            <SummaryMetric label="Проблема решена" value={pct(s.solved_rate)} hint={answered(s.solved_total)} />
            <SummaryMetric label="Понятно объяснили" value={pct(s.clear_rate)} hint={answered(s.clear_total)} />
          </div>
        )}
      </section>

      {summary.error && <LoadError message={`Отзывы не загрузились: ${summary.error}`} onRetry={summary.reload} />}
      {actionError && <p role="alert" className="text-xs text-(--color-brand-ink)">{actionError}</p>}

      {report?.exists && (
        <div className="grid items-start gap-3 xl:grid-cols-2">
          <FeedbackProblems problems={report.problems ?? []} />
          <div className="min-w-0 space-y-3">
            <AnswerFeedbackQueue />
            <SpecialistsTable specialists={report.specialists ?? []} />
          </div>
        </div>
      )}

      {!report?.exists && <AnswerFeedbackQueue />}

      {report && !report.exists && (
        <div className="card p-6 text-center text-xs text-(--color-ink-soft)">
          Оценок консультаций пока нет. После первых оценок здесь появятся показатели и задачи для разбора.
        </div>
      )}

      {report?.support_export && (
        <p className="px-1 text-xs text-(--color-ink-soft) tabular-nums">
          Основа отчёта: {s?.count ?? 0} {plural(s?.count ?? 0, ["отзыв", "отзыва", "отзывов"])} и{" "}
          {report.support_export.total.toLocaleString("ru")} обращений СТП
          {report.support_export.brush_off_share !== null && report.support_export.brush_off_share !== undefined
            ? ` · без объяснения ${pct(report.support_export.brush_off_share)}` : ""}.
        </p>
      )}
    </div>
  );
}

function answered(total: number | undefined) {
  return total ? `ответили ${total} ${plural(total, ["человек", "человека", "человек"])}` : "вопрос ещё не задавали";
}

function SummaryMetric({ label, value, hint = "" }: { label: string; value: string | number; hint?: string }) {
  return (
    <div className="bg-(--color-surface) px-4 py-3.5">
      <div className="text-xs text-(--color-ink-soft)">{label}</div>
      <div className="text-2xl font-bold tracking-tight tabular-nums">{value}</div>
      {hint && <div className="mt-0.5 text-xs text-(--color-ink-soft)">{hint}</div>}
    </div>
  );
}

function IndexCell({ specialist }: { specialist: Specialist }) {
  const index = specialist.index ?? null;
  if (index === null) return <span className="text-(--color-ink-soft)">—</span>;
  const tone = index < 60 ? "bg-(--color-brand)" : index >= 85 ? "bg-(--color-ok)" : "bg-(--color-warn)";
  return (
    <div className="min-w-24">
      <div className="flex items-center gap-2">
        <b className="tabular-nums">{index}</b>
        <div className="h-1.5 min-w-12 flex-1 overflow-hidden rounded-full bg-(--color-line)" aria-hidden>
          <div className={`h-full ${tone}`} style={{ width: `${index}%` }} />
        </div>
      </div>
      {specialist.low_data && <div className="text-xs text-(--color-ink-soft)">мало данных</div>}
    </div>
  );
}

function SpecialistsTable({ specialists }: { specialists: Specialist[] }) {
  const visible = specialists.slice(0, 5);
  return (
    <section className="card overflow-hidden" aria-labelledby="specialists-title">
      <div className="flex items-center justify-between gap-3 px-4 pt-3 pb-1">
        <h3 id="specialists-title" className="text-xs font-bold">Команда: сначала те, кому нужна помощь</h3>
        <span className="text-xs text-(--color-ink-soft)">{specialists.length}</span>
      </div>
      {visible.length === 0 ? (
        <p className="px-4 py-4 text-xs text-(--color-ink-soft)">Пока недостаточно данных по специалистам.</p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-left text-xs">
            <thead>
              <tr className="border-b border-(--color-line) text-(--color-ink-soft)">
                <th className="px-4 py-2">Специалист</th>
                <th className="px-2 py-2">Индекс</th>
                <th className="px-2 py-2">Главное</th>
                <th className="px-4 py-2 text-right">Отзывов</th>
              </tr>
            </thead>
            <tbody>
              {visible.map((specialist) => (
                <tr key={specialist.id} className="border-b border-(--color-line) last:border-0">
                  <td className="px-4 py-2.5">
                    <div className="flex items-center gap-2 font-semibold whitespace-nowrap">
                      {specialist.kind === "ai"
                        ? <Bot size={14} className="text-(--color-brand-ink)" />
                        : <UserRound size={14} className="text-(--color-ink-soft)" />}
                      {specialist.name}
                    </div>
                  </td>
                  <td className="px-2 py-2.5"><IndexCell specialist={specialist} /></td>
                  <td className="max-w-lg px-2 py-2.5 text-(--color-ink-soft)">
                    {specialist.weak_spots?.[0]?.text
                      ?? (specialist.kind === "ai" && specialist.auto_resolved_rate !== null && specialist.auto_resolved_rate !== undefined
                        ? `Без оператора ${pct(specialist.auto_resolved_rate)}` : "Явных проблем нет")}
                  </td>
                  <td className="px-4 py-2.5 text-right tabular-nums">{specialist.count}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {specialists.length > visible.length && (
        <p className="border-t border-(--color-line) px-4 py-2.5 text-xs text-(--color-ink-soft)">
          Ещё {specialists.length - visible.length} — без первоочередных задач.
        </p>
      )}
    </section>
  );
}

function FeedbackProblems({ problems }: { problems: Api["SystemProblem"][] }) {
  const visible = problems.slice(0, 3);
  return (
    <section className="card p-4" aria-labelledby="attention-title">
      <div className="mb-2 flex flex-wrap items-center gap-2 text-xs font-bold">
        <AlertTriangle size={14} className="text-(--color-warn-ink)" />
        <h3 id="attention-title">Требуют внимания</h3>
        {problems.length > 0 && (
          <span className="rounded-full bg-(--color-warn-soft) px-2 py-0.5 text-(--color-warn-ink) tabular-nums">
            {problems.length}
          </span>
        )}
      </div>
      {problems.length === 0 && <p className="text-xs text-(--color-ink-soft)">Явных проблем не видно.</p>}
      <div className="divide-y divide-(--color-line)">
        {visible.map((problem, index) => (
          <div key={`${problem.kind}-${problem.title}-${index}`} className="py-2.5 first:pt-1 last:pb-1">
            <div className="flex flex-wrap items-center gap-1.5">
              {PROBLEM_KINDS[problem.kind] && (
                <span className="rounded-full bg-(--color-surface-2) px-2 py-0.5 text-xs text-(--color-ink-soft)">
                  {PROBLEM_KINDS[problem.kind]}
                </span>
              )}
              <span className="text-xs font-bold">{problem.title}</span>
            </div>
            <p className="mt-1 line-clamp-2 text-xs leading-relaxed">{problem.detail}</p>
            {problem.advice && (
              <p className="mt-1 line-clamp-2 text-xs text-(--color-ink-soft)">Действие: {problem.advice}</p>
            )}
          </div>
        ))}
      </div>
      {problems.length > visible.length && (
        <p className="mt-2 text-xs text-(--color-ink-soft)">Показаны 3 главных из {problems.length}.</p>
      )}
    </section>
  );
}
