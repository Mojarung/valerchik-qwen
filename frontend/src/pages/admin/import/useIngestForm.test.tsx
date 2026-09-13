import { afterEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, renderHook } from "@testing-library/react";

/**
 * Форма загрузки документа: формат и размер проверяются до загрузки, название
 * подставляется из имени файла, отправка чистит ровно то, что ушло.
 */
const { post, upload } = vi.hoisted(() => ({ post: vi.fn(), upload: vi.fn() }));
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return { ...actual, api: { ...actual.api, post, upload } };
});

import { UPLOAD_TIMEOUT_MS } from "@/lib/api";
import { useIngestForm, type IngestFormOptions } from "./useIngestForm";

function setup(options: Partial<IngestFormOptions> = {}) {
  const onQueued = vi.fn();
  const hook = renderHook(() => useIngestForm({ maxDocumentMb: 128, onQueued, ...options }));
  return { ...hook, onQueued };
}
const doc = (name: string, bytes = 10) => new File([new Uint8Array(bytes)], name);

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("useIngestForm", () => {
  it("неподдерживаемый формат — отказ сразу, до загрузки", () => {
    const { result } = setup();
    act(() => result.current.pick(doc("setup.exe")));
    expect(result.current.file).toBeNull();
    expect(result.current.error).toContain("Формат .exe не поддерживается");
  });

  it("файл без расширения — просит переименовать", () => {
    const { result } = setup();
    act(() => result.current.pick(doc("README")));
    expect(result.current.error).toContain("нет расширения");
  });

  it("больше предела — отказ с размером файла и пределом", () => {
    const { result } = setup({ maxDocumentMb: 1 });
    act(() => result.current.pick(doc("big.pdf", 2 * 1024 * 1024)));
    expect(result.current.file).toBeNull();
    expect(result.current.error).toBe("Файл 2.0 МБ — больше предела в 1 МБ. Разделите документ на части.");
  });

  it("название берётся из имени файла, если его не задали; PDF узнаётся", () => {
    const { result } = setup();
    act(() => result.current.pick(doc("Инструкция_по-актированию.pdf")));
    expect(result.current.title).toBe("Инструкция по актированию");
    expect(result.current.isPdf).toBe(true);
    expect(result.current.canSubmit).toBe(true);
  });

  it("отправка файла: поля уходят формой, выбор очищается, список задач обновляется", async () => {
    upload.mockResolvedValue({ job_id: "j1" });
    const { result, onQueued } = setup();
    act(() => result.current.pick(doc("Регламент.docx")));
    act(() => result.current.setCategory("Регламенты"));

    await act(async () => { await result.current.submit(); });

    expect(upload).toHaveBeenCalledTimes(1);
    const [path, form] = upload.mock.calls[0]! as [string, FormData];
    expect(path).toBe("/api/kb/ingest/file");
    expect(form.get("title")).toBe("Регламент");
    expect(form.get("category")).toBe("Регламенты");
    expect(form.get("status")).toBe("draft");
    expect(form.get("use_llm")).toBe("true");
    expect(result.current.file).toBeNull();
    expect(result.current.title).toBe("");
    expect(result.current.notice).toContain("черновиками");
    expect(onQueued).toHaveBeenCalled();
  });

  it("текстом — не короче 20 символов и с долгим таймаутом разбора", async () => {
    post.mockResolvedValue({});
    const { result } = setup();
    act(() => result.current.setMode("text"));
    act(() => result.current.setText("Коротко."));
    expect(result.current.canSubmit).toBe(false);

    const text = "Подписание контракта выполняется в личном кабинете.";
    act(() => result.current.setText(text));
    expect(result.current.canSubmit).toBe(true);
    await act(async () => { await result.current.submit(); });

    expect(post).toHaveBeenCalledWith("/api/kb/ingest",
      expect.objectContaining({ text, title: "Документ", status: "draft" }), { timeoutMs: UPLOAD_TIMEOUT_MS });
    expect(result.current.text).toBe("");
  });

  it("отказ сервера — ошибка на месте, выбранный файл не теряется", async () => {
    upload.mockRejectedValue(new Error("Сервис разбора документов недоступен"));
    const { result } = setup();
    act(() => result.current.pick(doc("Регламент.docx")));

    await act(async () => { await result.current.submit(); });

    expect(result.current.error).toBe("Сервис разбора документов недоступен");
    expect(result.current.file?.name).toBe("Регламент.docx");
    expect(result.current.sending).toBe(false);
  });
});
