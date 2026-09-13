import { useRef, useState, type RefObject } from "react";
import { api, errorText, UPLOAD_TIMEOUT_MS } from "@/lib/api";
import { useToast } from "@/lib/useToast";

// Список ровно тот же, что SUPPORTED_EXTENSIONS на бэкенде: если сузить здесь,
// форматы просто перестанут приниматься, хотя сервер их умеет.
export const ACCEPT = ".pdf,.docx,.doc,.xlsx,.xls,.pptx,.html,.htm,.md,.markdown,.txt,.csv,"
  + ".epub,.json,.xml";

export type IngestMode = "file" | "text";

export interface IngestFormOptions {
  /** Предел документа с сервера; null — мета ещё не приехала, проверит сервер. */
  maxDocumentMb: number | null;
  /** Документ поставлен в очередь — список задач пора перезапросить. */
  onQueued: () => void;
}

export interface IngestForm {
  mode: IngestMode;
  setMode: (mode: IngestMode) => void;
  file: File | null;
  /** Выбрать файл: формат и размер проверяются сразу, до загрузки. */
  pick: (file: File | null) => void;
  fileInput: RefObject<HTMLInputElement | null>;
  text: string;
  setText: (text: string) => void;
  title: string;
  setTitle: (title: string) => void;
  category: string;
  setCategory: (category: string) => void;
  publish: boolean;
  setPublish: (value: boolean) => void;
  useLlm: boolean;
  setUseLlm: (value: boolean) => void;
  doOcr: boolean;
  setDoOcr: (value: boolean) => void;
  isPdf: boolean;
  sending: boolean;
  error: string;
  /** Итог постановки в очередь — показывается несколько секунд. */
  notice: string | null;
  canSubmit: boolean;
  submit: () => Promise<void>;
}

/** Короче этого текст разбирать бессмысленно: статьи из него не выйдет. */
const MIN_TEXT_CHARS = 20;
const MB = 1024 * 1024;

/** Форма загрузки документа в базу знаний: файлом или текстом. */
export function useIngestForm({ maxDocumentMb, onQueued }: IngestFormOptions): IngestForm {
  const [mode, setMode] = useState<IngestMode>("file");
  const [file, setFile] = useState<File | null>(null);
  const [text, setText] = useState("");
  const [title, setTitle] = useState("");
  const [category, setCategory] = useState("");
  const [publish, setPublish] = useState(false);
  const [useLlm, setUseLlm] = useState(true);
  const [doOcr, setDoOcr] = useState(false);
  const [sending, setSending] = useState(false);
  const [error, setError] = useState("");
  const { toast: notice, show: notify } = useToast(6000);
  const fileInput = useRef<HTMLInputElement>(null);

  /**
   * Проверяем формат и размер на месте: перетаскиванием можно принести что угодно,
   * а атрибут accept ограничивает только диалог выбора. Узнавать об отказе после
   * долгой загрузки — плохой сценарий.
   */
  const pick = (chosen: File | null) => {
    setError("");
    if (!chosen) {
      setFile(null);
      return;
    }
    const dot = chosen.name.lastIndexOf(".");
    const extension = dot > 0 ? chosen.name.slice(dot).toLowerCase() : "";
    if (!extension || !ACCEPT.split(",").includes(extension)) {
      setFile(null);
      setError(extension
        ? `Формат ${extension} не поддерживается. Допустимы: ${ACCEPT}`
        : "У файла нет расширения — по нему определяется формат. Переименуйте файл.");
      return;
    }
    if (maxDocumentMb !== null && chosen.size > maxDocumentMb * MB) {
      setFile(null);
      setError(`Файл ${(chosen.size / MB).toFixed(1)} МБ — больше предела в `
        + `${maxDocumentMb} МБ. Разделите документ на части.`);
      return;
    }
    setFile(chosen);
    if (!title.trim()) {
      setTitle(chosen.name.replace(/\.[^.]+$/, "").replace(/[_-]+/g, " "));
    }
  };

  const submit = async () => {
    if (mode === "file" && !file) return;
    const sent = file;
    setSending(true);
    setError("");
    try {
      const status = publish ? "active" : "draft";
      if (mode === "file") {
        const form = new FormData();
        form.append("file", sent as File);
        form.append("title", title.trim());
        form.append("category", category.trim());
        form.append("status", status);
        form.append("use_llm", String(useLlm));
        form.append("do_ocr", String(doOcr));
        await api.upload<{ job_id: string }>("/api/kb/ingest/file", form);
      } else {
        // В lite документ разбирается прямо в запросе — это минуты, а не секунды
        await api.post("/api/kb/ingest", {
          title: title.trim() || "Документ", text,
          category: category.trim() || null, status, use_llm: useLlm,
        }, { timeoutMs: UPLOAD_TIMEOUT_MS });
      }
      notify(publish
        ? "Документ в очереди. Статьи появятся в базе знаний и сразу попадут в поиск."
        : "Документ в очереди. Статьи лягут черновиками — проверьте их в разделе «База знаний».");
      // Чистим ровно то, что ушло на сервер: за время выгрузки пользователь мог
      // выбрать следующий файл, и стирать его было бы обидно.
      if (mode === "file") {
        setFile((current) => (current === sent ? null : current));
        if (fileInput.current && file === sent) fileInput.current.value = "";
      } else {
        setText("");
      }
      setTitle("");
      onQueued();
    } catch (e) {
      setError(errorText(e, "Не удалось поставить документ в очередь"));
    } finally {
      setSending(false);
    }
  };

  const isPdf = !!file && file.name.toLowerCase().endsWith(".pdf");
  const canSubmit = !sending && (mode === "file" ? !!file : text.trim().length >= MIN_TEXT_CHARS);

  return {
    mode, setMode, file, pick, fileInput, text, setText, title, setTitle, category, setCategory,
    publish, setPublish, useLlm, setUseLlm, doOcr, setDoOcr, isPdf, sending, error, notice, canSubmit, submit,
  };
}
