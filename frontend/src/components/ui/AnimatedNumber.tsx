/**
 * AnimatedNumber — counts smoothly to a new value instead of snapping.
 * Used for every live stat (WPM, fillers, durations, usage meters).
 */

import { useEffect, useRef, useState } from "react";

export function useAnimatedNumber(value: number, duration = 650): number {
  const [display, setDisplay] = useState(value);
  const displayRef = useRef(value);

  useEffect(() => {
    const from = displayRef.current;
    if (Math.abs(from - value) < 0.001) return;
    const start = performance.now();
    let raf = 0;
    const tick = (now: number): void => {
      const t = Math.min(1, (now - start) / duration);
      const eased = 1 - Math.pow(1 - t, 3);
      const next = from + (value - from) * eased;
      displayRef.current = next;
      setDisplay(next);
      if (t < 1) raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [value, duration]);

  return display;
}

interface AnimatedNumberProps {
  value: number;
  decimals?: number;
  suffix?: string;
  className?: string;
  duration?: number;
}

export function AnimatedNumber({
  value,
  decimals = 0,
  suffix = "",
  className,
  duration,
}: AnimatedNumberProps): JSX.Element {
  const display = useAnimatedNumber(value, duration);
  return (
    <span className={className}>
      {display.toFixed(decimals)}
      {suffix}
    </span>
  );
}
