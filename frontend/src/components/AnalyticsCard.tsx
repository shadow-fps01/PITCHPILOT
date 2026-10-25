/**
 * AnalyticsCard — compact stat tile used across the Dashboard and session
 * reports. Numbers animate, the icon sits in a tone-tinted badge, and the card
 * lifts with a soft glow on hover.
 */

import type { LucideIcon } from "lucide-react";

import { AnimatedNumber } from "@/components/ui/AnimatedNumber";
import { cn } from "@/lib/utils";

export type AnalyticsTone = "neutral" | "info" | "success" | "warn" | "alert";

interface AnalyticsCardProps {
  icon: LucideIcon;
  label: string;
  value: string | number;
  sub?: string;
  tone?: AnalyticsTone;
}

const TONE: Record<AnalyticsTone, { badge: string; value: string; glow: string }> = {
  neutral: {
    badge: "bg-slate-400/10 text-slate-300",
    value: "text-slate-50",
    glow: "hover:shadow-[0_10px_40px_-18px_rgba(148,163,184,0.6)]",
  },
  info: {
    badge: "bg-sky-400/15 text-sky-300",
    value: "text-sky-200",
    glow: "hover:shadow-[0_10px_40px_-16px_rgba(56,189,248,0.6)]",
  },
  success: {
    badge: "bg-emerald-400/15 text-emerald-300",
    value: "text-emerald-200",
    glow: "hover:shadow-[0_10px_40px_-16px_rgba(52,211,153,0.6)]",
  },
  warn: {
    badge: "bg-amber-400/15 text-amber-300",
    value: "text-amber-200",
    glow: "hover:shadow-[0_10px_40px_-16px_rgba(251,191,36,0.6)]",
  },
  alert: {
    badge: "bg-rose-400/15 text-rose-300",
    value: "text-rose-200",
    glow: "hover:shadow-[0_10px_40px_-16px_rgba(251,113,133,0.6)]",
  },
};

export function AnalyticsCard({
  icon: Icon,
  label,
  value,
  sub,
  tone = "neutral",
}: AnalyticsCardProps): JSX.Element {
  const t = TONE[tone];
  return (
    <div
      className={cn(
        "panel-inset group relative overflow-hidden p-4 transition-all duration-300 hover:-translate-y-0.5",
        t.glow,
      )}
    >
      <div className="flex items-center gap-2">
        <span className={cn("rounded-lg p-1.5", t.badge)}>
          <Icon size={14} />
        </span>
        <p className="text-[11px] font-semibold uppercase tracking-widest text-slate-400">
          {label}
        </p>
      </div>
      <p className={cn("mt-2.5 text-2xl font-bold tabular-nums", t.value)}>
        {typeof value === "number" ? <AnimatedNumber value={value} /> : value}
      </p>
      {sub !== undefined && <p className="mt-0.5 text-xs text-slate-500">{sub}</p>}
    </div>
  );
}
