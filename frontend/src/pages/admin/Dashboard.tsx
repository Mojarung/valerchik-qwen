import { useState } from "react";
import {
  Area, AreaChart, Bar, BarChart, CartesianGrid, Legend, Line, LineChart,
  ResponsiveContainer, Tooltip, XAxis, YAxis,
} from "recharts";
import { Bot, Download, MessagesSquare, ShieldAlert, Star, TrendingUp } from "lucide-react";
import { api, errorText } from "@/lib/api";
import { useApiData } from "@/lib/useApiData";
import { LoadError } from "@/components/LoadError";
import { useMeta } from "@/lib/store";
import { useCssVars } from "@/lib/useCssVars";
import type { Stats, TopicRow, TopicsResponse } from "@/lib/types";

/** Цвета серий берутся из темы: захардкоженный красный на тёмном фоне давал
 *  2.9:1, а янтарная «эскалации» на белом — 2.6:1. Линии — текстовые токены
 *  (светлее в тёмной теме), столбцы — заливка. */
const CHART_VARS = ["--color-brand", "--color-brand-ink", "--color-warn-ink", "--color-ok-ink"] as const;

export default function Dashboard() {
  const [downloading, setDownloading] = useState<string | null>(null);
  const lines = useMeta((s) => s.lines);
  const colors = useCssVars(CHART_VARS);

  const { data: stats, error: statsError, reload: reloadStats } = useApiData<Stats>("/api/admin/stats");

  /**
   * Выгрузка через fetch, а не ссылкой с ?token=.
   *
   * Раньше админский токен уезжал в query-строку и оседал в access-логах nginx
   * открытым текстом: любой, у кого есть доступ к логам, получал двенадцать
   * часов прав админа. Теперь права подтверждает httpOnly-кука, и её браузер
   * приложит сам — ни в адресе, ни в JS секрета не остаётся.
   */
  const download = async (href: string, label: string) => {
    setDownloading(href);
    try {
      const resp = await fetch(href, { credentials: "same-origin" });
      if (!resp.ok) throw new Error(`Сервер ответил ${resp.status}`);
      const blob = await resp.blob();
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = href.split("/").pop() ?? "export";
      link.click();
      URL.revokeObjectURL(url);
    } catch (e) {
      alert(`${label}: ${e instanceof Error ? e.message : "не удалось скачать"}`);
    } finally {
      setDownloading(null);
    }
  };

  if (!stats) {
    // Без статистики страница не «грузится вечно»: отказ сервера виден и повторяем
    return statsError
      ? <LoadError message={`Статистика не загрузилась: ${statsError}`} onRetry={reloadStats} />
      : <div className="p-8 text-center text-xs text-(--color-ink-soft)">Загрузка…</div>;
  }

  const cards = [
    { label: "Диалогов всего", value: stats.sessions.total, icon: MessagesSquare, hint: `${stats.sessions.by_status.resolved ?? 0} решено` },
    { label: "Автоответы ИИ", value: pct(stats.quality.auto_answer_rate), icon: Bot, hint: `уверенность ${pct(stats.quality.avg_confidence)}` },
    // Два вопроса после обращения — рядом со звёздами: средняя оценка без них не
    // говорит, решили ли проблему и поняли ли объяснение
    { label: "Средняя оценка", value: stats.quality.avg_rating ?? "—", icon: Star,
      hint: `${stats.quality.feedback_count} отзывов · решено ${pct(stats.quality.solved_rate)} · понятно ${pct(stats.quality.clear_rate)}` },
    { label: "Нарушения", value: stats.sessions.by_status.closed_violation ?? 0, icon: ShieldAlert, hint: "закрыто автоматически" },
  ];

  const lineData = Object.entries(stats.sessions.escalated_by_line).map(([line, count]) => ({
    name: lines[line]?.label ? `Л${line} · ${lines[line].label}` : `Линия ${line}`,
    count,
  }));

  return (
    <div className="space-y-3">
      {/* Четыре плитки в ряд — уже с lg, а не с xl: боковое меню появляется
          именно на lg, и в промежутке 1024–1280 карточки растягивались на
          пол-экрана ради одного числа. Графикам ширина нужна, им xl оставлен. */}
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        {cards.map(({ label, value, icon: Icon, hint }) => (
          <div key={label} className="card p-4">
            <div className="mb-1 flex items-center gap-2 text-xs text-(--color-ink-soft)">
              <Icon size={14} className="text-(--color-brand-ink)" /> {label}
            </div>
            <div className="text-2xl font-bold tracking-tight tabular-nums">{value}</div>
            <div className="text-xs text-(--color-ink-soft)">{hint}</div>
          </div>
        ))}
      </div>

      <div className="grid gap-3 xl:grid-cols-2">
        <ChartCard title="Диалоги и эскалации, 14 дней">
          <ResponsiveContainer width="100%" height={220}>
            <AreaChart data={stats.series}>
              <defs>
                <linearGradient id="g1" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor={colors["--color-brand-ink"]} stopOpacity={0.25} />
                  <stop offset="100%" stopColor={colors["--color-brand-ink"]} stopOpacity={0} />
                </linearGradient>
              </defs>
              <CartesianGrid strokeDasharray="3 3" />
              <XAxis dataKey="day" fontSize={12} />
              <YAxis fontSize={12} allowDecimals={false} />
              <Tooltip />
              <Legend wrapperStyle={{ fontSize: 12 }} />
              <Area type="monotone" dataKey="sessions" name="диалоги" stroke={colors["--color-brand-ink"]}
                fill="url(#g1)" strokeWidth={2} />
              <Area type="monotone" dataKey="escalated" name="эскалации" stroke={colors["--color-warn-ink"]}
                fill="none" strokeWidth={2} />
            </AreaChart>
          </ResponsiveContainer>
        </ChartCard>

        <ChartCard title="Средняя оценка по дням">
          <ResponsiveContainer width="100%" height={220}>
            <LineChart data={stats.series}>
              <CartesianGrid strokeDasharray="3 3" />
              <XAxis dataKey="day" fontSize={12} />
              <YAxis domain={[1, 5]} fontSize={12} />
              <Tooltip />
              <Line type="monotone" dataKey="avg_rating" name="оценка" stroke={colors["--color-ok-ink"]}
                strokeWidth={2} connectNulls />
            </LineChart>
          </ResponsiveContainer>
        </ChartCard>
      </div>

      <div className="grid gap-3 xl:grid-cols-2">
        <ChartCard title="Эскалации по линиям поддержки">
          <ResponsiveContainer width="100%" height={200}>
            {/* Линий поддержки три-четыре, и число рядом со столбцом читается
                сразу — в отличие от тултипа, который вдобавок вставал ровно
                поверх своего же столбца и закрывал его. */}
            <BarChart data={lineData} layout="vertical" margin={{ right: 28 }}>
              <XAxis type="number" fontSize={12} allowDecimals={false} />
              <YAxis type="category" dataKey="name" width={180} fontSize={12} />
              <Bar dataKey="count" name="обращений" fill={colors["--color-brand"]} radius={[0, 6, 6, 0]} barSize={18}
                label={{ position: "right", fontSize: 12, fill: "currentColor" }} />
            </BarChart>
          </ResponsiveContainer>
        </ChartCard>

        <div className="card p-4">
          <div className="mb-3 text-xs font-semibold">Выгрузки и база знаний</div>
          <div className="mb-3 grid grid-cols-2 gap-2 text-center">
            <div className="rounded-xl bg-(--color-surface-2) p-3">
              <div className="text-xl font-bold">{stats.kb.active_articles}</div>
              <div className="text-xs text-(--color-ink-soft)">статей активно</div>
            </div>
            <div className="rounded-xl bg-(--color-surface-2) p-3">
              <div className="text-xl font-bold">{stats.kb.open_gaps}</div>
              <div className="text-xs text-(--color-ink-soft)">пробелов открыто</div>
            </div>
          </div>
          <div className="flex flex-wrap gap-2">
            {([
              ["Диалоги CSV", "/api/admin/export/dialogs.csv"],
              ["Отзывы CSV", "/api/admin/export/feedback.csv"],
              ["База знаний CSV", "/api/kb/export.csv"],
              ["База знаний JSON", "/api/kb/export.json"],
            ] as const).map(([label, href]) => (
              <button key={href} className="btn btn-ghost !py-1.5 text-xs"
                disabled={downloading === href}
                onClick={() => void download(href, label)}>
                <Download size={13} /> {label}
              </button>
            ))}
          </div>
          <p className="mt-3 text-xs leading-relaxed text-(--color-ink-soft)">
            Медианная задержка ответа: <b>{stats.quality.median_latency_ms ?? "—"} мс</b>.
            Персональные данные в выгрузках маскируются согласно настройкам guardrails.
          </p>
        </div>
      </div>

      <TopicsCard />
    </div>
  );
}

