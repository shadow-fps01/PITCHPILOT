/**
 * Aurora — the app's dynamic ambient backdrop.
 *
 * A near-black canvas with a faint animated blueprint grid and three drifting
 * gradient blobs (sky → indigo → violet). The `intensity` prop (0..1) lets a
 * live screen push the aurora brighter/faster in response to speech, so the
 * background literally reacts to the room.
 */

import { cn } from "@/lib/utils";

interface AuroraProps {
  className?: string;
  /** 0..1 — react to live energy (mic level / pacing). */
  intensity?: number;
  /** "subtle" keeps it dim for the overlay; "full" for the dashboard. */
  variant?: "full" | "subtle";
}

export function Aurora({
  className,
  intensity = 0,
  variant = "full",
}: AuroraProps): JSX.Element {
  const subtle = variant === "subtle";
  const lift = 1 + intensity * 0.5;

  return (
    <div
      aria-hidden
      className={cn(
        "pointer-events-none fixed inset-0 -z-10 overflow-hidden",
        className,
      )}
    >
      <div className="absolute inset-0 bg-ink-950" />
      <div
        className={cn(
          "absolute inset-0 bg-grid animate-grid-pan",
          subtle ? "opacity-[0.1]" : "opacity-[0.16]",
        )}
      />

      {/* drifting aurora blobs */}
      <div
        className="absolute -left-[14%] -top-[24%] h-[46rem] w-[46rem] rounded-full blur-[120px] animate-aurora-a"
        style={{
          background:
            "radial-gradient(circle at center, rgba(56,189,248,0.42), transparent 62%)",
          opacity: (subtle ? 0.4 : 0.85) * lift,
        }}
      />
      <div
        className="absolute -right-[12%] top-[6%] h-[40rem] w-[40rem] rounded-full blur-[130px] animate-aurora-b"
        style={{
          background:
            "radial-gradient(circle at center, rgba(168,85,247,0.4), transparent 62%)",
          opacity: (subtle ? 0.35 : 0.8) * lift,
        }}
      />
      <div
        className="absolute bottom-[-28%] left-[26%] h-[42rem] w-[42rem] rounded-full blur-[140px] animate-aurora-a"
        style={{
          background:
            "radial-gradient(circle at center, rgba(79,70,229,0.38), transparent 64%)",
          animationDelay: "-8s",
          opacity: (subtle ? 0.3 : 0.7) * lift,
        }}
      />

      {/* vignette to keep foreground text legible */}
      <div className="absolute inset-0 bg-[radial-gradient(ellipse_at_50%_0%,transparent_20%,rgba(4,6,15,0.7)_78%)]" />
    </div>
  );
}
