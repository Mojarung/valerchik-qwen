import { useRef, useState, type RefObject } from "react";
import { Upload } from "lucide-react";

/** Зона выбора файла. Перетаскивание — основной способ: методичку тащат из
 *  папки «Загрузки», поэтому вся зона — одна большая кнопка и цель для броска. */
export function FileDrop({ file, accept, maxDocumentMb, inputRef, onPick }: {
  file: File | null;
  accept: string;
  maxDocumentMb: number | null;
  inputRef: RefObject<HTMLInputElement | null>;
  onPick: (file: File | null) => void;
}) {
  const [dragOver, setDragOver] = useState(false);
  const dragDepth = useRef(0);

  return (
    <>
      <button
        type="button"
        className={`flex w-full cursor-pointer flex-col items-center gap-1.5 rounded-2xl border-2 border-dashed px-4 py-7 text-center transition-colors ${
          dragOver
            ? "border-(--color-brand) bg-(--color-brand-soft)"
            : "border-(--color-line) hover:border-(--color-brand)"}`}
        onClick={() => inputRef.current?.click()}
        onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
        // Считаем входы и выходы: переход на вложенный элемент тоже даёт
        // dragleave, и без счётчика подсветка мигает под курсором.
        onDragEnter={() => { dragDepth.current += 1; setDragOver(true); }}
        onDragLeave={() => {
          dragDepth.current -= 1;
          if (dragDepth.current <= 0) {
            dragDepth.current = 0;
            setDragOver(false);
          }
        }}
        onDrop={(e) => {
          e.preventDefault();
          dragDepth.current = 0;
          setDragOver(false);
          onPick(e.dataTransfer.files?.[0] ?? null);
        }}
      >
        <Upload size={22} className="text-(--color-ink-soft)" />
        <div className="text-sm font-medium">
          {file ? file.name : "Перетащите файл или нажмите, чтобы выбрать"}
        </div>
        <div className="text-xs text-(--color-ink-soft)">
          {file
            ? `${(file.size / 1024 / 1024).toFixed(1)} МБ`
            : `pdf, docx, xlsx, xls, pptx, html, md, txt, csv${maxDocumentMb ? ` — до ${maxDocumentMb} МБ` : ""}`}
        </div>
      </button>
      <input ref={inputRef} type="file" accept={accept} className="hidden"
        onChange={(e) => onPick(e.target.files?.[0] ?? null)} />
    </>
  );
}
