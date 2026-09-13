import { useEffect } from "react";
import { AlertTriangle, CheckCircle2, FileUp, Images, Loader2, Type } from "lucide-react";
import { Toggle } from "@/components/Toggle";
import { useMeta } from "@/lib/store";
import type { Api } from "@/lib/types";
import { useApiData } from "@/lib/useApiData";
import { FileDrop } from "./import/FileDrop";
import { IngestJobs, isActive } from "./import/IngestJobs";
import { ACCEPT, useIngestForm } from "./import/useIngestForm";

/**
 * Загрузка неформализованных материалов: методичек, регламентов, выгрузок.
 *
 * Разбор идёт в фоне — трёхсотстраничная инструкция даёт порядка шестисот статей
 * и занимает минуты, — поэтому страница не ждёт ответа, а показывает очередь
 * задач с прогрессом. Пока хоть одна задача в работе, список сам обновляется.
 */

type IngestJob = Api["IngestJobOut"];

const POLL_MS = 2000;

export default function ImportPage() {
  const docsParsing = useMeta((s) => s.docsParsing);
  // Предел документа — с сервера, а не своей копией: копия разошлась с бэкендом
  // (40 МБ здесь при 128 там). null — мета ещё не приехала, проверит сервер.
  const maxDocumentMb = useMeta((s) => s.maxDocumentMb);

  // Промах мимо дропзоны открыл бы файл прямо в браузере — вкладка ушла бы на
  // просмотр PDF, а заполненная форма пропала.
  useEffect(() => {
    const swallow = (e: DragEvent) => e.preventDefault();
    window.addEventListener("dragover", swallow);
    window.addEventListener("drop", swallow);
    return () => {
      window.removeEventListener("dragover", swallow);
      window.removeEventListener("drop", swallow);
    };
  }, []);

  // Опрашиваем, только пока есть незавершённые задачи: держать таймер ради
  // готового списка незачем.
  const { data: jobs, error: jobsError, reload } = useApiData<IngestJob[]>("/api/kb/ingest/jobs?limit=15", {
    pollMs: POLL_MS,
    pollWhile: (list) => (list ?? []).some(isActive),
  });
  const form = useIngestForm({ maxDocumentMb, onQueued: reload });
  const { mode, isPdf } = form;

  return (
    <div className="space-y-3">
      <div className="card flex items-start gap-2 border-l-4 border-l-(--color-brand) px-4 py-3">
        <FileUp size={15} className="mt-0.5 shrink-0 text-(--color-brand-ink)" />
        <p className="text-xs leading-relaxed">
          Загрузите <b>методичку, регламент или выгрузку</b> — система разберёт документ на статьи:
          порежет по разделам, к каждому куску сформулирует вопрос, который он закрывает, и определит
          линию поддержки. Готовая выгрузка «вопрос — ответ» таблицей распознаётся сама и
          переносится как есть, без переформулирования.
        </p>
      </div>

      <div className="card p-4">
        <div className="mb-3 flex gap-1.5">
          {([["file", "Файл", FileUp], ["text", "Текстом", Type]] as const).map(
            ([value, label, Icon]) => (
              <button key={value}
                className={`flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-medium transition-colors ${
                  mode === value
                    ? "bg-(--color-brand-soft) text-(--color-brand-ink)"
                    : "hover:bg-(--color-surface-2)"}`}
                onClick={() => form.setMode(value)}>
                <Icon size={13} /> {label}
              </button>
            ))}
        </div>

        {mode === "file" ? (
          <>
            <FileDrop file={form.file} accept={ACCEPT} maxDocumentMb={maxDocumentMb}
              inputRef={form.fileInput} onPick={form.pick} />
            {isPdf && (
              <div className={`mt-2 flex items-start gap-2 rounded-xl px-3 py-2 text-xs ${
                docsParsing ? "bg-(--color-surface-2)" : "bg-(--color-warn)/10"}`}>
                <Images size={13} className="mt-0.5 shrink-0" />
                {docsParsing ? (
                  <span>
                    PDF разбирается с разметкой страниц: иллюстрации приедут вместе с подписями и
                    прикрепятся к тем фрагментам, которые на них ссылаются. Считайте
                    ~4 секунды на страницу.
                  </span>
                ) : (
                  <span>
                    Разбор PDF с иллюстрациями не поднят — документ импортируется текстом, без
                    картинок. Чтобы включить, запустите стек с профилем <code>docs</code>.
                  </span>
                )}
              </div>
            )}
          </>
        ) : (
          <textarea className="input h-56 resize-none !text-sm" aria-label="Текст документа"
            placeholder="Вставьте текст методички, регламента или инструкции…"
            value={form.text} onChange={(e) => form.setText(e.target.value)} />
        )}

        <div className="mt-3 grid gap-3 sm:grid-cols-2">
          <div>
            <span className="label">Название документа</span>
            <input aria-label="Название документа" id="ingest-title" className="input" value={form.title}
              placeholder="Инструкция по электронному актированию"
              onChange={(e) => form.setTitle(e.target.value)} />
          </div>
          <div>
            <span className="label">Категория статей</span>
            <input aria-label="Категория статей" id="ingest-category" className="input" value={form.category}
              placeholder="по умолчанию — название документа"
              onChange={(e) => form.setCategory(e.target.value)} />
          </div>
        </div>

        <div className="mt-3 space-y-2 rounded-xl bg-(--color-surface-2) px-3 py-2.5">
          <Toggle checked={form.useLlm} onChange={form.setUseLlm}
            title="Формулировать вопросы через LLM"
            hint="Поиск сравнивает вопрос пользователя с вопросом статьи, поэтому формулировка важна. Выключите, чтобы залить быстро — вопросы возьмутся из заголовков разделов." />
          <Toggle checked={form.publish} onChange={form.setPublish}
            title="Опубликовать сразу"
            hint="По умолчанию статьи ложатся черновиками: формулировки придумала машина, и их стоит просмотреть. Черновики в поиск не попадают." />
          {mode === "file" && isPdf && docsParsing && (
            <Toggle checked={form.doOcr} onChange={form.setDoOcr}
              title="Распознавать текст (OCR)"
              hint="Нужно только для сканов. В обычных методичках текстовый слой есть, и OCR вчетверо удлиняет разбор без пользы." />
          )}
        </div>

        <p className="mt-3 text-xs text-(--color-ink-soft)">
          Опубликованные ответы автоматически попадают в очередь проверки противоречий.
          Черновики — после публикации. Проверка выполняется, когда включён автоматический режим,
          настроена модель и доступен дневной лимит. Прогресс — в разделе «Противоречия».
        </p>

        {/* aria-live: без него результат отправки не объявляется — незрячий
            администратор не узнает ни об ошибке, ни о постановке в очередь */}
        <div aria-live="polite">
          {form.error && (
            <div role="alert"
              className="mt-3 flex items-center gap-2 rounded-xl border-l-4 border-l-(--color-brand) bg-(--color-surface-2) px-3 py-2 text-xs">
              <AlertTriangle size={14} className="shrink-0 text-(--color-brand-ink)" /> {form.error}
            </div>
          )}
          {form.notice && (
            <div className="mt-3 flex items-center gap-2 rounded-xl border-l-4 border-l-(--color-ok) bg-(--color-surface-2) px-3 py-2 text-xs">
              <CheckCircle2 size={14} className="shrink-0 text-(--color-ok-ink)" /> {form.notice}
            </div>
          )}
        </div>

        <div className="mt-3 flex justify-end">
          <button className="btn btn-primary" onClick={() => void form.submit()} disabled={!form.canSubmit}>
            {form.sending ? <Loader2 size={14} className="animate-spin" /> : <FileUp size={14} />}
            Разобрать документ
          </button>
        </div>
      </div>

      <IngestJobs jobs={jobs ?? []} error={jobsError} onReload={reload} />
    </div>
  );
}
