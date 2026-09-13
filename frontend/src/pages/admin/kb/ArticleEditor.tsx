import { useRef, useState } from "react";
import { ImagePlus, X } from "lucide-react";
import { Modal } from "@/components/Modal";
import { api, assetUrl, errorText } from "@/lib/api";
import type { ArticleImage, KbArticle } from "@/lib/types";
import { AUDIENCE_LABEL, audienceOf } from "./articles";

/**
 * Редактор статьи. Ошибки сохранения и загрузки картинки показываются в самом
 * окне: тост страницы лежал под подложкой модалки, а правка не должна пропадать
 * вместе с ошибкой.
 */
export function ArticleEditor({ initial, onSave, onClose }: {
  initial: Partial<KbArticle>;
  /** Сохранить; отказ показывается в окне, и окно остаётся открытым. */
  onSave: (article: Partial<KbArticle>) => Promise<void>;
  onClose: () => void;
}) {
  const [draft, setDraft] = useState<Partial<KbArticle>>(initial);
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);
  const savingRef = useRef(false);
  const edit = (patch: Partial<KbArticle>) => setDraft((current) => ({ ...current, ...patch }));
  const images = draft.images ?? [];

  /** Скриншот к статье руками: когда сервис разбора методичек не поднят или
   *  картинку не удалось привязать автоматически. */
  const uploadImage = async (file: File) => {
    setError("");
    const form = new FormData();
    form.append("file", file);
    try {
      const image = await api.upload<ArticleImage>("/api/kb/media", form);
      setDraft((current) => ({ ...current, images: [...(current.images ?? []), image] }));
    } catch (e) {
      setError(errorText(e, "Не удалось загрузить картинку"));
    }
  };

  const save = async () => {
    if (savingRef.current) return;
    savingRef.current = true;
    setSaving(true);
    setError("");
    try {
      // Роль и картинки уходят всегда: пустой список — это «убрать все», сервер так и понимает
      await onSave({ ...draft, audience: draft.audience ?? "all", images });
    } catch (e) {
      setError(errorText(e, "Статья не сохранилась"));
    } finally {
      savingRef.current = false;
      setSaving(false);
    }
  };

  return (
    <Modal onClose={() => { if (!savingRef.current) onClose(); }} title={draft.id ? "Редактирование статьи" : "Новая статья"}>
      <fieldset disabled={saving} className="min-w-0">
      <span className="label">Вопрос</span>
      <input aria-label="Вопрос" className="input mb-3" value={draft.question ?? ""}
        onChange={(e) => edit({ question: e.target.value })} />
      <span className="label">Ответ</span>
      <textarea aria-label="Ответ" className="input mb-3 resize-none" rows={6} value={draft.answer ?? ""}
        onChange={(e) => edit({ answer: e.target.value })} />
      <div className="mb-3 grid grid-cols-2 gap-3 sm:grid-cols-4">
        <div>
          <span className="label">Категория</span>
          <input aria-label="Категория" className="input" value={draft.category ?? ""}
            onChange={(e) => edit({ category: e.target.value })} />
        </div>
        <div>
          <span className="label">Линия</span>
          <select aria-label="Линия" className="input" value={draft.line ?? 1}
            onChange={(e) => edit({ line: Number(e.target.value) })}>
            {[1, 2, 3].map((n) => <option key={n} value={n}>Линия {n}</option>)}
          </select>
        </div>
        {/* Роль: поставщику статью заказчика в выдаче не покажут, и наоборот */}
        <div>
          <span className="label">Кому</span>
          <select aria-label="Кому" className="input" value={draft.audience ?? "all"}
            onChange={(e) => edit({ audience: audienceOf(e.target.value) })}>
            {Object.entries(AUDIENCE_LABEL).map(([key, label]) => (
              <option key={key} value={key}>{label}</option>
            ))}
          </select>
        </div>
        <div>
          <span className="label">Статус</span>
          <select aria-label="Статус" className="input" value={draft.status ?? "active"}
            onChange={(e) => edit({ status: e.target.value })}>
            <option value="active">Активна</option>
            <option value="draft">Черновик</option>
            <option value="archived">Архив</option>
          </select>
        </div>
      </div>
      <span className="label">Ключевые слова (через запятую)</span>
      <input aria-label="Ключевые слова (через запятую)" className="input mb-3"
        value={(draft.keywords ?? []).join(", ")}
        onChange={(e) => edit({ keywords: e.target.value.split(",").map((s) => s.trim()).filter(Boolean) })} />

      {/* Иллюстрации: текст инструкций ссылается на скриншоты прямо —
          «нажмите (Рисунок 26)», — и без картинки такой ответ читается
          как загадка. Разбор методички кладёт их сам, здесь — руками. */}
      <span className="label">Иллюстрации</span>
      <div className="mb-4 space-y-2">
        {images.map((image, index) => (
          <div key={image.url} className="flex items-center gap-2">
            <img src={assetUrl(image.url)} alt="" loading="lazy"
              className="h-12 w-16 shrink-0 rounded-lg border border-(--color-line) object-cover" />
            <input aria-label="Подпись к иллюстрации" className="input flex-1 !text-xs"
              placeholder="Подпись, например «Рисунок 3 — Кнопка подписания»"
              value={image.caption ?? ""}
              onChange={(e) => edit({
                images: images.map((img, i) => (i === index ? { ...img, caption: e.target.value } : img)),
              })} />
            <button className="btn btn-ghost !p-1.5 hover:text-(--color-brand-ink)" title="Убрать"
              aria-label="Убрать иллюстрацию"
              onClick={() => edit({ images: images.filter((_, i) => i !== index) })}>
              <X size={14} />
            </button>
          </div>
        ))}
        <label className="btn btn-ghost cursor-pointer text-xs">
          <ImagePlus size={14} /> Добавить картинку
          <input type="file" accept="image/png,image/jpeg,image/webp,image/gif" hidden
            onChange={(e) => {
              const file = e.target.files?.[0];
              e.target.value = "";
              if (file) void uploadImage(file);
            }} />
        </label>
      </div>
      {error && (
        <p role="alert" className="mb-3 rounded-lg border-l-4 border-l-(--color-brand) bg-(--color-surface-2) px-2 py-1 text-xs">
          {error}
        </p>
      )}
      <p className="mb-3 text-xs text-(--color-ink-soft)">
        После сохранения новая версия опубликованного ответа автоматически попадёт в очередь
        проверки противоречий. Проверка выполняется, когда включён автоматический режим,
        настроена модель и доступен дневной лимит. Прогресс — в разделе «Противоречия».
      </p>
      <div className="flex flex-col gap-2 sm:flex-row sm:justify-end">
        <button className="btn btn-ghost" onClick={onClose}>Отмена</button>
        <button className="btn btn-primary whitespace-normal" onClick={() => void save()}
          disabled={(draft.question ?? "").length < 5 || (draft.answer ?? "").length < 5}>
          {saving ? "Сохраняем статью…" : "Сохранить — применится сразу"}
        </button>
      </div>
      </fieldset>
    </Modal>
  );
}
