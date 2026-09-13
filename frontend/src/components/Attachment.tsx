import { Download, FileText } from "lucide-react";
import { assetUrl } from "@/lib/api";

/** Вложение оператора: приезжает в meta сообщения, а не в тексте. */
export interface MessageAttachment {
  url: string;
  name: string;
  size: number;
  content_type: string;
  kind: "image" | "file";
}

export function attachmentOf(meta: unknown): MessageAttachment | null {
  const found = (meta as { attachment?: MessageAttachment } | null)?.attachment;
  return found?.url ? found : null;
}

export function humanSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} Б`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} КБ`;
  return `${(bytes / 1024 / 1024).toFixed(1)} МБ`;
}

/**
 * Картинку показываем сразу — оператор шлёт скриншот, чтобы его увидели, а не
 * чтобы скачали. Документ показать нечем, поэтому он превращается в плашку со
 * скачиванием: имя, размер и понятно, куда нажимать.
 */
export function Attachment({ attachment, onZoom }: {
  attachment: MessageAttachment;
  /** Клик по картинке — крупный просмотр (в чате есть лайтбокс, у оператора нет). */
  onZoom?: () => void;
}) {
  if (attachment.kind === "image") {
    return (
      <img
        src={assetUrl(attachment.url)}
        alt={attachment.name}
        loading="lazy"
        onClick={onZoom}
        className={`mt-1.5 max-h-64 w-auto max-w-full rounded-xl border border-(--color-line) ${
          onZoom ? "cursor-zoom-in" : ""}`}
      />
    );
  }
  return (
    <a href={assetUrl(attachment.url)} download={attachment.name}
      className="mt-1.5 flex items-center gap-2 rounded-xl border border-(--color-line) bg-(--color-surface) px-3 py-2 text-(--color-ink) transition-colors hover:border-(--color-brand) hover:text-(--color-brand-ink)">
      <FileText size={16} className="shrink-0" />
      <span className="min-w-0 flex-1">
        <span className="block truncate text-xs font-medium">{attachment.name}</span>
        <span className="block text-xs text-(--color-ink-soft)">
          {humanSize(attachment.size)}
        </span>
      </span>
      <Download size={14} className="shrink-0 opacity-70" />
    </a>
  );
}
