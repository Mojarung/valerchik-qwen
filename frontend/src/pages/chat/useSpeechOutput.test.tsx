import { afterEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, renderHook } from "@testing-library/react";

/** Озвучка ответов: только в голосовом режиме, замолкает, когда человек говорит. */
vi.mock("@/lib/speech", () => ({
  speechEnabled: () => true,
  setSpeechEnabled: vi.fn(),
  stopSpeaking: vi.fn(),
  speak: vi.fn(async () => undefined),
}));

import { setSpeechEnabled, stopSpeaking } from "@/lib/speech";
import { useSpeechOutput, type SpeechOutputInput } from "./useSpeechOutput";

const base: SpeechOutputInput = { ttsAvailable: true, liveState: "listening", listening: false };

function setup(input: Partial<SpeechOutputInput> = {}) {
  const speakRef = { current: false };
  const hook = renderHook((props: SpeechOutputInput) => useSpeechOutput(speakRef, props),
    { initialProps: { ...base, ...input } });
  return { ...hook, speakRef };
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("useSpeechOutput", () => {
  it("озвучивает только в голосовом режиме, при доступном синтезе и включённой озвучке", () => {
    const { speakRef, rerender } = setup();
    expect(speakRef.current).toBe(true);

    rerender({ ...base, liveState: "off" });
    expect(speakRef.current).toBe(false);
    rerender({ ...base, ttsAvailable: false });
    expect(speakRef.current).toBe(false);
  });

  it("человек заговорил — ответ замолкает", () => {
    const { rerender } = setup();
    expect(stopSpeaking).not.toHaveBeenCalled();
    rerender({ ...base, listening: true });
    expect(stopSpeaking).toHaveBeenCalled();
  });

  it("выключатель озвучки запоминается", () => {
    const { result, speakRef } = setup();
    act(() => result.current.toggleSpeech());
    expect(result.current.speechOn).toBe(false);
    expect(setSpeechEnabled).toHaveBeenCalledWith(false);
    expect(speakRef.current).toBe(false);
  });

  it("уход со страницы обрывает фразу", () => {
    const { unmount } = setup();
    unmount();
    expect(stopSpeaking).toHaveBeenCalled();
  });
});
