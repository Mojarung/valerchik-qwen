import { afterEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, renderHook } from "@testing-library/react";
import type { LiveVoiceOptions } from "@/lib/liveVoice";
import type { VoiceInputOptions } from "./useVoiceInput";

/** Голосовой ввод: live-режим живёт в своей сессии и отпускает микрофон при закрытии. */
const { live, supported } = vi.hoisted(() => {
  // Стор при загрузке спрашивает тему у matchMedia, а в jsdom её нет
  window.matchMedia = ((query: string) => ({
    matches: false, media: query, onchange: null,
    addEventListener: () => undefined, removeEventListener: () => undefined,
    addListener: () => undefined, removeListener: () => undefined, dispatchEvent: () => false,
  })) as unknown as typeof window.matchMedia;
  return {
    live: [] as { options: LiveVoiceOptions; start: () => Promise<void>; stop: () => void }[],
    supported: { value: true },
  };
});
vi.mock("@/lib/liveVoice", () => ({
  liveVoiceSupported: () => supported.value,
  LiveVoiceSession: class {
    options: LiveVoiceOptions;
    start = vi.fn(async () => undefined);
    stop = vi.fn();
    constructor(options: LiveVoiceOptions) {
      this.options = options;
      live.push(this);
    }
  },
}));

import { useVoiceInput } from "./useVoiceInput";

function setup(overrides: Partial<VoiceInputOptions> = {}) {
  const options: VoiceInputOptions = {
    closed: false, onDictation: vi.fn(), onUtterance: vi.fn(), onError: vi.fn(), ...overrides,
  };
  const hook = renderHook((props: VoiceInputOptions) => useVoiceInput(props), { initialProps: options });
  return { ...hook, options };
}

afterEach(() => {
  cleanup();
  live.length = 0;
  supported.value = true;
});

describe("useVoiceInput", () => {
  it("браузер без записи — понятная ошибка вместо тишины", async () => {
    supported.value = false;
    const { result, options } = setup();
    await act(() => result.current.toggleLive());
    expect(options.onError).toHaveBeenCalledWith("Браузер не поддерживает запись с микрофона");
    expect(live).toHaveLength(0);
  });

  it("live: состояние из сессии, реплика уходит в свежий обработчик", async () => {
    const { result, rerender, options } = setup();
    await act(() => result.current.toggleLive());
    expect(live[0]?.start).toHaveBeenCalled();

    act(() => live[0]!.options.onState("listening"));
    expect(result.current.liveState).toBe("listening");
    expect(result.current.listening).toBe(true);

    const fresh = vi.fn();
    rerender({ ...options, onUtterance: fresh });
    live[0]!.options.onUtterance("Как подать заявку?");
    expect(fresh).toHaveBeenCalledWith("Как подать заявку?");
    expect(options.onUtterance).not.toHaveBeenCalled();
  });

  it("повторное нажатие выключает live", async () => {
    const { result } = setup();
    await act(() => result.current.toggleLive());
    await act(() => result.current.toggleLive());
    expect(live[0]?.stop).toHaveBeenCalled();
    expect(result.current.liveState).toBe("off");
  });

  it("закрытие диалога сразу отпускает микрофон", async () => {
    const { result, rerender, options } = setup();
    await act(() => result.current.toggleLive());
    rerender({ ...options, closed: true });
    expect(live[0]?.stop).toHaveBeenCalled();
    expect(result.current.liveState).toBe("off");
  });

  it("уход со страницы отпускает микрофон", async () => {
    const { result, unmount } = setup();
    await act(() => result.current.toggleLive());
    unmount();
    expect(live[0]?.stop).toHaveBeenCalled();
  });

  it("нет доступа к микрофону — понятная ошибка", async () => {
    const { result, options } = setup();
    await act(() => result.current.toggleRecording());
    expect(options.onError).toHaveBeenCalledWith("Нет доступа к микрофону");
    expect(result.current.recording).toBe(false);
  });
});
