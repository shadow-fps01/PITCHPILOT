/**
 * WpmGauge — a dynamic words-per-minute dial.
 *
 * A gradient arc sweeps with the live pace, a dot rides the tip, target
 * markers sit on the track, and the whole thing pulses when the speaker is
 * running hot. The number counts smoothly (no jitter).
 */

import { AnimatedNumber } from "@/components/ui/AnimatedNumber";
import { cn } from "@/lib/utils";

interface WpmGaugeProps {
  wpm: number;
  target: number;
  className?: string;
}

type GaugeZone = "idle" | "slow" | "good" | "fast" | "very-fast";

const ZONE: Record<GaugeZone, { color: string; label: string; text: string }> = {
  idle: { color: "#64748b", label: "warming up", text: "text-slate-400" },
  slow: { color: "#fbbf24", label: "ease in", text: "text-amber-300" },
  good: { color: "#34d399", label: "on pace", text: "text-emerald-300" },
  fast: { color: "#fbbf24", label: "a touch fast", text: "text-amber-300" },
  "very-fast": { color: "#fb7185", label: "slow down", text: "text-rose-300" },
};

function zoneFor(wpm: number, target: number): GaugeZone {
  if (wpm <= 0) return "idle";
  if (wpm > target * 1.35) return "very-fast";
  if (wpm > target + 25) return "fast";
  if (wpm < target - 30) return "slow";
  return "good";
}

const CX = 60;
const CY = 54;
const R = 46;
const ARC = Math.PI * R;
const ARC_PATH = `M ${CX - R} ${CY} A ${R} ${R} 0 0 1 ${CX + R} ${CY}`;

function pointOnArc(fraction: number): { x: number; y: number } {
  const theta = Math.PI + fraction * Math.PI;
  return { x: CX + R * Math.cos(theta), y: CY + R * Math.sin(theta) };
}

export function WpmGauge({ wpm, target, className }: WpmGaugeProps): JSX.Element {
  const max = Math.max(target * 2, 200);
  const clamped = Math.min(Math.max(wpm, 0), max);
  const fraction = clamped / max;
  const zone = zoneFor(wpm, target);
  const { color, label, text } = ZONE[zone];
  const targetFraction = Math.min(1, target / max);
  const targetTip = pointOnArc(targetFraction);
  const progress = fraction * ARC;
  const tip = pointOnArc(fraction);
  const hot = zone === "very-fast";

  return (
    <div className={cn("relative w-36 select-none", className)}>
      <svg viewBox="0 0 120 66" className="w-full overflow-visible">
        <defs>
          <linearGradient id="wpm-grad" x1="0" y1="0" x2="1" y2="0">
            <stop offset="0%" stopColor="#fbbf24" />
            <stop offset="55%" stopColor={color} />
            <stop offset="100%" stopColor="#fbbf24" />
          </linearGradient>
        </defs>

        {/* track */}
        <path
          d={ARC_PATH}
          fill="none"
          stroke="rgba(148,163,184,0.16)"
          strokeWidth="8"
          strokeLinecap="round"
        />
        {/* target marker */}
        <circle cx={targetTip.x} cy={targetTip.y} r="2.4" fill="rgba(226,232,240,0.55)" />

        {/* live progress */}
        <path
          d={ARC_PATH}
          fill="none"
          stroke="url(#wpm-grad)"
          strokeWidth="8"
          strokeLinecap="round"
          strokeDasharray={`${progress} ${ARC}`}
          style={{
            transition:
              "stroke-dasharray 0.7s cubic-bezier(0.4,0,0.2,1), stroke 0.4s ease",
            filter: `drop-shadow(0 0 6px ${color}88)`,
          }}
        />

        {/* riding dot */}
        {wpm > 0 && (
          <circle
            cx={tip.x}
            cy={tip.y}
            r="3.4"
            fill="#fff"
            stroke={color}
            strokeWidth="2"
            style={{ transition: "cx 0.7s cubic-bezier(0.4,0,0.2,1), cy 0.7s cubic-bezier(0.4,0,0.2,1)" }}
          />
        )}
      </svg>

      <div className="pointer-events-none absolute inset-x-0 bottom-1 flex flex-col items-center">
        <span className="flex items-baseline gap-1">
          <AnimatedNumber
            value={wpm}
            className={cn("text-2xl font-bold tabular-nums leading-none", text)}
          />
          <span className="text-[10px] font-medium text-slate-500">WPM</span>
        </span>
        <span className="mt-0.5 text-[10px] text-slate-500">
          target {target}
        </span>
      </div>

      {hot && (
        <span
          className="pointer-events-none absolute left-6 top-4 h-2 w-2 rounded-full"
          style={{ background: color }}
        >
          <span className="absolute inset-0 animate-ping-soft rounded-full" style={{ background: color }} />
        </span>
      )}

      <span className={cn("sr-only", text)}>{label}</span>
    </div>
  );
}
