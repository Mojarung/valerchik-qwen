import { useState } from "react";
import { Loader2, Play, TargetIcon, TriangleAlert } from "lucide-react";
import { api, errorText } from "@/lib/api";
import type { EvalResponse } from "@/lib/types";
import { useApiData } from "@/lib/useApiData";
import { LoadError } from "@/components/LoadError";

/**
 * Авто-регресс качества по золотому набору.
 *
 * Страница отвечает на один вопрос: стало лучше или хуже после последней правки
 * промпта, порогов или базы знаний. Поэтому каждая метрика показана вместе с
 * изменением к прошлому прогону — абсолютные числа сами по себе ни о чём не
 * говорят, а «точность упала на 8 пунктов» говорит всё.
 */
export default function QualityPage() {
  // Пока прогон идёт — переспрашиваем: полсотни вопросов через настоящую модель
  // это минуты, и статичная страница выглядела бы зависшей.
  const { data, error, reload } = useApiData<EvalResponse>("/api/admin/eval", {
    pollMs: 2000,
    pollWhile: (d) => Boolean(d?.running),
  });
  const [runError, setRunError] = useState("");

  const run = async () => {
    setRunError("");
    try {
      await api.post("/api/admin/eval/run");
      reload();
    } catch (e) {
      setRunError(errorText(e, "Прогон не запустился"));
    }
  };

  const report = data?.report ?? null;
  const previous = data?.previous ?? null;

  return (
    <div className="space-y-3">
      <div className="card flex flex-wrap items-center gap-3 p-4">
        <TargetIcon size={16} className="text-(--color-brand-ink)" />
        <div className="mr-auto">
          <div className="text-sm font-semibold">Регресс качества</div>
          <div className="text-xs text-(--color-ink-soft)">
            {data?.questions ?? 0} эталонных вопросов
            {report && ` · прогон ${new Date(report.generated_at).toLocaleString("ru")}`}
          </div>
        </div>
        {data?.running ? (
          <span className="flex items-center gap-2 text-xs text-(--color-ink-soft)">
            <Loader2 size={14} className="animate-spin" />
            {data.progress ? `${data.progress.done} из ${data.progress.total}` : "Идёт прогон…"}
          </span>
        ) : (
          <button className="btn btn-primary !py-2 text-xs"
            onClick={() => void run()}>
            <Play size={13} /> Прогнать набор
          </button>
        )}
      </div>

      {error && <LoadError message={`Отчёт не загрузился: ${error}`} onRetry={reload} />}
      {runError && <div role="alert" className="card p-4 text-xs text-(--color-brand-ink)">{runError}</div>}

      {!report && !data?.running && (
        <div className="card p-6 text-center text-xs text-(--color-ink-soft)">
          Прогонов ещё не было. Запустите набор — он покажет, на каких темах бот отвечает
          мимо, и станет точкой отсчёта для следующих правок.
        </div>
      )}

      {report && (
        <>
          <div className="grid grid-cols-2 gap-3 xl:grid-cols-4">
            <Metric label="Точность" value={report.metrics.accuracy}
              previous={previous?.metrics.accuracy} percent
              hint="доля вопросов, где ответ содержит ожидаемое" />
            <Metric label="Отвечает" value={report.metrics.answer_rate}
              previous={previous?.metrics.answer_rate} percent
              hint="доля вопросов, где бот вообще дал ответ" />
            <Metric label="Уверенные ошибки" value={report.metrics.confident_wrong_rate}
              previous={previous?.metrics.confident_wrong_rate} percent lowerIsBetter
              hint="ответил уверенно и мимо — опаснее, чем «не знаю»" />
            <Metric label="Медиана ответа"
              value={report.metrics.median_latency_ms}
              previous={previous?.metrics.median_latency_ms ?? undefined}
              lowerIsBetter suffix=" мс" hint="время до готового ответа" />
          </div>

          <div className="grid gap-3 xl:grid-cols-2">
            <div className="card p-4">
              <div className="mb-2 text-xs font-semibold">Слабые темы</div>
              <div className="space-y-1.5">
                {report.by_topic.map((t) => (
                  <div key={t.topic} className="flex items-center gap-2 text-xs">
                    <span className="min-w-0 flex-1 truncate">{t.topic}</span>
                    <div className="h-1.5 w-24 overflow-hidden rounded-full bg-(--color-surface-2)">
                      <div className="h-full rounded-full bg-(--color-brand)"
                        style={{ width: `${(t.hits / t.total) * 100}%` }} />
                    </div>
                    <span className="w-12 shrink-0 text-right text-(--color-ink-soft)">
                      {t.hits}/{t.total}
                    </span>
                  </div>
                ))}
              </div>
            </div>

            <div className="card p-4">
              <div className="mb-2 flex items-center gap-1.5 text-xs font-semibold">
                Промахи
                <span className="font-normal text-(--color-ink-soft)">
                  — с ними идут править базу знаний
                </span>
              </div>
              <div className="max-h-96 space-y-2 overflow-y-auto">
                {report.misses.length === 0 && (
                  <p className="text-xs text-(--color-ink-soft)">Промахов нет.</p>
                )}
                {report.misses.map((m) => (
                  <div key={m.question} className="rounded-xl border border-(--color-line) p-2.5">
                    <div className="flex items-start gap-1.5 text-xs font-medium">
                      {m.confident_wrong && (
                        <TriangleAlert size={13}
                          className="mt-0.5 shrink-0 text-(--color-brand-ink)"
                          aria-label="Ответил уверенно и мимо" />
                      )}
                      <span className="min-w-0">{m.question}</span>
                    </div>
                    <div className="mt-1 line-clamp-2 text-xs text-(--color-ink-soft)">
                      {m.answer || "— ответа не было"}
                    </div>
                  </div>
                ))}
              </div>
            </div>
          </div>
        </>
      )}
    </div>
  );
}

function Metric({ label, value, previous, percent, suffix = "", lowerIsBetter, hint }: {
  label: string; value: number | null; previous?: number | null; percent?: boolean;
  suffix?: string; lowerIsBetter?: boolean; hint: string;
}) {
  // Пустой прогон метрик не даёт: прочерк честнее нуля, который читается как «всё плохо»
  const show = (v: number | null) => (v === null ? "—" : percent ? `${(v * 100).toFixed(0)}%` : `${v}${suffix}`);
  const delta = typeof previous === "number" && value !== null ? value - previous : null;
  // Нулевую разницу не подсвечиваем: «не изменилось» — это не новость, а шум,
  // из-за которого настоящие изменения теряются среди зелёных и красных значков.
  const better = delta === null || delta === 0 ? null : (delta > 0) !== Boolean(lowerIsBetter);
  return (
    <div className="card p-4">
      <div className="mb-1 text-xs text-(--color-ink-soft)">{label}</div>
      <div className="flex items-baseline gap-2">
        <span className="text-2xl font-bold tracking-tight tabular-nums">{show(value)}</span>
        {better !== null && (
          <span className={`text-xs font-semibold ${better ? "text-(--color-ok-ink)" : "text-(--color-brand-ink)"}`}>
            {delta! > 0 ? "+" : "−"}{show(Math.abs(delta!)).replace("-", "")}
          </span>
        )}
      </div>
      <div className="text-xs leading-snug text-(--color-ink-soft)">{hint}</div>
    </div>
  );
}
