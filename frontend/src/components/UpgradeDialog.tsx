/**
 * UpgradeDialog — PitchPilot Plus paywall.
 *
 * Checkout always goes through the hosted payment page; there is no free
 * or demo upgrade path.
 */

import { useCallback, useEffect, useState } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import {
  CheckCircle2,
  CreditCard,
  Crown,
  Loader2,
  Sparkles,
  X,
} from "lucide-react";

import { ApiError, billingApi } from "@/lib/api";

interface UpgradeDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Optional message to highlight (e.g. the 402 quota error). */
  reason?: string | null;
  priceDisplay: string;
  freeSessions: number;
  freeScripts: number;
  /** Called after the plan state changes (e.g. an existing payment applied). */
  onUpgraded: () => void;
}

const FEATURES = [
  "Unlimited coaching sessions — pitch as often as you like",
  "Unlimited scripts and outlines",
  "Live pacing, filler-word and script-miss coaching",
  "Next-topic suggestion cards, ready before you ask",
];

export function UpgradeDialog({
  open,
  onOpenChange,
  reason,
  priceDisplay,
  freeSessions,
  freeScripts,
  onUpgraded,
}: UpgradeDialogProps): JSX.Element {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [alreadyPlus, setAlreadyPlus] = useState(false);

  useEffect((): void => {
    if (!open) {
      setError(null);
      setBusy(false);
      setAlreadyPlus(false);
    }
  }, [open]);

  const upgrade = useCallback(async (): Promise<void> => {
    if (busy) return;

    // Open the checkout tab synchronously, before the API request. Browsers
    // only allow popups from a direct click; waiting for the async response
    // would let the popup blocker swallow it. The Electron shell skips this
    // and opens a normal-sized checkout window from its main process instead.
    const isMobile = /Mobi|Android|iPhone|iPad|iPod/i.test(navigator.userAgent);
    let checkoutWindow: Window | null = null;
    if (window.pitchpilotDesktop !== true && !isMobile) {
      checkoutWindow = window.open(
        "",
        "pitchpilot-checkout",
        "popup=yes,width=1100,height=900",
      );
    }

    setBusy(true);
    setError(null);
    try {
      const response = await billingApi.checkout();
      if (response.mode === "paystack" && response.authorization_url !== null) {
        if (checkoutWindow !== null && !checkoutWindow.closed) {
          try {
            checkoutWindow.opener = null;
            checkoutWindow.focus();
          } catch {
            /* opener may already be detached */
          }
          checkoutWindow.location.replace(response.authorization_url);
        } else {
          // Popup blocked or closed: same-tab is still better than no
          // checkout at all, even if the window is narrow.
          window.location.assign(response.authorization_url);
        }
        return; // the hosted page takes over
      }

      checkoutWindow?.close();
      if (response.mode === "already_plus") {
        // A previous Paystack payment was found and activated server-side —
        // no new charge is needed.
        setAlreadyPlus(true);
        onUpgraded();
        return;
      }
      // The provider returned neither a checkout URL nor an active plan:
      // never pretend the upgrade happened.
      setError("Checkout did not open. Please try again.");
    } catch (err) {
      checkoutWindow?.close();
      setError(
        err instanceof ApiError ? err.message : "Could not start the upgrade",
      );
    } finally {
      setBusy(false);
    }
  }, [busy, onUpgraded]);

  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-40 bg-ink-950/80 backdrop-blur-sm" />
        <Dialog.Content className="panel fixed left-1/2 top-1/2 z-50 max-h-[86vh] w-[calc(100%-2rem)] max-w-md -translate-x-1/2 -translate-y-1/2 animate-scale-in overflow-hidden p-0">
          {/* gradient header */}
          <div className="relative overflow-hidden border-b border-white/[0.08] bg-gradient-to-br from-amber-400/15 via-transparent to-transparent px-6 pb-5 pt-5">
            <span className="pointer-events-none absolute -right-8 -top-10 h-32 w-32 rounded-full bg-amber-400/25 blur-3xl" />
            <div className="flex items-start justify-between">
              <div className="flex items-center gap-2.5">
                <span className="rounded-xl bg-gradient-gold p-2 text-slate-950 shadow-glow-gold">
                  <Crown size={18} />
                </span>
                <div>
                  <Dialog.Title className="text-lg font-bold text-white">
                    PitchPilot Plus
                  </Dialog.Title>
                  <p className="text-xs text-slate-400">
                    Remove every limit, keep every cue.
                  </p>
                </div>
              </div>
              <Dialog.Close className="rounded-lg border border-white/10 p-1.5 text-slate-400 transition-colors hover:text-slate-100">
                <X size={14} />
              </Dialog.Close>
            </div>
          </div>

          <div className="max-h-[calc(86vh-6rem)] overflow-y-auto px-6 py-5">
            {alreadyPlus ? (
              <div className="flex flex-col items-center gap-3 py-6 text-center">
                <span className="relative rounded-2xl bg-amber-500/10 p-4 text-amber-400">
                  <span className="absolute inset-0 animate-ping-soft rounded-2xl bg-amber-400/20" />
                  <Sparkles size={26} />
                </span>
                <p className="text-sm font-semibold text-slate-100">
                  You're already on Plus - unlimited everything.
                </p>
                <p className="text-xs text-slate-500">
                  Your subscription is active - no new charge was made.
                </p>
                <Dialog.Close asChild>
                  <button type="button" className="btn-primary mt-2">
                    Back to coaching
                  </button>
                </Dialog.Close>
              </div>
            ) : (
              <>
                {reason != null && reason.length > 0 && (
                  <p className="mb-4 rounded-xl border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-xs text-amber-300">
                    {reason}
                  </p>
                )}

                <div className="flex items-baseline gap-2">
                  <span className="text-4xl font-bold text-white">
                    {priceDisplay}
                  </span>
                  <span className="text-sm text-slate-400">/ month</span>
                </div>
                <p className="mt-1.5 text-xs text-slate-500">
                  Free plan: {freeSessions} coaching sessions and {freeScripts}{" "}
                  scripts per month. Plus removes every limit.
                </p>

                <ul className="mt-5 flex flex-col gap-2.5">
                  {FEATURES.map((feature) => (
                    <li
                      key={feature}
                      className="flex items-start gap-2.5 text-sm text-slate-300"
                    >
                      <CheckCircle2
                        size={15}
                        className="mt-0.5 shrink-0 text-emerald-400"
                      />
                      {feature}
                    </li>
                  ))}
                </ul>

                {error !== null && (
                  <p className="mt-4 rounded-xl border border-rose-500/30 bg-rose-500/10 px-3 py-2 text-xs text-rose-300">
                    {error}
                  </p>
                )}

                <button
                  onClick={() => void upgrade()}
                  disabled={busy}
                  className="btn-gold btn-sheen mt-5 w-full py-3"
                >
                  {busy ? (
                    <Loader2 size={16} className="animate-spin" />
                  ) : (
                    <CreditCard size={16} />
                  )}
                  {`Upgrade to Plus - ${priceDisplay}/mo`}
                </button>
                <p className="mt-2.5 text-center text-[11px] text-slate-600">
                  Secure checkout · cancel anytime
                </p>
              </>
            )}
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
