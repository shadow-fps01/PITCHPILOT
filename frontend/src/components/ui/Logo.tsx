/**
 * Logo — the PitchPilot mark: a gradient mic in a halo with a slow rotating
 * conic ring, so the brand feels alive at the top of every screen.
 */

import { Mic } from "lucide-react";

import { cn } from "@/lib/utils";

interface LogoProps {
  size?: number;
  className?: string;
  /** Show the rotating conic halo (bigger marks only). */
  halo?: boolean;
}

export function Logo({ size = 40, className, halo = false }: LogoProps): JSX.Element {
  return (
    <span
      className={cn("relative inline-flex items-center justify-center", className)}
      style={{ width: size, height: size }}
    >
      {halo && (
        <span className="absolute inset-[-30%] rounded-full bg-gradient-to-tr from-amber-500/25 via-yellow-300/20 to-transparent blur-md animate-spin-slow" />
      )}
      <span className="absolute inset-0 rounded-2xl bg-gradient-brand opacity-90 shadow-glow" />
      <span className="absolute inset-[1.5px] rounded-[14px] bg-ink-950/85 backdrop-blur" />
      <Mic
        size={Math.round(size * 0.5)}
        className="relative text-white"
        strokeWidth={2.2}
      />
    </span>
  );
}
