/**
 * AccountNotice — a slim, non-blocking banner for account states that need
 * attention: the result of an email-verification link, or a reminder that the
 * address still needs confirming. Rendered above every non-live screen.
 */

import { useEffect, useState } from "react";
import { Loader2, MailWarning, X } from "lucide-react";

import { useApp } from "@/context/AppContext";
import { useAuth } from "@/context/AuthContext";
import { authApi } from "@/lib/api";

type ResendState = "idle" | "sending" | "sent" | "error";

export function AccountNotice(): JSX.Element | null {
  const { view } = useApp();
  const { user, notice, dismissNotice } = useAuth();
  const [resendState, setResendState] = useState<ResendState>("idle");
  const [emailAvailable, setEmailAvailable] = useState<boolean | null>(null);

  const needsVerification = user !== null && !user.email_verified;

  useEffect(() => {
    if (!needsVerification) return;
    let cancelled = false;
    authApi
      .emailAvailability()
      .then((result) => {
        if (!cancelled) setEmailAvailable(result.available);
      })
      .catch(() => {
        if (!cancelled) setEmailAvailable(false);
      });
    return () => {
      cancelled = true;
    };
  }, [needsVerification]);

  // Don't ask someone to confirm an address when this deployment can't send
  // the confirmation email yet. Verification results (notice) still show.
  const showVerificationReminder = needsVerification && emailAvailable === true;
  if (view === "live" || (notice === null && !showVerificationReminder)) return null;

  const resend = async (): Promise<void> => {
    setResendState("sending");
    try {
      const result = await authApi.resendVerification();
      setResendState(result.sent ? "sent" : "error");
    } catch {
      setResendState("error");
    }
  };

  return (
    <div className="pointer-events-none fixed inset-x-0 top-4 z-50 flex justify-center px-4">
      <div
        role="status"
        aria-live="polite"
        className="pointer-events-auto flex w-full max-w-xl items-start gap-3 rounded-2xl border border-amber-300/25 bg-slate-950/85 px-4 py-3 text-sm text-amber-50 shadow-2xl shadow-black/40 backdrop-blur-xl"
      >
        <MailWarning className="mt-0.5 size-4 shrink-0 text-amber-300" />

        <div className="min-w-0 flex-1">
          {notice !== null ? (
            <p>{notice}</p>
          ) : (
            <>
              <p className="font-semibold text-white">
                Confirm your email to start coaching
              </p>
              <p className="mt-0.5 text-amber-100/70">
                {resendState === "sent"
                  ? "Sent — check your inbox (and spam folder)."
                  : resendState === "error"
                    ? "Couldn't send that just now — please try again."
                    : "Open the link we emailed you when you signed up."}
              </p>
            </>
          )}
        </div>

        {notice !== null ? (
          <button
            type="button"
            onClick={dismissNotice}
            aria-label="Dismiss notice"
            className="rounded-lg p-1 text-amber-100/60 transition hover:bg-white/10 hover:text-white"
          >
            <X className="size-4" />
          </button>
        ) : resendState !== "sent" ? (
          <button
            type="button"
            onClick={() => void resend()}
            disabled={resendState === "sending"}
            className="flex shrink-0 items-center gap-1.5 rounded-lg border border-amber-300/30 bg-amber-300/10 px-2.5 py-1 text-xs font-semibold text-amber-100 transition hover:bg-amber-300/20 disabled:opacity-60"
          >
            {resendState === "sending" ? (
              <Loader2 className="size-3.5 animate-spin" />
            ) : null}
            Resend
          </button>
        ) : null}
      </div>
    </div>
  );
}
