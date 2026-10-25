/** Compact footer links to the built-in public policy/support pages. */

import { useApp } from "@/context/AppContext";
import { cn } from "@/lib/utils";
import type { LegalView } from "@/types";

const LINKS: Array<{ view: LegalView; label: string }> = [
  { view: "privacy", label: "Privacy" },
  { view: "terms", label: "Terms" },
  { view: "refunds", label: "Refunds" },
  { view: "support", label: "Support" },
];

export function LegalLinks({
  className,
}: {
  className?: string;
}): JSX.Element {
  const { setView } = useApp();

  return (
    <nav
      aria-label="Legal and support"
      className={cn("flex flex-wrap items-center gap-x-4 gap-y-1.5", className)}
    >
      {LINKS.map((link) => (
        <a
          key={link.view}
          href={`/?view=${link.view}`}
          onClick={(event) => {
            // Keep modified clicks (new tab/window) native.
            if (
              event.metaKey ||
              event.ctrlKey ||
              event.shiftKey ||
              event.altKey ||
              event.button !== 0
            ) {
              return;
            }
            event.preventDefault();
            setView(link.view);
          }}
          className="text-[11px] font-medium text-slate-500 transition-colors hover:text-slate-300"
        >
          {link.label}
        </a>
      ))}
    </nav>
  );
}
