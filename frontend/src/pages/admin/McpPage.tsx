import { useState } from "react";
import {
  ChevronDown, CircleCheck, CircleX, Plug, Plus, RefreshCw, Trash2, Wrench,
} from "lucide-react";
import { api, errorText, SLOW_TIMEOUT_MS } from "@/lib/api";
import { useApiData } from "@/lib/useApiData";
import { LoadError } from "@/components/LoadError";
import type { Api } from "@/lib/types";
import { Modal } from "@/components/Modal";


type McpServer = Api["McpServerOut"];

const EMPTY = { name: "", url: "", description: "", headers: "", timeout_s: 20 };

function pluralTools(n: number): string {
  const tail = n % 100 >= 11 && n % 100 <= 14 ? 0 : n % 10;
  if (tail === 1) return "инструмент";
  if (tail >= 2 && tail <= 4) return "инструмента";
  return "инструментов";
}

export default function McpPage() {
  const [adding, setAdding] = useState(false);
  const [form, setForm] = useState(EMPTY);
  const [busy, setBusy] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<string | null>(null);
  const [error, setError] = useState("");
  const [actionError, setActionError] = useState("");
  const { data, error: loadError, reload } = useApiData<McpServer[]>("/api/admin/mcp");
  const servers = data ?? [];

  const create = async () => {
    setError("");
    let headers: Record<string, string> = {};
    if (form.headers.trim()) {
      try {
        headers = JSON.parse(form.headers);
      } catch {
        setError("Заголовки должны быть JSON-объектом, например {\"Authorization\": \"Bearer …\"}");
        return;
      }
    }
    try {
      await api.post("/api/admin/mcp", {
        name: form.name.trim(), url: form.url.trim(), description: form.description.trim(),
        headers, timeout_s: form.timeout_s, enabled: true,
        // Сервер сразу проверяет подключение к новому MCP — это дольше обычного
      }, { timeoutMs: SLOW_TIMEOUT_MS });
      setAdding(false);
      setForm(EMPTY);
      reload();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Не удалось добавить сервер");
    }
  };

  /** Действие над сервером из списка: занятость на кнопке, отказ — в сообщение над списком. */
  const act = async (server: McpServer, action: () => Promise<unknown>, failure: string) => {
    setBusy(server.id);
    setActionError("");
    try {
      await action();
      reload();
    } catch (e) {
      setActionError(errorText(e, failure));
    } finally {
      setBusy(null);
    }
  };

  const toggle = (server: McpServer) =>
    act(server, () => api.patch(`/api/admin/mcp/${server.id}`, { enabled: !server.enabled }),
      "Сервер не удалось переключить");
  const check = (server: McpServer) =>
    act(server, () => api.post(`/api/admin/mcp/${server.id}/check`, undefined, { timeoutMs: SLOW_TIMEOUT_MS }),
      "Проверка подключения не удалась");
  const remove = (server: McpServer) =>
    act(server, () => api.delete(`/api/admin/mcp/${server.id}`), "Сервер не удалось удалить");

  return (
    <div className="space-y-3">
      {loadError && <LoadError message={`Реестр MCP-серверов не загрузился: ${loadError}`} onRetry={reload} />}
      {actionError && <p role="alert" className="text-xs text-(--color-brand-ink)">{actionError}</p>}
      <div className="card flex items-center gap-2 border-l-4 border-l-(--color-brand) px-4 py-3">
        <Plug size={15} className="shrink-0 text-(--color-brand-ink)" />
        <p className="text-xs leading-relaxed">
          Подключите <b>MCP-серверы</b> — их инструменты агент увидит наравне с поиском по базе
          знаний и сможет вызывать в диалоге. Тумблер включает и отключает источник на лету,
          без перезапуска. Недоступный сервер не мешает работе: он молча выпадает из набора,
          пользователь получает обычный ответ по базе знаний.
        </p>
      </div>

      <div className="flex justify-end">
        <button className="btn btn-primary !py-1.5 text-xs" onClick={() => setAdding(true)}>
          <Plus size={13} /> Подключить сервер
        </button>
      </div>

      <div className="card divide-y divide-(--color-line)">
        {servers.length === 0 && (
          <div className="p-6 text-center text-xs text-(--color-ink-soft)">
            Нет подключённых MCP-серверов
          </div>
        )}
        {servers.map((s) => (
          <div key={s.id} className="px-4 py-3">
            <div className="flex flex-wrap items-center gap-2">
              {/* тумблер: единственное действие, меняющее поведение агента мгновенно */}
              <button
                className={`relative h-5 w-9 shrink-0 rounded-full transition-colors ${
                  s.enabled ? "bg-(--color-brand)" : "bg-(--color-line)"}`}
                disabled={busy === s.id}
                title={s.enabled ? "Отключить" : "Включить"}
                aria-label={`${s.enabled ? "Отключить" : "Включить"} сервер ${s.name}`}
                aria-pressed={s.enabled}
                onClick={() => void toggle(s)}>
                <span className={`absolute top-0.5 size-4 rounded-full bg-white transition-all ${
                  s.enabled ? "left-4.5" : "left-0.5"}`} />
              </button>
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-1.5 text-xs font-medium">
                  {s.name}
                  {s.last_status === "ok" && <CircleCheck size={12} className="text-(--color-ok-ink)" />}
                  {s.last_status === "error" && <CircleX size={12} className="text-(--color-brand-ink)" />}
                </div>
                <div className="truncate text-xs text-(--color-ink-soft)">
                  {s.url}
                  {s.checked_at && ` · проверен ${new Date(s.checked_at).toLocaleString("ru")}`}
                </div>
              </div>
              {s.tools.length > 0 && (
                <button
                  className="flex shrink-0 cursor-pointer items-center gap-1 rounded-full border
                             border-(--color-brand-soft) bg-(--color-brand-soft) px-2.5 py-1
                             text-xs font-bold text-(--color-brand-ink) transition-colors
                             hover:border-(--color-brand) hover:brightness-95"
                  aria-expanded={expanded === s.id}
                  title={expanded === s.id ? "Скрыть инструменты" : "Показать инструменты"}
                  onClick={() => setExpanded(expanded === s.id ? null : s.id)}>
                  <Wrench size={11} />
                  {s.tools.length} {pluralTools(s.tools.length)}
                  <ChevronDown size={11}
                    className={`transition-transform ${expanded === s.id ? "rotate-180" : ""}`} />
                </button>
              )}
              <button className="btn btn-ghost !p-2" title="Проверить подключение"
                disabled={busy === s.id} onClick={() => void check(s)}>
                <RefreshCw size={13} className={busy === s.id ? "animate-spin" : ""} />
              </button>
              <button className="btn btn-ghost !p-2" title="Удалить"
                disabled={busy === s.id} onClick={() => void remove(s)}>
                <Trash2 size={13} />
              </button>
            </div>

            {s.last_status === "error" && s.last_error && (
              <div className="mt-2 rounded-lg bg-(--color-surface-2) px-3 py-1.5 text-xs text-(--color-ink-soft)">
                Не подключается: {s.last_error}
              </div>
            )}
            {expanded === s.id && (
              <div className="mt-2 space-y-1 rounded-lg bg-(--color-surface-2) px-3 py-2">
                {s.tools.map((t) => (
                  <div key={t.name} className="text-xs">
                    <b>{t.name}</b>
                    {t.description && <span className="text-(--color-ink-soft)"> — {t.description}</span>}
                  </div>
                ))}
              </div>
            )}
          </div>
        ))}
      </div>

      {adding && (
        <Modal title="Подключить MCP-сервер" onClose={() => { setAdding(false); setError(""); }}>
          {error && (
            <div className="mb-3 rounded-xl border-l-4 border-l-(--color-brand) bg-(--color-surface-2) px-3 py-2 text-xs">
              {error}
            </div>
          )}
          <span className="label">Название</span>
          <input aria-label="Название" className="input mb-3" value={form.name} placeholder="например, zakupki-registry"
            onChange={(e) => setForm({ ...form, name: e.target.value })} />
          <span className="label">Адрес</span>
          <input aria-label="Адрес" className="input mb-1" value={form.url} placeholder="https://example.com/mcp"
            onChange={(e) => setForm({ ...form, url: e.target.value })} />
          <p className="mb-3 text-xs text-(--color-ink-soft)">
            http(s)-эндпоинт MCP или путь к stdio-скрипту сервера
          </p>
          <span className="label">Описание</span>
          <input aria-label="Описание" className="input mb-3" value={form.description} placeholder="что даёт этот сервер"
            onChange={(e) => setForm({ ...form, description: e.target.value })} />
          <div className="mb-4 grid grid-cols-2 gap-3">
            <div>
              <span className="label">Заголовки (JSON)</span>
              <input aria-label="Заголовки (JSON)" className="input" value={form.headers} placeholder='{"Authorization": "Bearer …"}'
                onChange={(e) => setForm({ ...form, headers: e.target.value })} />
            </div>
            <div>
              <span className="label">Таймаут, с</span>
              <input aria-label="Таймаут, с" className="input" type="number" min={1} max={120} value={form.timeout_s}
                onChange={(e) => setForm({ ...form, timeout_s: Number(e.target.value) })} />
            </div>
          </div>
          <div className="flex justify-end gap-2">
            <button className="btn btn-ghost" onClick={() => { setAdding(false); setError(""); }}>
              Отмена
            </button>
            <button className="btn btn-primary" onClick={() => void create()}
              disabled={form.name.trim().length < 1 || form.url.trim().length < 4}>
              Подключить и проверить
            </button>
          </div>
        </Modal>
      )}
    </div>
  );
}
