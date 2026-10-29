/** One translucent coaching-cue toast card for the live overlay. */

import { Gauge, ListChecks, MessageSquareWarning } from "lucide-react";
import type { LucideIcon } from "lucide-react";

import type { LiveCue } from "@/types";
import { cn } from "@/lib/utils";

const SEVERITY_STYLE: Record<
  LiveCue["severity"],
  { border: string; icon: string; body: string; bar: string }
> = {
  info: {
    border: "border-amber-400/30",
    icon: "bg-amber-400/15 text-amber-300",
    body: "text-amber-50",
    bar: "bg-amber-400",
  },
  warn: {
    border: "border-amber-400/40",
    icon: "bg-amber-400/15 text-amber-300",
    body: "text-amber-50",
    bar: "bg-amber-400",
  },
  alert: {
    border: "border-rose-400/50",
    icon: "bg-rose-400/15 text-rose-300",
    body: "text-rose-50",
    bar: "bg-rose-400",
  },
};

const CUE_ICON: Record<LiveCue["cue_type"], LucideIcon> = {
  FILLER_WORD: MessageSquareWarning,
  PACING_ALERT: Gauge,
  SCRIPT_MISS: ListChecks,
};

export function CueToast({ cue }: { cue: LiveCue }): JSX.Element {
  const style = SEVERITY_STYLE[cue.severity];
  const Icon = CUE_ICON[cue.cue_type];
  return (
    <div
      className={cn(
        "relative animate-cue-in overflow-hidden rounded-xl border bg-slate-900/85 px-3 py-2.5 pl-4 shadow-lg backdrop-blur-md",
        cue.severity === "alert" && "animate-border-flash",
        style.border,
      )}
    >
      <span className={cn("absolute inset-y-0 left-0 w-1", style.bar)} />
      <div className="flex items-start gap-2.5">
        <span className={cn("mt-0.5 shrink-0 rounded-lg p-1.5", style.icon)}>
          <Icon size={14} />
        </span>
        <p className={cn("text-[13px] font-medium leading-snug", style.body)}>
          {cue.message}
        </p>
      </div>
    </div>
  );
}