/**
 * Темы обращений и их рост.
 *
 * Рост стоит первым не для красоты: верхние темы поддержка и так знает наизусть,
 * а ценно то, что выросло за неделю — это либо сломанный релиз портала, либо
 * дыра в документации, и реагировать нужно на неё.
 */
function TopicsCard() {
  const [days, setDays] = useState(7);
  // Пока расчёт идёт в фоне, спрашиваем снова: отчёт по тысячам сообщений
  // считается десятки секунд, и оставить админа с надписью «считаем» навсегда
  // было бы хуже, чем лишний запрос раз в три секунды.
  const { data, error, reload } = useApiData<TopicsResponse>(`/api/admin/topics?days=${days}`, {
    pollMs: 3000,
    pollWhile: (d) => Boolean(d?.computing),
  });
  const [refreshError, setRefreshError] = useState("");

  const refresh = async () => {
    setRefreshError("");
    try {
      await api.post(`/api/admin/topics/refresh?days=${days}`);
      reload();
    } catch (e) {
      setRefreshError(errorText(e, "Пересчёт не запустился"));
    }
  };

  const topics = data?.report?.topics ?? [];
  const rising = [...topics].filter((t) => t.delta > 0).sort((a, b) => b.delta - a.delta).slice(0, 5);
  const frequent = topics.slice(0, 5);

  return (
    <div className="card p-4">
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <TrendingUp size={14} className="text-(--color-brand-ink)" />
        <span className="text-xs font-semibold">Темы обращений</span>
        <div className="flex gap-1 text-xs">
          {[7, 30].map((d) => (
            <button key={d} onClick={() => setDays(d)}
              className={`rounded-full px-2.5 py-1 transition-colors ${days === d ? "bg-(--color-brand) text-white" : "hover:bg-(--color-surface-2)"}`}>
              {d} дней
            </button>
          ))}
        </div>
        <div className="ml-auto flex items-center gap-2 text-xs text-(--color-ink-soft)">
          {data?.report && (
            <span>
              {data.report.messages} вопросов · рассчитано{" "}
              {new Date(data.report.generated_at).toLocaleString("ru", {
                day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit",
              })}
            </span>
          )}
          <button className="font-medium text-(--color-brand-ink) hover:underline disabled:opacity-50"
            disabled={data?.computing}
            onClick={() => void refresh()}>
            Пересчитать
          </button>
        </div>
      </div>

      {error && <LoadError message={`Темы не загрузились: ${error}`} onRetry={reload} />}
      {refreshError && <p role="alert" className="text-xs text-(--color-brand-ink)">{refreshError}</p>}
      {data?.computing && (
        <p className="text-xs text-(--color-ink-soft)">Считаем темы — это занимает до минуты…</p>
      )}
      {!error && !data?.computing && topics.length === 0 && (
        <p className="text-xs text-(--color-ink-soft)">
          За период не набралось тем: нужно хотя бы несколько одинаковых по смыслу вопросов.
        </p>
      )}

      {topics.length > 0 && (
        <div className="grid gap-4 xl:grid-cols-2">
          <TopicList title="Растёт быстрее всего" rows={rising}
            empty="Ничего заметно не выросло — это хорошая новость" showDelta />
          <TopicList title="Спрашивают чаще всего" rows={frequent} empty="" />
        </div>
      )}
    </div>
  );
}

