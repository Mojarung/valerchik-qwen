import { useState } from "react";
import { CircleHelp, Sparkles, X } from "lucide-react";
import { api, errorText } from "@/lib/api";
import type { Api } from "@/lib/types";
import { LoadError } from "@/components/LoadError";
import { Modal } from "@/components/Modal";
import { useApiData } from "@/lib/useApiData";
import { useToast } from "@/lib/useToast";

type Gap = Api["GapOut"];

export default function GapsPage() {
  const [converting, setConverting] = useState<Gap | null>(null);
  const [answer, setAnswer] = useState("");
  const [category, setCategory] = useState("Новые темы");
  const [line, setLine] = useState(1);
  const { toast, show: notify } = useToast();
  const { data, error, reload } = useApiData<Gap[]>("/api/admin/gaps");
  const gaps = data ?? [];

  const convert = async () => {
    if (!converting) return;
    try {
      await api.post(`/api/admin/gaps/${converting.id}/convert`,
        { answer, category, line, publish: true });
      notify("Статья опубликована — агент уже отвечает на этот вопрос");
      setConverting(null);
      setAnswer("");
      reload();
    } catch (e) {
      notify(errorText(e, "Статья не опубликовалась"), 8000);
    }
  };

  const dismiss = async (id: string) => {
    try {
      await api.post(`/api/admin/gaps/${id}/dismiss`);
      reload();
    } catch (e) {
      notify(errorText(e, "Вопрос не удалось отклонить"), 8000);
    }
  };

  return (
    <div className="space-y-3">
      {error && <LoadError message={`Список пробелов не загрузился: ${error}`} onRetry={reload} />}
      <div className="card flex items-center gap-2 border-l-4 border-l-(--color-brand) px-4 py-3">
        <CircleHelp size={15} className="shrink-0 text-(--color-brand-ink)" />
        <p className="text-xs leading-relaxed">
          Система <b>сама находит пробелы</b>: вопросы, на которые база знаний не ответила, копятся здесь
          с частотой повторов. Один клик — и пробел становится статьёй, доступной агенту мгновенно.
        </p>
      </div>

      {toast && (
        <div className="card animate-(--animate-fade-up) border-l-4 border-l-(--color-ok) px-3 py-2 text-xs">
          {toast}
        </div>
      )}

      <div className="card divide-y divide-(--color-line)">
        {gaps.length === 0 && (
          <div className="p-6 text-center text-xs text-(--color-ink-soft)">
            Открытых пробелов нет — база знаний покрывает все вопросы
          </div>
        )}
        {gaps.map((g) => (
          <div key={g.id} className="flex flex-wrap items-center gap-2 px-4 py-3">
            <div className="min-w-0 flex-1">
              <div className="text-xs font-medium">{g.query}</div>
              <div className="text-xs text-(--color-ink-soft)">
                спрашивали {g.hits} раз · лучший скор {(g.best_score * 100).toFixed(0)}% ·
                последний раз {new Date(g.last_seen).toLocaleDateString("ru")}
              </div>
            </div>
            <span className="rounded-full bg-(--color-brand-soft) px-2 py-0.5 text-xs font-bold text-(--color-brand-ink)">
              ×{g.hits}
            </span>
            <button className="btn btn-primary !py-1.5 text-xs" onClick={() => setConverting(g)}>
              <Sparkles size={12} /> Создать статью
            </button>
            <button className="btn btn-ghost !p-2" title="Отклонить" onClick={() => void dismiss(g.id)}>
              <X size={13} />
            </button>
          </div>
        ))}
      </div>

      {converting && (
        <Modal title="Пробел → статья базы знаний" onClose={() => setConverting(null)}>
          <div className="mb-3 rounded-xl bg-(--color-surface-2) px-3 py-2 text-xs">
            <b>Вопрос:</b> {converting.query}
          </div>
          <span className="label">Ответ</span>
          <textarea aria-label="Ответ" className="input mb-3 resize-none" rows={6} value={answer}
            placeholder="Напишите ответ — он сразу станет доступен ИИ-агенту"
            onChange={(e) => setAnswer(e.target.value)} />
          <div className="mb-4 grid grid-cols-2 gap-3">
            <div>
              <span className="label">Категория</span>
              <input aria-label="Категория" className="input" value={category} onChange={(e) => setCategory(e.target.value)} />
            </div>
            <div>
              <span className="label">Линия</span>
              <select aria-label="Линия" className="input" value={line} onChange={(e) => setLine(Number(e.target.value))}>
                {[1, 2, 3].map((n) => <option key={n} value={n}>Линия {n}</option>)}
              </select>
            </div>
          </div>
          <div className="flex justify-end gap-2">
            <button className="btn btn-ghost" onClick={() => setConverting(null)}>Отмена</button>
            <button className="btn btn-primary" onClick={() => void convert()} disabled={answer.length < 5}>
              Опубликовать
            </button>
          </div>
        </Modal>
      )}
    </div>
  );
}
