import { Player, type PlayerRef } from "@remotion/player";
import { useCallback, useEffect, useRef, useState } from "react";
import { H, RENDER_FPS, SUB, W } from "./anim";
import { Deck } from "./deck/Deck";
import { SEGMENTS, SLIDES, TOTAL, locate } from "./deck/timeline";
import { C, font } from "./theme";

/**
 * Пульт презентации. Каждый слайд — отрезок таймлайна: переход, демо и покой.
 * Дойдя до конца покоя, плеер отматывается на его начало, так что слайд живёт,
 * пока докладчик говорит. Стрелка вперёд запускает переход к следующему.
 * Отрезки записаны в кадрах по 30 в секунду, плеер считает в своих, по 60.
 */
export function App() {
  const player = useRef<PlayerRef>(null);
  const current = useRef(0);
  const [slide, setSlide] = useState(0);
  const [chrome, setChrome] = useState(true);
  const hideTimer = useRef<number | undefined>(undefined);

  const go = useCallback((next: number, withTransition: boolean) => {
    const p = player.current;
    if (!p) return;
    const idx = Math.max(0, Math.min(SLIDES.length - 1, next));
    current.current = idx;
    setSlide(idx);
    p.seekTo((withTransition ? SEGMENTS[idx].start : SEGMENTS[idx].holdStart) * SUB);
    p.play();
  }, []);

  useEffect(() => {
    const p = player.current;
    if (!p) return;
    // ?frame=N — застывший кадр (в 30-кадровых единицах) для проверки вёрстки скриншотами
    const still = new URLSearchParams(location.search).get("frame");
    if (still !== null) {
      p.pause();
      p.seekTo(Math.round(Number(still) * SUB));
      setSlide(locate(Number(still)).i);
      return;
    }
    const onFrame = (e: { detail: { frame: number } }) => {
      const u = e.detail.frame / SUB;
      setSlide(locate(u).i);
      const seg = SEGMENTS[current.current];
      if (u >= seg.end - 1) p.seekTo(seg.holdStart * SUB);
    };
    p.addEventListener("frameupdate", onFrame);
    p.play();
    return () => p.removeEventListener("frameupdate", onFrame);
  }, []);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const i = current.current;
      if (["ArrowRight", "PageDown", " ", "Enter"].includes(e.key)) {
        e.preventDefault();
        if (i < SLIDES.length - 1) go(i + 1, true);
      } else if (["ArrowLeft", "PageUp", "Backspace"].includes(e.key)) {
        e.preventDefault();
        go(i - 1, false);
      } else if (e.key === "Home") {
        go(0, true);
      } else if (e.key.toLowerCase() === "r" || e.key === "к") {
        go(i, true);
      } else if (e.key.toLowerCase() === "f" || e.key === "а") {
        if (document.fullscreenElement) void document.exitFullscreen();
        else void document.documentElement.requestFullscreen();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [go]);

  const wake = () => {
    setChrome(true);
    window.clearTimeout(hideTimer.current);
    hideTimer.current = window.setTimeout(() => setChrome(false), 2200);
  };

  return (
    <div
      onMouseMove={wake}
      onClick={() => current.current < SLIDES.length - 1 && go(current.current + 1, true)}
      style={{
        position: "fixed", inset: 0, background: C.surface, display: "grid", placeItems: "center",
        cursor: chrome ? "default" : "none", fontFamily: font.sans,
      }}
    >
      <Player
        ref={player}
        component={Deck}
        durationInFrames={TOTAL * SUB}
        compositionWidth={W}
        compositionHeight={H}
        fps={RENDER_FPS}
        style={{ width: "100vw", height: "100vh" }}
        clickToPlay={false}
        doubleClickToFullscreen={false}
        spaceKeyToPlayOrPause={false}
        acknowledgeRemotionLicense
      />
      <div style={{
        position: "fixed", right: 24, bottom: 20, display: "flex", gap: 12, alignItems: "center",
        padding: "8px 16px", borderRadius: 999, background: C.surfaceHigh, color: C.inkSoft, fontSize: 15,
        opacity: chrome ? 0.95 : 0, transition: "opacity 0.4s", pointerEvents: "none",
      }}>
        <b style={{ color: C.ink, fontVariantNumeric: "tabular-nums" }}>{slide + 1} / {SLIDES.length}</b>
        <span>{SLIDES[slide].title}</span>
        <span style={{ fontFamily: font.mono, fontSize: 13 }}>→ дальше · ← назад · R повтор · F экран</span>
      </div>
    </div>
  );
}