function TopicList({ title, rows, empty, showDelta }: {
  title: string; rows: TopicRow[]; empty: string; showDelta?: boolean;
}) {
  return (
    <div>
      <div className="label">{title}</div>
      {rows.length === 0 && <p className="text-xs text-(--color-ink-soft)">{empty}</p>}
      <div className="space-y-1.5">
        {rows.map((t) => (
          <div key={t.label} className="rounded-xl border border-(--color-line) p-2.5">
            <div className="flex items-start gap-2 text-xs">
              <span className="min-w-0 flex-1 leading-snug">{t.label}</span>
              {showDelta && <GrowthBadge row={t} />}
              <span className="shrink-0 font-bold">{t.count}</span>
            </div>
            <div className="mt-0.5 text-xs text-(--color-ink-soft)">
              было {t.previous} · на оператора ушло {t.escalated}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

function GrowthBadge({ row }: { row: TopicRow }) {
  // Тема без прошлого — «новая», а не «рост в N раз»: делить не на что, и
  // проценты от нуля вводили бы в заблуждение сильнее, чем помогали.
  const isNew = row.previous === 0;
  return (
    <span className={`shrink-0 rounded px-1.5 py-0.5 text-xs font-semibold ${isNew ? "bg-(--color-brand) text-white" : "bg-(--color-warn) text-(--color-on-warn)"}`}>
      {isNew ? "новая" : `×${row.growth}`}
    </span>
  );
}

function ChartCard({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="card p-4">
      <div className="mb-2 text-xs font-semibold">{title}</div>
      {children}
    </div>
  );
}

function pct(v: number | null): string {
  return v === null ? "—" : `${(v * 100).toFixed(0)}%`;
}
