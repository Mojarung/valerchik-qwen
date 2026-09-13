import { useEffect, useState } from "react";
import { Eye, EyeOff, Pencil, Plus, RotateCcw, Route, Search, Sparkles, Trash2 } from "lucide-react";
import { api, errorText, SLOW_TIMEOUT_MS } from "@/lib/api";
import { useApiData } from "@/lib/useApiData";
import { LoadError } from "@/components/LoadError";
import { takeGuideDraft } from "@/lib/guideDraft";
import type { Api, GuideStep } from "@/lib/types";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { Modal } from "@/components/Modal";
import { EMPTY_STEP, GuideEditor, type EditableGuide, type EditableStep } from "./GuideEditor";

/**
 * Сценарии по порталу: список, редактор и проверка подбора.
 *
 * Заводские сценарии лежат файлом в репозитории, правки админов — в БД и
 * накладываются поверх. Отсюда две особенности списка: у строки видно, откуда
 * она взялась, а «Вернуть заводской» удаляет запись, а не сценарий.
 *
 * Черновик, записанный расширением прямо на портале, приезжает сюда в якоре
 * адреса (#draft=…): сервер якорь не видит, а страница уже открыта под правами
 * администратора — сохраняет он сам.
 */

type GuideItem = Api["GuideAdminItem"];

const SOURCE_LABEL: Record<GuideItem["source"], string> = {
  file: "заводской",
  admin: "из админки",
  recorder: "записан на портале",
  llm: "нейросеть",
};

const AUDIENCE_LABEL: Record<string, string> = {
  all: "всем", supplier: "поставщику", customer: "заказчику",
};

const NEW_GUIDE: EditableGuide = {
  id: "", title: "", audience: "all", questions: "", steps: [{ ...EMPTY_STEP }],
  enabled: true, source: "admin",
};

function toEditable(item: GuideItem): EditableGuide {
  return {
    id: item.id,
    title: item.title,
    audience: item.audience,
    questions: (item.questions ?? []).join("\n"),
    enabled: item.enabled,
    source: item.source === "file" ? "admin" : item.source,
    steps: (item.steps ?? []).map(fromStep),
  };
}

function fromStep(step: GuideStep & { go?: string | null }): EditableStep {
  return {
    url: step.url ?? "",
    title: step.title,
    text: step.text ?? "",
    target: step.target?.length ? step.target : [{ css: "" }],
    action: step.action ?? "",
    optional: Boolean(step.optional),
    go: typeof step.go === "string" ? step.go : "",
    noGo: "go" in step && step.go === null,
  };
}

function toPayload(guide: EditableGuide) {
  return {
    id: guide.id,
    title: guide.title,
    audience: guide.audience,
    enabled: guide.enabled,
    source: guide.source,
    questions: guide.questions.split("\n").map((q) => q.trim()).filter(Boolean),
    steps: guide.steps.map((step) => ({
      ...(step.url ? { url: step.url } : {}),
      title: step.title,
      text: step.text,
      target: step.target.filter((l) => l.css || l.text || l.href),
      ...(step.action ? { action: step.action } : {}),
      ...(step.optional ? { optional: true } : {}),
      // три состояния: не задано, «только с предыдущего» (null) и адрес перехода
      ...(step.noGo ? { go: null } : step.go ? { go: step.go } : {}),
    })),
  };
}

/** Черновик, записанный расширением на портале (снят со старта приложения). */
function readDraft(): EditableGuide | null {
  const raw = takeGuideDraft<{ audience?: string; steps?: (GuideStep & { go?: string | null })[] }>();
  if (!raw) return null;
  return {
    ...NEW_GUIDE,
    audience: raw.audience ?? "all",
    source: "recorder",
    steps: (raw.steps ?? []).map(fromStep),
  };
}

