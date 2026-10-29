/**
 * SettingsForm — overlay + coaching preferences (persisted via AppContext).
 * Includes a live preview of the overlay so slider changes are tangible.
 */

import { useEffect, useRef, useState, type FormEvent, type ReactNode } from "react";
import * as Slider from "@radix-ui/react-slider";
import * as Switch from "@radix-ui/react-switch";
import {
  AlertTriangle,
  ArrowLeft,
  Check,
  Download,
  Eye,
  Gauge,
  KeyRound,
  Loader2,
  Mail,
  ShieldCheck,
  Sparkles,
  Timer,
  Trash2,
  Type,
} from "lucide-react";

import { useApp } from "@/context/AppContext";
import { useAuth } from "@/context/AuthContext";
import { LegalLinks } from "@/components/LegalLinks";
import { Logo } from "@/components/ui/Logo";
import { ApiError, authApi } from "@/lib/api";
import { cn } from "@/lib/utils";
import type { CueSensitivity } from "@/types";

function Row({
  icon: Icon,
  title,
  description,
  children,
}: {
  icon: typeof Eye;
  title: string;
  description: string;
  children: ReactNode;
}): JSX.Element {
  return (
    <div className="flex flex-col gap-4 rounded-2xl border border-white/[0.08] bg-white/[0.03] p-4 backdrop-blur-xl sm:flex-row sm:items-center sm:justify-between sm:gap-6">
      <div className="flex items-start gap-3">
        <span className="mt-0.5 rounded-xl border border-white/10 bg-white/[0.04] p-2 text-amber-300">
          <Icon size={15} />
        </span>
        <div>
          <p className="text-sm font-semibold text-slate-100">{title}</p>
          <p className="mt-0.5 text-xs leading-relaxed text-slate-500">
            {description}
          </p>
        </div>
      </div>
      <div className="w-full shrink-0 sm:w-48">{children}</div>
    </div>
  );
}

function SliderControl({
  value,
  onChange,
  min,
  max,
  step = 1,
}: {
  value: number;
  onChange: (value: number) => void;
  min: number;
  max: number;
  step?: number;
}): JSX.Element {
  return (
    <Slider.Root
      min={min}
      max={max}
      step={step}
      value={[value]}
      onValueChange={([next]) => onChange(next)}
      className="relative flex h-5 w-full touch-none select-none items-center"
    >
      <Slider.Track className="relative h-1.5 w-full grow rounded-full bg-white/10">
        <Slider.Range className="absolute h-full rounded-full bg-gradient-brand" />
      </Slider.Track>
      <Slider.Thumb className="block h-4 w-4 rounded-full border-2 border-white bg-amber-500 shadow-glow transition-transform hover:scale-110 focus:outline-none focus:ring-4 focus:ring-amber-500/30" />
    </Slider.Root>
  );
}

function SensitivitySelector({
  value,
  onChange,
}: {
  value: CueSensitivity;
  onChange: (value: CueSensitivity) => void;
}): JSX.Element {
  const options: Array<{ value: CueSensitivity; label: string }> = [
    { value: "calm", label: "Calm" },
    { value: "balanced", label: "Balanced" },
    { value: "coach", label: "Coach" },
  ];
  return (
    <div className="grid grid-cols-3 gap-1 rounded-xl border border-white/5 bg-white/[0.03] p-1">
      {options.map((option) => (
        <button
          key={option.value}
          type="button"
          onClick={() => onChange(option.value)}
          className={cn(
            "rounded-lg px-2 py-1.5 text-xs font-medium transition-all",
            value === option.value
              ? "bg-gradient-brand text-slate-950 shadow-glow"
              : "text-slate-400 hover:text-slate-100",
          )}
        >
          {option.label}
        </button>
      ))}
    </div>
  );
}

