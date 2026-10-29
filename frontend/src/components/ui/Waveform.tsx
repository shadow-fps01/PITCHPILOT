/**
 * Waveform — a small live level meter.
 *
 * When `active` it tracks the microphone amplitude (each bar has its own
 * weight, so it reads like a real VU meter); when idle it undulates gently so
 * the UI never looks dead.
 */

import { cn } from "@/lib/utils";

const WEIGHTS = [0.42, 0.72, 1, 0.82, 0.56, 0.66, 0.38];

interface WaveformProps {
  level?: number;
  active?: boolean;
  bars?: number;
  className?: string;
  tone?: "brand" | "emerald" | "gold";
}

const TONE: Record<NonNullable<WaveformProps["tone"]>, string> = {
  brand: "bg-gradient-to-t from-amber-400 to-amber-300",
  emerald: "bg-gradient-to-t from-emerald-400 to-emerald-300",
  gold: "bg-gradient-to-t from-amber-400 to-yellow-300",
};

export function Waveform({
  level = 0,
  active = false,
  bars = 5,
  className,
  tone = "brand",
}: WaveformProps): JSX.Element {
  const shown = WEIGHTS.slice(0, bars);
  return (
    <div className={cn("flex h-5 items-end gap-[3px]", className)}>
      {shown.map((weight, index) => {
        const height = active
          ? Math.min(100, 14 + level * 100 * weight)
          : 14 + weight * 22;
        return (
          <span
            key={index}
            className={cn(
              "w-[3px] rounded-full transition-[height] duration-100 ease-out",
              active ? TONE[tone] : "bg-slate-700",
            )}
            style={{
              height: `${Math.max(12, height)}%`,
              animation: !active
                ? `wave ${1.4 + index * 0.18}s ease-in-out ${-index * 0.2}s infinite`
                : undefined,
              transformOrigin: "bottom",
            }}
          />
        );
      })}
    </div>
  );
}
