import { useId, useMemo, useState } from "react";
import { Save, Zap } from "lucide-react";
import { api, errorText } from "@/lib/api";
import type { Api, SettingEntry } from "@/lib/types";
import { useToast } from "@/lib/useToast";
import { useApiData } from "@/lib/useApiData";
import { LoadError } from "@/components/LoadError";

const CATEGORY_TITLES: Record<string, string> = {
  chat: "Чат",
  agent: "ReAct-агент",
  llm: "LLM (OpenAI-совместимый API)",
  guardrails: "Guardrails",
  retrieval: "Поиск (RAG)",
  routing: "Линии поддержки",
  rate_limit: "Rate limit",
  voice: "Голос",
  feedback: "Обратная связь",
  operator: "Операторы и очередь",
  backup: "Резервные копии",
  source_conflicts: "Проверка противоречий",
};

export default function SettingsPage() {
  const [draft, setDraft] = useState<Record<string, unknown>>({});
  const { toast, show: notify } = useToast();
  const { data, error, reload } = useApiData<SettingEntry[]>("/api/admin/settings");
  const settings = useMemo(() => data ?? [], [data]);

  const grouped = useMemo(() => {
    const g: Record<string, SettingEntry[]> = {};
    for (const s of settings) (g[s.category] ??= []).push(s);
    return g;
  }, [settings]);

  const dirtyKeys = Object.keys(draft);

  const save = async () => {
    try {
      const res = await api.put<Api["SettingsSaved"]>("/api/admin/settings", { updates: draft });
      notify(`Применено: ${res.changed.length} настроек. ${res.note}.`);
      setDraft({});
      reload();
    } catch (e) {
      // Черновик остаётся: администратор поправит одно значение, а не вобьёт всё заново
      notify(errorText(e, "Настройки не сохранились"), 8000);
    }
  };

  return (
    <div className="space-y-3 pb-20">
      {error && <LoadError message={`Настройки не загрузились: ${error}`} onRetry={reload} />}
      <div className="card flex items-center gap-2 border-l-4 border-l-(--color-brand) px-4 py-3">
        <Zap size={15} className="shrink-0 text-(--color-brand-ink)" />
        <p className="text-xs leading-relaxed">
          Настройки применяются <b>на лету на всех инстансах</b> — без перезапуска сервиса
          (БД + Redis pub/sub). Можно менять промпт агента, пороги, лимиты и даже LLM прямо во время работы.
        </p>
      </div>

      {toast && (
        <div className="card animate-(--animate-fade-up) sticky top-2 z-10 border-l-4 border-l-(--color-ok) px-3 py-2 text-xs">
          {toast}
        </div>
      )}

      {Object.entries(grouped).map(([category, entries]) => (
        <div key={category} className="card p-4">
          <h3 className="mb-3 text-xs font-bold tracking-wide uppercase">
            {CATEGORY_TITLES[category] ?? category}
          </h3>
          <div className="space-y-4">
            {entries.map((entry) => (
              <SettingField key={entry.key} entry={entry}
                value={entry.key in draft ? draft[entry.key] : entry.value}
                dirty={entry.key in draft}
                onChange={(v) => setDraft((d) => ({ ...d, [entry.key]: v }))} />
            ))}
          </div>
        </div>
      ))}

      {dirtyKeys.length > 0 && (
        <div className="fixed right-4 bottom-4 left-4 z-20 mx-auto max-w-md">
          <button className="btn btn-primary animate-(--animate-pop) w-full !whitespace-normal shadow-lg"
            onClick={() => void save()}>
            <Save size={15} /> Применить {dirtyKeys.length} изменений без перезапуска
          </button>
        </div>
      )}
    </div>
  );
}