export function SettingsForm(): JSX.Element {
  const { settings, updateSettings, setView } = useApp();
  const { user, changePassword, logout } = useAuth();
  const [saved, setSaved] = useState(false);
  const firstRenderRef = useRef(true);

  const [exporting, setExporting] = useState(false);
  const [accountInfo, setAccountInfo] = useState<string | null>(null);
  const [accountError, setAccountError] = useState<string | null>(null);
  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [passwordBusy, setPasswordBusy] = useState(false);
  const [passwordError, setPasswordError] = useState<string | null>(null);
  const [passwordInfo, setPasswordInfo] = useState<string | null>(null);
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [deletePassword, setDeletePassword] = useState("");
  const [deleteBusy, setDeleteBusy] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);

  // Flash a "saved" indicator whenever settings change (after first mount).
  useEffect(() => {
    if (firstRenderRef.current) {
      firstRenderRef.current = false;
      return;
    }
    setSaved(true);
    const timer = window.setTimeout(() => setSaved(false), 1400);
    return () => window.clearTimeout(timer);
  }, [settings]);

  const downloadData = async (): Promise<void> => {
    setExporting(true);
    setAccountInfo(null);
    setAccountError(null);
    try {
      const data = await authApi.exportData();
      const blob = new Blob([JSON.stringify(data, null, 2)], {
        type: "application/json",
      });
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = `pitchpilot-export-${new Date().toISOString().slice(0, 10)}.json`;
      document.body.appendChild(link);
      link.click();
      link.remove();
      window.setTimeout(() => URL.revokeObjectURL(url), 1000);
      setAccountInfo("Your data export has downloaded.");
    } catch (err) {
      setAccountError(
        err instanceof ApiError
          ? err.message
          : "Could not prepare your export — please try again.",
      );
    } finally {
      setExporting(false);
    }
  };

  const updatePassword = async (
    event: FormEvent<HTMLFormElement>,
  ): Promise<void> => {
    event.preventDefault();
    if (passwordBusy) return;
    setPasswordError(null);
    setPasswordInfo(null);

    if (newPassword.length < 12) {
      setPasswordError("Use at least 12 characters for your new password.");
      return;
    }
    if (newPassword !== confirmPassword) {
      setPasswordError("Those new passwords do not match.");
      return;
    }
    if (newPassword === currentPassword) {
      setPasswordError("Choose a password you haven't used for this account.");
      return;
    }

    setPasswordBusy(true);
    try {
      await changePassword(currentPassword, newPassword);
      setCurrentPassword("");
      setNewPassword("");
      setConfirmPassword("");
      setPasswordInfo(
        "Password updated. Other devices have been signed out.",
      );
    } catch (err) {
      setPasswordError(
        err instanceof ApiError
          ? err.message
          : "Could not update your password — please try again.",
      );
    } finally {
      setPasswordBusy(false);
    }
  };

  const deleteAccount = async (event: FormEvent<HTMLFormElement>): Promise<void> => {
    event.preventDefault();
    if (deleteBusy || deletePassword.length === 0) return;
    setDeleteBusy(true);
    setDeleteError(null);
    try {
      await authApi.deleteAccount(deletePassword);
      logout();
    } catch (err) {
      setDeleteError(
        err instanceof ApiError
          ? err.message
          : "Could not delete your account — please try again.",
      );
      setDeleteBusy(false);
    }
  };

  return (
    <div className="mx-auto w-full max-w-2xl px-4 pb-20 pt-6">
      <header className="mb-6 flex items-center gap-3">
        <button onClick={() => setView("dashboard")} className="btn-outline text-xs">
          <ArrowLeft size={14} /> Dashboard
        </button>
        <div className="flex items-center gap-2">
          <Logo size={30} />
          <h1 className="text-base font-bold text-white">Settings</h1>
        </div>
        <span
          className={cn(
            "ml-auto flex items-center gap-1.5 rounded-full border border-emerald-400/30 bg-emerald-400/10 px-2.5 py-1 text-xs font-medium text-emerald-300 transition-all",
            saved ? "opacity-100" : "translate-y-1 opacity-0",
          )}
        >
          <Check size={13} /> Saved
        </span>
      </header>

      {/* ── Live preview ── */}
      <section className="mb-6">
        <h2 className="mb-3 text-xs font-semibold uppercase tracking-widest text-slate-400">
          Preview
        </h2>
        <div className="relative overflow-hidden rounded-2xl border border-white/10 bg-gradient-to-br from-amber-500/10 via-amber-500/10 to-transparent p-6">
          <div
            className="mx-auto max-w-[320px] rounded-2xl border border-white/10 p-3 shadow-panel backdrop-blur-xl transition-colors"
            style={{ backgroundColor: `rgba(6, 10, 22, ${settings.overlayOpacity})` }}
          >
            <div className="flex items-center gap-2 text-[10px] font-semibold uppercase tracking-widest text-slate-400">
              <span className="relative flex h-2 w-2">
                <span className="absolute inset-0 animate-ping-soft rounded-full bg-emerald-400" />
                <span className="relative h-2 w-2 rounded-full bg-emerald-400" />
              </span>
              Live
              <span className="ml-auto font-mono text-slate-300">2:14</span>
            </div>
            <p className="mt-2 text-[12.5px] text-slate-200">
              ⚠ Ease off — you're at{" "}
              <span className="font-semibold text-amber-300">178 WPM</span>
            </p>
            <p className="mt-1 text-[11px] text-slate-400">
              fillers: 4 · next up: pricing
            </p>
            {settings.showTranscriptTicker && (
              <p className="mt-2 line-clamp-1 border-t border-white/5 pt-2 text-[10px] italic text-amber-300/70">
                “...so the pricing tiers start at forty nine dollars...”
              </p>
            )}
          </div>
        </div>
      </section>

      <section className="flex flex-col gap-3">
        <h2 className="text-xs font-semibold uppercase tracking-widest text-slate-400">
          Overlay
        </h2>

        <Row
          icon={Eye}
          title="Overlay opacity"
          description="How solid the live overlay card appears. Lower = more translucent."
        >
          <SliderControl
            min={50}
            max={95}
            value={Math.round(settings.overlayOpacity * 100)}
            onChange={(v) => updateSettings({ overlayOpacity: v / 100 })}
          />
          <p className="mt-1 text-right text-xs tabular-nums text-slate-400">
            {Math.round(settings.overlayOpacity * 100)}%
          </p>
        </Row>

        <Row
          icon={Timer}
          title="Cue display time"
          description="How long each coaching cue stays on the overlay before fading out."
        >
          <SliderControl
            min={3}
            max={15}
            value={settings.cueDismissSeconds}
            onChange={(v) => updateSettings({ cueDismissSeconds: v })}
          />
          <p className="mt-1 text-right text-xs tabular-nums text-slate-400">
            {settings.cueDismissSeconds}s
          </p>
        </Row>

        <Row
          icon={Type}
          title="Transcript ticker"
          description="Show a rolling one-line transcript at the bottom of the overlay."
        >
          <div className="flex justify-end">
            <Switch.Root
              checked={settings.showTranscriptTicker}
              onCheckedChange={(checked) =>
                updateSettings({ showTranscriptTicker: checked })
              }
              className="relative h-6 w-11 rounded-full bg-white/10 transition-colors data-[state=checked]:bg-gradient-brand"
            >
              <Switch.Thumb className="block h-5 w-5 translate-x-0.5 rounded-full bg-white shadow transition-transform will-change-transform data-[state=checked]:translate-x-[22px]" />
            </Switch.Root>
          </div>
        </Row>
      </section>

      <section className="mt-6 flex flex-col gap-3">
        <h2 className="text-xs font-semibold uppercase tracking-widest text-slate-400">
          Coaching
        </h2>

        <Row
          icon={Sparkles}
          title="Cue sensitivity"
          description="How often the coach speaks up. Calm = rare, high-confidence cues; Coach = constant feedback for practice."
        >
          <SensitivitySelector
            value={settings.cueSensitivity}
            onChange={(value) => updateSettings({ cueSensitivity: value })}
          />
          <p className="mt-1 text-right text-xs capitalize text-slate-400">
            {settings.cueSensitivity}
          </p>
        </Row>

        <Row
          icon={Gauge}
          title="Default target pace"
          description="Used when a session has no script selected. Scripts can override this."
        >
          <SliderControl
            min={80}
            max={220}
            step={5}
            value={settings.targetWpmDefault}
            onChange={(v) => updateSettings({ targetWpmDefault: v })}
          />
          <p className="mt-1 text-right text-xs tabular-nums text-slate-400">
            {settings.targetWpmDefault} WPM
          </p>
        </Row>
      </section>

      <section className="mt-6 flex flex-col gap-3">
        <h2 className="text-xs font-semibold uppercase tracking-widest text-slate-400">
          Account
        </h2>

        <div className="rounded-2xl border border-white/[0.08] bg-white/[0.03] p-4 backdrop-blur-xl">
          <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
            <div className="flex items-start gap-3">
              <span className="mt-0.5 rounded-xl border border-white/10 bg-white/[0.04] p-2 text-amber-300">
                <Mail size={15} />
              </span>
              <div>
                <p className="text-sm font-semibold text-slate-100">
                  {user?.email ?? "Signed in"}
                </p>
                <p className="mt-0.5 text-xs text-slate-500">
                  {user?.email_verified ? "Email verified" : "Email verification pending"}
                </p>
              </div>
            </div>
            <button
              type="button"
              onClick={() => void downloadData()}
              disabled={exporting}
              className="btn-outline text-xs"
            >
              {exporting ? (
                <Loader2 size={14} className="animate-spin" />
              ) : (
                <Download size={14} />
              )}
              Download my data
            </button>
          </div>

          {accountInfo !== null && (
            <p className="mt-3 flex items-center gap-2 rounded-xl border border-emerald-400/25 bg-emerald-400/10 px-3 py-2 text-xs text-emerald-200">
              <Check size={13} /> {accountInfo}
            </p>
          )}
          {accountError !== null && (
            <p className="mt-3 rounded-xl border border-rose-500/30 bg-rose-500/10 px-3 py-2 text-xs text-rose-300">
              {accountError}
            </p>
          )}
        </div>

        <div className="rounded-2xl border border-white/[0.08] bg-white/[0.03] p-4 backdrop-blur-xl">
          <div className="flex items-start gap-3">
            <span className="mt-0.5 rounded-xl border border-white/10 bg-white/[0.04] p-2 text-amber-300">
              <KeyRound size={15} />
            </span>
            <div>
              <p className="text-sm font-semibold text-slate-100">Password</p>
              <p className="mt-0.5 text-xs leading-relaxed text-slate-500">
                Use a unique password. Changing it signs out every other device
                and revokes live connection links.
              </p>
            </div>
          </div>

          <form onSubmit={updatePassword} className="mt-4 flex flex-col gap-3">
            <label className="block">
              <span className="field-label">Current password</span>
              <input
                type="password"
                required
                autoComplete="current-password"
                value={currentPassword}
                onChange={(event) => setCurrentPassword(event.target.value)}
                placeholder="Your current password"
                className="input"
              />
            </label>
            <div className="grid gap-3 sm:grid-cols-2">
              <label className="block">
                <span className="field-label">New password</span>
                <input
                  type="password"
                  required
                  minLength={12}
                  autoComplete="new-password"
                  value={newPassword}
                  onChange={(event) => setNewPassword(event.target.value)}
                  placeholder="At least 12 characters"
                  className="input"
                />
              </label>
              <label className="block">
                <span className="field-label">Confirm new password</span>
                <input
                  type="password"
                  required
                  minLength={12}
                  autoComplete="new-password"
                  value={confirmPassword}
                  onChange={(event) => setConfirmPassword(event.target.value)}
                  placeholder="Repeat new password"
                  className="input"
                />
              </label>
            </div>

            {passwordInfo !== null && (
              <p className="flex items-center gap-2 rounded-xl border border-emerald-400/25 bg-emerald-400/10 px-3 py-2 text-xs text-emerald-200">
                <ShieldCheck size={13} /> {passwordInfo}
              </p>
            )}
            {passwordError !== null && (
              <p className="rounded-xl border border-rose-500/30 bg-rose-500/10 px-3 py-2 text-xs text-rose-300">
                {passwordError}
              </p>
            )}

            <div className="flex justify-end">
              <button
                type="submit"
                disabled={
                  passwordBusy ||
                  currentPassword.length === 0 ||
                  newPassword.length < 12 ||
                  confirmPassword.length < 12
                }
                className="btn-outline text-xs"
              >
                {passwordBusy ? (
                  <Loader2 size={14} className="animate-spin" />
                ) : (
                  <KeyRound size={14} />
                )}
                Update password
              </button>
            </div>
          </form>
        </div>

        <div className="rounded-2xl border border-rose-500/20 bg-rose-500/[0.04] p-4 backdrop-blur-xl">
          <div className="flex items-start gap-3">
            <span className="mt-0.5 rounded-xl border border-rose-400/20 bg-rose-400/10 p-2 text-rose-300">
              <AlertTriangle size={15} />
            </span>
            <div>
              <p className="text-sm font-semibold text-slate-100">Delete account</p>
              <p className="mt-0.5 text-xs leading-relaxed text-slate-500">
                Permanently deletes your scripts, sessions, transcripts, coaching
                history, and billing history. Any active subscription is cancelled
                first.
              </p>
            </div>
          </div>

          {!deleteOpen ? (
            <button
              type="button"
              onClick={() => {
                setDeleteOpen(true);
                setDeleteError(null);
              }}
              className="mt-4 inline-flex items-center gap-1.5 rounded-xl border border-rose-400/30 px-3 py-2 text-xs font-semibold text-rose-300 transition-colors hover:bg-rose-500/10"
            >
              <Trash2 size={13} /> Delete my account
            </button>
          ) : (
            <form onSubmit={deleteAccount} className="mt-4 flex flex-col gap-2">
              <label
                htmlFor="delete-password"
                className="text-[11px] font-medium text-slate-400"
              >
                Confirm with your password
              </label>
              <input
                id="delete-password"
                type="password"
                required
                autoComplete="current-password"
                value={deletePassword}
                onChange={(event) => setDeletePassword(event.target.value)}
                placeholder="Current password"
                className="input"
              />
              {deleteError !== null && (
                <p className="rounded-xl border border-rose-500/30 bg-rose-500/10 px-3 py-2 text-xs text-rose-300">
                  {deleteError}
                </p>
              )}
              <div className="flex flex-col gap-2 sm:flex-row">
                <button
                  type="submit"
                  disabled={deleteBusy || deletePassword.length === 0}
                  className="inline-flex items-center justify-center gap-1.5 rounded-xl border border-rose-400/30 bg-rose-500/10 px-3 py-2 text-xs font-semibold text-rose-300 transition-colors hover:bg-rose-500/20 disabled:cursor-not-allowed disabled:opacity-50"
                >
                  {deleteBusy ? (
                    <Loader2 size={13} className="animate-spin" />
                  ) : (
                    <Trash2 size={13} />
                  )}
                  Delete permanently
                </button>
                <button
                  type="button"
                  onClick={() => {
                    setDeleteOpen(false);
                    setDeletePassword("");
                    setDeleteError(null);
                  }}
                  className="btn-outline text-xs"
                >
                  Cancel
                </button>
              </div>
            </form>
          )}
        </div>
      </section>

      <p className="mt-6 text-xs leading-relaxed text-slate-600">
        Coaching adapts to your pace and delivery as you speak.
      </p>
      <LegalLinks className="mt-3" />
    </div>
  );
}
