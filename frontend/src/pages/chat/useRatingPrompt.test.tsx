import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, renderHook } from "@testing-library/react";
import { useRatingPrompt, type RatingPromptInput } from "./useRatingPrompt";

/**
 * Предложение оценить консультацию: после паузы в диалоге, не посреди
 * разговора, заново — с новым диалогом.
 */
const base: RatingPromptInput = { generation: 1, idleSeconds: 30, busy: false, blocked: false, lastMessageId: "m2" };

const setup = (input: Partial<RatingPromptInput> = {}) =>
  renderHook((props: RatingPromptInput) => useRatingPrompt(props), { initialProps: { ...base, ...input } });

const wait = (seconds: number) => act(() => { vi.advanceTimersByTime(seconds * 1000); });

beforeEach(() => { vi.useFakeTimers(); });
afterEach(() => { cleanup(); vi.useRealTimers(); });

describe("useRatingPrompt", () => {
  it("предлагает оценку после паузы в диалоге", () => {
    const { result } = setup();
    wait(29);
    expect(result.current.askRating).toBe(false);
    wait(1);
    expect(result.current.askRating).toBe(true);
  });

  it("пока идёт ответ или человек печатает — молчит, после — отсчитывает заново", () => {
    const { result, rerender } = setup({ busy: true });
    wait(120);
    expect(result.current.askRating).toBe(false);
    rerender({ ...base, busy: false });
    wait(30);
    expect(result.current.askRating).toBe(true);
  });

  it("закрытый, ждущий оператора или ещё без ответа диалог — не предлагает", () => {
    const { result, rerender } = setup({ blocked: true });
    wait(120);
    expect(result.current.askRating).toBe(false);
    rerender({ ...base, blocked: false });
    wait(30);
    expect(result.current.askRating).toBe(true);
  });

  it("пауза 0 в настройках — не предлагает вовсе", () => {
    const { result, rerender } = setup({ idleSeconds: 0 });
    wait(120);
    expect(result.current.askRating).toBe(false);
    rerender({ ...base, idleSeconds: 30 });
    wait(30);
    expect(result.current.askRating).toBe(true);
  });

  it("новое сообщение начинает отсчёт заново", () => {
    const { result, rerender } = setup();
    wait(20);
    rerender({ ...base, lastMessageId: "m3" });
    wait(20);
    expect(result.current.askRating).toBe(false);
    wait(10);
    expect(result.current.askRating).toBe(true);
  });

  it("«Не сейчас» — в этом диалоге больше не предлагает, в новом — снова", () => {
    const { result, rerender } = setup();
    wait(30);
    act(() => result.current.decline());
    expect(result.current.askRating).toBe(false);
    rerender({ ...base, lastMessageId: "m4" });
    wait(60);
    expect(result.current.askRating).toBe(false);

    rerender({ ...base, generation: 2, lastMessageId: "m5" });
    wait(30);
    expect(result.current.askRating).toBe(true);
  });

  it("поставленная оценка гасит предложение и живёт до нового диалога", () => {
    const { result, rerender } = setup();
    wait(30);
    act(() => result.current.markRated(4));
    expect(result.current).toMatchObject({ askRating: false, rated: true, ratedStars: 4 });

    rerender({ ...base, generation: 2 });
    expect(result.current).toMatchObject({ rated: false, ratedStars: 0 });
  });

  it("разговор продолжился — карточка убирается сама", () => {
    const { result } = setup();
    wait(30);
    expect(result.current.askRating).toBe(true);
    act(() => result.current.hide());
    expect(result.current.askRating).toBe(false);
  });
});
