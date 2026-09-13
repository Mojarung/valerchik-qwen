import { useState } from "react";
import { Modal } from "@/components/Modal";
import { api, errorText, UPLOAD_TIMEOUT_MS } from "@/lib/api";
import type { Api } from "@/lib/types";

/** Импорт статей массивом JSON. Формат совпадает с экспортом — базу можно
 *  переносить между стендами. Ошибки — в самом окне, текст не пропадает. */
export function ImportJsonModal({ onImported, onClose }: {
  /** Сервер принял статьи: сколько импортировано. */
  onImported: (count: number) => void;
  onClose: () => void;
}) {
  const [text, setText] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const submit = async () => {
    setError("");
    let items: unknown;
    try {
      items = JSON.parse(text);
    } catch {
      // Текст движка («Unexpected token } in JSON at position 14») администратору
      // ничего не говорит — объясняем по-русски, где обычно ошибаются.
      setError("Это не JSON — проверьте скобки, кавычки и запятые между статьями");
      return;
    }
    setBusy(true);
    try {
      const res = await api.post<Api["ImportResult"]>("/api/kb/import", { items }, { timeoutMs: UPLOAD_TIMEOUT_MS });
      onImported(res.imported);
    } catch (e) {
      setError(errorText(e, "Статьи не импортировались"));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal onClose={onClose} title="Импорт статей (JSON)">
      <p className="mb-2 text-xs text-(--color-ink-soft)">
        Массив объектов: question, answer, category, keywords[], line, status,
        audience (all | supplier | customer), images[]. Формат совпадает с экспортом —
        можно переносить базу между стендами.
      </p>
      <textarea className="input mb-4 h-56 resize-none font-mono !text-xs" aria-label="Статьи в формате JSON"
        placeholder='[{"question": "...", "answer": "...", "category": "...", "keywords": [], "line": 1, "status": "active"}]'
        value={text} onChange={(e) => setText(e.target.value)} />
      {error && (
        <p role="alert" className="mb-3 rounded-lg border-l-4 border-l-(--color-brand) bg-(--color-surface-2) px-2 py-1 text-xs">
          {error}
        </p>
      )}
      <div className="flex justify-end gap-2">
        <button className="btn btn-ghost" onClick={onClose}>Отмена</button>
        <button className="btn btn-primary" onClick={() => void submit()} disabled={busy || !text.trim()}>
          Импортировать
        </button>
      </div>
    </Modal>
  );
}
