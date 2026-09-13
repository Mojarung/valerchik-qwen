import { useRef } from "react";
import { Modal } from "@/components/Modal";

/**
 * Подтверждение необратимого действия вместо `window.confirm`: нативное окно
 * нельзя оформить, оно не знает тёмной темы и не даёт назвать кнопку по делу
 * («Удалить насовсем» вместо «ОК»). Фокус стоит на «Отмена» — случайный Enter
 * не удаляет.
 */
export function ConfirmDialog({ open, title, text, confirmLabel, danger = false, onConfirm, onCancel }: {
  open: boolean;
  title: string;
  text: string;
  confirmLabel: string;
  danger?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  const cancelRef = useRef<HTMLButtonElement>(null);
  if (!open) return null;
  return (
    <Modal title={title} onClose={onCancel} initialFocus={cancelRef}>
      <p className="whitespace-pre-line text-sm leading-relaxed text-(--color-ink-soft)">{text}</p>
      <div className="mt-5 flex justify-end gap-2">
        <button ref={cancelRef} type="button" className="btn btn-ghost" onClick={onCancel}>
          Отмена
        </button>
        <button
          type="button"
          className={`btn btn-primary ${danger ? "!bg-(--color-brand-dark)" : ""}`}
          onClick={onConfirm}
        >
          {confirmLabel}
        </button>
      </div>
    </Modal>
  );
}
