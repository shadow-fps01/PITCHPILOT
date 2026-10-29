/**
 * Teleprompter — a dynamic, voice-responsive prompt panel (copilot F4).
 *
 * - Outline words already spoken dim to green; the next ~10 words render
 *   large and highlighted; the rest stay muted.
 * - Auto-scroll keeps the current word at a fixed reading anchor, driven by
 *   actual transcript coverage (so it waits when the speaker waits).
 * - Mic amplitude pulses the current line; the WPM modulates the progress
 *   bar so pacing feedback is ambient, not mechanical.
 * - If coverage stalls >6s mid-session, the current line breathes gently
 *   as a "keep going" nudge.
 */

import { useEffect, useMemo, useRef, useState } from "react";

import { useTeleprompter } from "@/hooks/useTeleprompter";
import { cn } from "@/lib/utils";

interface TeleprompterProps {
  outline: string;
  finalTranscript: string;
  micLevel: number;
  wpm: number;
  targetWpm: number;
  active: boolean;
}

const CURRENT_WINDOW = 10; // words rendered in the highlighted zone
const STALL_NUDGE_SECONDS = 6;

export function Teleprompter({
  outline,
  finalTranscript,
  micLevel,
  wpm,
  targetWpm,
  active,
}: TeleprompterProps): JSX.Element {
  const { tokens, currentWordIndex, progress } = useTeleprompter(
    outline,
    finalTranscript,
  );
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const [stalled, setStalled] = useState(false);
  const lastProgressRef = useRef({ index: 0, at: Date.now() });

  // Scroll so the current word sits at ~38% height of the panel.
  useEffect(() => {
    if (!active || currentWordIndex === 0) return;
    const container = scrollRef.current;
    if (container === null) return;
    const anchor = container.querySelector<HTMLElement>(
      `[data-index="${currentWordIndex}"]`,
    );
    if (anchor !== null) {
      container.scrollTo({
        top: anchor.offsetTop - container.clientHeight * 0.38,
        behavior: "smooth",
      });
    }
  }, [currentWordIndex, active]);

  // Stall detection — nudge when coverage freezes mid-speech.
  useEffect(() => {
    if (!active) return;
    if (currentWordIndex !== lastProgressRef.current.index) {
      lastProgressRef.current = { index: currentWordIndex, at: Date.now() };
      if (stalled) setStalled(false);
      return;
    }
    const timer = window.setInterval(() => {
      if (Date.now() - lastProgressRef.current.at > STALL_NUDGE_SECONDS * 1000) {
        setStalled(true);
      }
    }, 1500);
    return () => window.clearInterval(timer);
  }, [currentWordIndex, active, stalled]);

  // Amplitude-driven glow for the current reading zone.
  const glow = useMemo(
    () => `0 0 ${Math.round(8 + micLevel * 18)}px rgba(251, 191, 36, ${0.3 + micLevel * 0.5})`,
    [micLevel],
  );
  const paceTone =
    wpm > targetWpm * 1.35
      ? "from-rose-400 to-rose-500"
      : wpm > targetWpm + 25
        ? "from-amber-400 to-amber-500"
        : "from-yellow-200 to-amber-400";

  if (tokens.length === 0) {
    return (
      <p className="px-4 py-3 text-center text-xs text-slate-500">
        This script has no outline text to prompt with.
      </p>
    );
  }

  return (
    <div className="relative border-t border-white/5 bg-white/[0.015]">
      {/* pacing-modulated progress bar */}
      <div className="absolute inset-x-0 top-0 h-0.5 bg-white/5">
        <div
          className={cn(
            "h-full bg-gradient-to-r transition-all duration-500",
            paceTone,
          )}
          style={{ width: `${Math.round(progress * 100)}%` }}
        />
      </div>
      <div
        ref={scrollRef}
        className="relative max-h-40 overflow-y-auto px-4 py-3 leading-relaxed"
      >
        <p className="text-[15px]">
          {tokens.map((token, index) => {
            const spoken = index < currentWordIndex;
            const inWindow =
              !spoken && index < currentWordIndex + CURRENT_WINDOW;
            const isCurrent = index === currentWordIndex;
            if (inWindow) {
              return (
                <span
                  key={index}
                  data-index={index}
                  className={cn(
                    "text-amber-100 transition-all",
                    isCurrent &&
                      "font-semibold text-white",
                    isCurrent && stalled && active && "animate-pulse",
                  )}
                  style={
                    isCurrent
                      ? { textShadow: glow, fontSize: `${15 + micLevel * 4}px` }
                      : undefined
                  }
                >
                  {token.raw}{" "}
                </span>
              );
            }
            return (
              <span
                key={index}
                data-index={index}
                className={cn(
                  spoken ? "text-emerald-400/50" : "text-slate-600",
                )}
              >
                {token.raw}{" "}
              </span>
            );
          })}
        </p>
        {/* soft bottom fade so upcoming words melt away */}
        <div className="pointer-events-none sticky bottom-0 -mt-6 h-6 bg-gradient-to-t from-[rgba(6,10,22,0.9)] to-transparent" />
      </div>
    </div>
  );
}
