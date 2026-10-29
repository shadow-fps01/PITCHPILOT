/**
 * Aurora — the app's dynamic ambient backdrop.
 *
 * A black canvas with a faint gold blueprint grid, warm drifting light
 * (champagne → gold → amber) and a slow rotating gold ring. The `intensity`
 * prop (0..1) lets a live screen push the aurora brighter in response to
 * speech, and the pointer subtly parallaxes the light so the scene feels
 * three-dimensional rather than flat.
 *
 * Palette note: strictly black + gold. No purple/violet, and no cool
 * sky/teal wash — those muddied the gold accents used across the UI.
 */

import { useEffect, useRef } from "react";

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
  const lift = 1 + Math.min(1, Math.max(0, intensity)) * 0.5;
  const rootRef = useRef<HTMLDivElement | null>(null);

  // Pointer parallax: shift the light field a few pixels away from the cursor.
  useEffect(() => {
    if (subtle) return;
    const media = window.matchMedia("(prefers-reduced-motion: reduce)");
    if (media.matches) return;
    const node = rootRef.current;
    if (node === null) return;

    let frame = 0;
    let targetX = 0;
    let targetY = 0;
    let currentX = 0;
    let currentY = 0;

    const onMove = (event: PointerEvent): void => {
      targetX = (event.clientX / window.innerWidth - 0.5) * -30;
      targetY = (event.clientY / window.innerHeight - 0.5) * -22;
    };

    const tick = (): void => {
      currentX += (targetX - currentX) * 0.06;
      currentY += (targetY - currentY) * 0.06;
      node.style.setProperty("--px", currentX.toFixed(2));
      node.style.setProperty("--py", currentY.toFixed(2));
      frame = window.requestAnimationFrame(tick);
    };

    window.addEventListener("pointermove", onMove, { passive: true });
    frame = window.requestAnimationFrame(tick);
    return () => {
      window.removeEventListener("pointermove", onMove);
      window.cancelAnimationFrame(frame);
    };
  }, [subtle]);

  return (
    <div
      ref={rootRef}
      aria-hidden
      className={cn(
        "parallax pointer-events-none fixed inset-0 -z-10 overflow-hidden",
        className,
      )}
    >
      {/* pure black base — gold is the only light source */}
      <div className="absolute inset-0 bg-black" />
      <div
        className={cn(
          "absolute inset-0 bg-grid animate-grid-pan",
          subtle ? "opacity-[0.07]" : "opacity-[0.12]",
        )}
      />

      {!subtle && (
        <div
          className="absolute left-1/2 top-[-30%] h-[80rem] w-[80rem] -translate-x-1/2 rounded-full opacity-[0.16] blur-[90px] animate-spin-slow"
          style={{
            background:
              "conic-gradient(from 0deg, transparent 0deg, rgba(251,191,36,0.55) 70deg, transparent 140deg, rgba(253,230,138,0.42) 240deg, transparent 320deg)",
          }}
        />
      )}

      {/* drifting gold light pools */}
      <div
        className="absolute -left-[14%] -top-[24%] h-[46rem] w-[46rem] rounded-full blur-[120px] animate-aurora-a"
        style={{
          background:
            "radial-gradient(circle at center, rgba(251,191,36,0.34), transparent 62%)",
          opacity: (subtle ? 0.3 : 0.7) * lift,
        }}
      />
      <div
        className="absolute -right-[12%] top-[6%] h-[40rem] w-[40rem] rounded-full blur-[130px] animate-aurora-b"
        style={{
          background:
            "radial-gradient(circle at center, rgba(245,158,11,0.26), transparent 62%)",
          opacity: (subtle ? 0.26 : 0.62) * lift,
        }}
      />
      <div
        className="absolute bottom-[-28%] left-[26%] h-[42rem] w-[42rem] rounded-full blur-[140px] animate-aurora-a"
        style={{
          background:
            "radial-gradient(circle at center, rgba(253,230,138,0.22), transparent 64%)",
          animationDelay: "-8s",
          opacity: (subtle ? 0.22 : 0.52) * lift,
        }}
      />

      {/* vignette to keep foreground text legible */}
      <div className="absolute inset-0 bg-[radial-gradient(ellipse_at_50%_0%,transparent_20%,rgba(0,0,0,0.82)_80%)]" />
      {/* fine warm grain, sells the "molten gold on black" material */}
      <div className="absolute inset-0 opacity-[0.05] mix-blend-overlay [background-image:repeating-linear-gradient(115deg,rgba(251,191,36,0.9)_0_1px,transparent_1px_3px)]" />
    </div>
  );
}
