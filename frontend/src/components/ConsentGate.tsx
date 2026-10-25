/**
 * ConsentGate — blocking first-run confirmation that the presenter has
 * permission to record/transcribe the call. The backend enforces the same
 * gate, so hiding this dialog cannot bypass it.
 */

import { useState } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import { Loader2, LockKeyhole, LogOut, ShieldCheck } from "lucide-react";

import { useApp } from "@/context/AppContext";
import { useAuth } from "@/context/AuthContext";
import { ApiError } from "@/lib/api";

export function ConsentGate(): JSX.Element | null {
  const { view } = useApp();
  const { user, acceptConsent, logout } = useAuth();
  const [checked, setChecked] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Wait until email verification is done (when that gate is enabled), and
  // never cover the transparent live overlay.
  if (
    user === null ||
    user.consent_current ||
    view === "live" ||
    view === "privacy" ||
    view === "terms" ||
    view === "refunds" ||
    view === "support" ||
    !user.email_verified
  ) {
    return null;
  }

  // The built-in pages are always available; env vars can point at external
  // copies instead. Links open in a new tab so this consent dialog survives.
  const privacyUrl =
    import.meta.env.VITE_PRIVACY_URL?.trim() || "/?view=privacy";
  const termsUrl = import.meta.env.VITE_TERMS_URL?.trim() || "/?view=terms";

  const accept = async (): Promise<void> => {
    if (!checked || busy) return;
    setBusy(true);
    setError(null);
    try {
      await acceptConsent();
    } catch (err) {
      setError(
        err instanceof ApiError
          ? err.message
          : "Could not save that just now — please try again.",
      );
      setBusy(false);
    }
  };

  return (
    <Dialog.Root open>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-[60] bg-ink-950/90 backdrop-blur-md" />
        <Dialog.Content
          onEscapeKeyDown={(event) => event.preventDefault()}
          onPointerDownOutside={(event) => event.preventDefault()}
          className="panel fixed left-1/2 top-1/2 z-[70] w-[calc(100%-2rem)] max-w-lg -translate-x-1/2 -translate-y-1/2 animate-scale-in p-6 sm:p-7"
        >
          <div className="flex items-start gap-3">
            <span className="rounded-2xl border border-sky-400/20 bg-sky-400/10 p-2.5 text-sky-300">
              <ShieldCheck size={20} />
            </span>
            <div>
              <Dialog.Title className="text-lg font-bold text-white">
                Before your first live session
              </Dialog.Title>
              <Dialog.Description className="mt-1 text-xs leading-relaxed text-slate-400">
                PitchPilot listens to your microphone and, when you choose, the
                call audio you share. Short segments are transcribed and
                analyzed live; screen frames are analyzed and discarded.
                Transcript excerpts and coaching notes stay in your account for
                up to 30 days.
              </Dialog.Description>
            </div>
          </div>

          <div className="mt-5 rounded-2xl border border-amber-300/20 bg-amber-300/[0.06] p-4">
            <p className="text-xs leading-relaxed text-amber-100/80">
              Only start a session when you have permission to record everyone
              on the call. If you are unsure, ask the other participants first.
            </p>
          </div>

          <label className="mt-5 flex cursor-pointer items-start gap-3 rounded-xl border border-white/10 bg-white/[0.03] p-3.5">
            <input
              type="checkbox"
              checked={checked}
              onChange={(event) => setChecked(event.target.checked)}
              className="mt-0.5 size-4 shrink-0 accent-sky-400"
            />
            <span className="text-xs leading-relaxed text-slate-300">
              I have permission to record this call and agree to the{" "}
              <a
                href={privacyUrl}
                target="_blank"
                rel="noreferrer"
                className="font-semibold text-sky-300 underline decoration-sky-300/40 underline-offset-2 hover:text-sky-200"
              >
                Privacy Policy
              </a>{" "}
              and{" "}
              <a
                href={termsUrl}
                target="_blank"
                rel="noreferrer"
                className="font-semibold text-sky-300 underline decoration-sky-300/40 underline-offset-2 hover:text-sky-200"
              >
                Terms
              </a>
              .
            </span>
          </label>

          {error !== null && (
            <p className="mt-3 rounded-xl border border-rose-500/30 bg-rose-500/10 px-3 py-2 text-xs text-rose-300">
              {error}
            </p>
          )}

          <button
            type="button"
            onClick={() => void accept()}
            disabled={!checked || busy}
            className="btn-primary btn-sheen mt-5 w-full"
          >
            {busy ? (
              <Loader2 size={16} className="animate-spin" />
            ) : (
              <LockKeyhole size={16} />
            )}
            I have permission — continue
          </button>

          <button
            type="button"
            onClick={logout}
            className="mx-auto mt-3 flex items-center gap-1.5 text-[11px] font-medium text-slate-500 transition-colors hover:text-slate-300"
          >
            <LogOut size={12} /> Sign out
          </button>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