export default function GuidesPage() {
  const [editing, setEditing] = useState<EditableGuide | null>(null);
  const [error, setError] = useState("");
  const [actionError, setActionError] = useState("");
  const { data, error: loadError, reload } = useApiData<Api["GuideAdminList"]>("/api/admin/guides");
  const items = data?.items ?? [];
  const [probe, setProbe] = useState("");
  const [probeRole, setProbeRole] = useState("all");
  const [probeResult, setProbeResult] = useState<string | null>(null);
  const [drafting, setDrafting] = useState(false);
  /** Сценарий, для которого показываем подтверждение удаления или отката. */
  const [removing, setRemoving] = useState<GuideItem | null>(null);

  useEffect(() => {
    const draft = readDraft();
    if (draft) setEditing(draft);
  }, []);

  const save = async () => {
    if (!editing) return;
    setError("");
    try {
      await api.put(`/api/admin/guides/${editing.id}`, toPayload(editing));
      setEditing(null);
      reload();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Не удалось сохранить");
    }
  };

  /** Действие со списка: отказ — в сообщение над списком, а не в брошенный промис. */
  const act = async (action: () => Promise<unknown>, failure: string) => {
    setActionError("");
    try {
      await action();
      reload();
    } catch (e) {
      setActionError(errorText(e, failure));
    }
  };

  const remove = (item: GuideItem) =>
    act(() => api.delete(`/api/admin/guides/${item.id}`), "Сценарий не удалось удалить");

  const toggle = (item: GuideItem) =>
    act(() => api.post(`/api/admin/guides/${item.id}/enabled?enabled=${!item.enabled}`),
      "Сценарий не удалось переключить");

  /** Тексты шагов и формулировки вопроса — нейросетью; селекторы не трогаем. */
  const conjure = async () => {
    if (!editing) return;
    setError("");
    setDrafting(true);
    try {
      const payload = toPayload(editing);
      const drafted = await api.post<Api["GuideDraft"]>("/api/admin/guides/draft", {
        goal: editing.title || "сценарий по порталу",
        audience: editing.audience,
        steps: payload.steps,
      }, { timeoutMs: SLOW_TIMEOUT_MS });
      setEditing({
        ...editing,
        title: drafted.title || editing.title,
        questions: drafted.questions.length ? drafted.questions.join("\n") : editing.questions,
        steps: editing.steps.map((step, i) => ({
          ...step,
          title: drafted.steps[i]?.title || step.title,
          text: drafted.steps[i]?.text || step.text,
        })),
        source: drafted.llm ? "llm" : editing.source,
      });
      if (!drafted.llm) setError(drafted.reason);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Не удалось сочинить тексты");
    } finally {
      setDrafting(false);
    }
  };

  const check = async () => {
    const params = new URLSearchParams({ q: probe, audience: probeRole });
    const found = await api.get<Api["GuideMatch"]>(`/api/guides/match?${params}`);
    setProbeResult(found.guide ? `${found.guide.title} (${found.guide.id})` : "");
  };

  return (
    <div className="space-y-3">
      {loadError && <LoadError message={`Сценарии не загрузились: ${loadError}`} onRetry={reload} />}
      {actionError && <p role="alert" className="text-xs text-(--color-brand-ink)">{actionError}</p>}
      <div className="card flex flex-wrap items-center gap-2 p-3">
        <Route size={16} className="text-(--color-brand-ink)" />
        <div className="mr-auto">
          <div className="text-sm font-bold">Сценарии по порталу</div>
          <div className="text-xs text-(--color-ink-soft)">
            Расширение ведёт человека по шагам: подсвечивает элемент и объясняет, что нажать
          </div>
        </div>
        <button className="btn btn-primary text-xs" onClick={() => setEditing({ ...NEW_GUIDE })}>
          <Plus size={14} /> Новый сценарий
        </button>
      </div>

      <div className="card flex flex-wrap items-center gap-2 p-3">
        <Search size={15} className="text-(--color-ink-soft)" />
        <input className="input min-w-52 flex-1 !text-xs" aria-label="Вопрос для проверки сценария"
          placeholder="Проверить: какой сценарий подберётся к вопросу"
          value={probe} onChange={(e) => setProbe(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && void check()} />
        <select className="input w-auto !text-xs" aria-label="Роль собеседника для проверки" value={probeRole}
          onChange={(e) => setProbeRole(e.target.value)}>
          <option value="all">Роль неизвестна</option>
          <option value="supplier">Поставщик</option>
          <option value="customer">Заказчик</option>
        </select>
        <button className="btn btn-ghost text-xs" onClick={() => void check()}>Проверить</button>
        {probeResult !== null && (
          <span className={`text-xs ${probeResult ? "text-(--color-ok-ink)" : "text-(--color-ink-soft)"}`}>
            {probeResult || "ни один не подошёл"}
          </span>
        )}
      </div>

      <div className="card divide-y divide-(--color-line) p-0">
        {items.map((item) => (
          <div key={item.id} className={`flex flex-wrap items-center gap-2 p-3 ${item.enabled ? "" : "opacity-55"}`}>
            <div className="min-w-52 flex-1">
              <div className="text-sm font-semibold">{item.title}</div>
              <div className="mt-0.5 flex flex-wrap items-center gap-1.5 text-xs text-(--color-ink-soft)">
                <span className="font-mono">{item.id}</span>
                <span className="rounded-full bg-(--color-surface-2) px-2 py-0.5">
                  {AUDIENCE_LABEL[item.audience] ?? item.audience}
                </span>
                <span>шагов: {item.steps.length}</span>
                <span className={`rounded-full px-2 py-0.5 ${
                  item.source === "file" ? "bg-(--color-surface-2)" : "bg-(--color-brand-soft) text-(--color-brand-ink)"}`}>
                  {SOURCE_LABEL[item.source]}
                </span>
                {item.overridden && <span>заводской переписан</span>}
              </div>
            </div>
            <button className="btn btn-ghost !p-1.5" title={item.enabled ? "Выключить" : "Включить"}
              onClick={() => void toggle(item)}>
              {item.enabled ? <Eye size={15} /> : <EyeOff size={15} />}
            </button>
            <button className="btn btn-ghost !p-1.5" title="Изменить"
              onClick={() => setEditing(toEditable(item))}>
              <Pencil size={15} />
            </button>
            {item.source !== "file" && (
              <button className="btn btn-ghost !p-1.5 hover:text-(--color-brand-ink)"
                title={item.overridden ? "Вернуть заводской" : "Удалить"}
                onClick={() => setRemoving(item)}>
                {item.overridden ? <RotateCcw size={15} /> : <Trash2 size={15} />}
              </button>
            )}
          </div>
        ))}
        {items.length === 0 && (
          <div className="p-6 text-center text-xs text-(--color-ink-soft)">Сценариев пока нет</div>
        )}
      </div>

      {editing && (
        <Modal title={editing.id ? `Сценарий «${editing.id}»` : "Новый сценарий"}
          onClose={() => setEditing(null)}>
          {editing.source === "recorder" && (
            <p className="mb-3 rounded-xl bg-(--color-brand-soft) px-3 py-2 text-xs text-(--color-brand-ink)">
              Шаги записаны прямо на портале. Проверьте заголовки и добавьте формулировки вопроса.
            </p>
          )}
          <GuideEditor value={editing} onChange={setEditing} />
          {error && <p className="mt-3 text-xs text-(--color-brand-ink)">{error}</p>}
          <div className="mt-4 flex items-center justify-end gap-2">
            <button className="btn btn-ghost mr-auto text-xs" disabled={drafting || !editing.steps.length}
              title="Заголовки, пояснения и формулировки вопроса напишет модель; селекторы останутся вашими"
              onClick={() => void conjure()}>
              <Sparkles size={14} /> {drafting ? "Сочиняю…" : "Сочинить тексты"}
            </button>
            <button className="btn btn-ghost" onClick={() => setEditing(null)}>Отмена</button>
            <button className="btn btn-primary" onClick={() => void save()}
              disabled={!editing.id || !editing.title || !editing.steps.length}>
              Сохранить
            </button>
          </div>
        </Modal>
      )}

      <ConfirmDialog
        open={removing !== null}
        title={removing?.overridden ? "Вернуть заводской сценарий?" : "Удалить сценарий?"}
        text={removing
          ? removing.overridden
            ? `«${removing.title}» вернётся к заводской версии, ваши правки будут потеряны.`
            : `Сценарий «${removing.title}» будет удалён, и расширение перестанет его показывать.`
          : ""}
        confirmLabel={removing?.overridden ? "Вернуть заводской" : "Удалить"}
        danger
        onCancel={() => setRemoving(null)}
        onConfirm={() => {
          const item = removing;
          setRemoving(null);
          if (item) void remove(item);
        }}
      />
    </div>
  );
}