function SettingField({ entry, value, dirty, onChange }: {
  entry: SettingEntry; value: unknown; dirty: boolean; onChange: (v: unknown) => void;
}) {
  const id = useId();
  const border = dirty ? "border-l-2 border-l-(--color-warn) pl-3" : "";
  // Подпись поля — название настройки, а не её ключ: страницу открывает
  // руководитель службы поддержки. Ключ остаётся рядом мелким моноширинным
  // текстом — по нему настройку ищут в логах и в документации.
  const label = (
    <div className="mb-1">
      <label className="block text-sm font-semibold" htmlFor={id}>{entry.label}</label>
      <div className="mt-0.5 flex flex-wrap items-baseline gap-2 text-xs text-(--color-ink-soft)">
        <code className="rounded bg-(--color-surface-2) px-1 py-0.5">{entry.key}</code>
        <span>{entry.description}</span>
      </div>
    </div>
  );

  if (typeof value === "boolean") {
    return (
      <div className={border}>
        {label}
        <button
          id={id}
          type="button"
          className={`relative h-6 w-11 cursor-pointer rounded-full transition-colors focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-(--color-brand) ${value ? "bg-(--color-brand)" : "bg-(--color-line)"}`}
          onClick={() => onChange(!value)} role="switch" aria-checked={value}
          aria-label={entry.label}>
          <span className={`absolute top-0.5 size-5 rounded-full bg-white shadow transition-all ${value ? "left-5.5" : "left-0.5"}`} />
        </button>
      </div>
    );
  }
  if (typeof value === "number") {
    return (
      <div className={border}>
        {label}
        <input id={id} type="number" step="any" className="input max-w-40" value={value}
          onChange={(e) => onChange(Number(e.target.value))} />
      </div>
    );
  }
  if (Array.isArray(value)) {
    return (
      <div className={border}>
        {label}
        <textarea id={id} className="input resize-none font-mono !text-xs" rows={Math.min(5, value.length + 1)}
          value={value.join("\n")} placeholder="по одному значению на строку"
          onChange={(e) => onChange(e.target.value.split("\n").map((s) => s.trim()).filter(Boolean))} />
      </div>
    );
  }
  if (typeof value === "object" && value !== null) {
    return <JsonField id={id} border={border} label={label} value={value} onChange={onChange} />;
  }
  const str = String(value ?? "");
  // Секрет сервер отдаёт маской «***». Поле пароля, а не текста: иначе маска
  // выглядит как настоящее значение, и её случайно сохраняют поверх ключа.
  if (entry.secret) {
    return (
      <div className={border}>
        {label}
        <input id={id} type="password" className="input" value={str} autoComplete="off"
          placeholder="значение скрыто — введите новое, чтобы заменить"
          onChange={(e) => onChange(e.target.value)} />
      </div>
    );
  }
  return (
    <div className={border}>
      {label}
      {str.length > 60 ? (
        <textarea id={id} className="input resize-y" rows={3} value={str}
          onChange={(e) => onChange(e.target.value)} />
      ) : (
        <input id={id} className="input" value={str} onChange={(e) => onChange(e.target.value)} />
      )}
    </div>
  );
}

function JsonField({ id, border, label, value, onChange }: {
  id: string; border: string; label: React.ReactNode; value: object; onChange: (v: unknown) => void;
}) {
  const [text, setText] = useState(() => JSON.stringify(value, null, 2));
  const [invalid, setInvalid] = useState(false);
  const errorId = `${id}-error`;
  return (
    <div className={border}>
      {label}
      <textarea
        id={id}
        aria-invalid={invalid}
        aria-describedby={invalid ? errorId : undefined}
        className={`input resize-y font-mono !text-xs ${invalid ? "!border-(--color-brand)" : ""}`}
        rows={6} value={text}
        onChange={(e) => {
          setText(e.target.value);
          try {
            onChange(JSON.parse(e.target.value));
            setInvalid(false);
          } catch {
            setInvalid(true);
          }
        }} />
      {invalid && (
        <div id={errorId} role="alert" className="mt-1 text-xs text-(--color-brand-ink)">
          Невалидный JSON — не будет сохранён
        </div>
      )}
    </div>
  );
}
