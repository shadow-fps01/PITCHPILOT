/** Login / registration / password recovery — brand panel + glass auth card. */

import { useEffect, useState, type FormEvent } from "react";
import * as Tabs from "@radix-ui/react-tabs";
import {
  ArrowLeft,
  AtSign,
  CheckCircle2,
  Eye,
  EyeOff,
  KeyRound,
  Loader2,
  LogIn,
  Mail,
  ShieldCheck,
  Sparkles,
  UserPlus,
  Wand2,
} from "lucide-react";

import { useAuth } from "@/context/AuthContext";
import { ApiError, authApi } from "@/lib/api";
import { Logo } from "@/components/ui/Logo";
import { Waveform } from "@/components/ui/Waveform";
import { cn } from "@/lib/utils";

type AuthMode = "login" | "register" | "forgot" | "reset";

const HIGHLIGHTS = [
  {
    icon: Sparkles,
    title: "Cues only you can see",
    body: "Pacing, filler and script-miss nudges float over your call — never on the shared screen.",
  },
  {
    icon: Wand2,
    title: "A copilot that answers",
    body: "It hears the call and tells you exactly what to say next, in the moment.",
  },
  {
    icon: ShieldCheck,
    title: "Ready in seconds",
    body: "Start coaching right away — no setup or configuration required.",
  },
];

function resetTokenFromUrl(): string | null {
  try {
    const token = new URLSearchParams(window.location.search).get("reset");
    return token !== null && token.length > 0 ? token : null;
  } catch {
    return null;
  }
}

export function AuthForm(): JSX.Element {
  const { login, register } = useAuth();
  const [initialResetToken] = useState<string | null>(resetTokenFromUrl);
  const [mode, setMode] = useState<AuthMode>(
    initialResetToken === null ? "login" : "reset",
  );
  // Hidden until we know the deployment can actually deliver the link. This
  // avoids promising an email while account email is switched off.
  const [emailAvailable, setEmailAvailable] = useState<boolean | null>(null);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [info, setInfo] = useState<string | null>(null);

  // Strip the single-use reset token from the address bar once read. Wait a
  // tick so AuthProvider's restore effect (parent effects run after child
  // effects) can see the token and drop any stale signed-in session first.
  useEffect(() => {
    if (initialResetToken === null) return;
    const timer = window.setTimeout(() => {
      const params = new URLSearchParams(window.location.search);
      if (!params.has("reset")) return;
      params.delete("reset");
      const query = params.toString();
      window.history.replaceState(
        {},
        "",
        `${window.location.pathname}${query.length > 0 ? `?${query}` : ""}${window.location.hash}`,
      );
    }, 0);
    return () => window.clearTimeout(timer);
  }, [initialResetToken]);

  useEffect(() => {
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
  }, []);

  const isAux = mode === "forgot" || mode === "reset";
  const showEmail = mode !== "reset";
  const showPasswordField = mode !== "forgot";

  function switchMode(next: AuthMode): void {
    setMode(next);
    setError(null);
    setInfo(null);
    setPassword("");
    setConfirmPassword("");
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    if (busy) return;
    setError(null);
    setInfo(null);

    if (mode === "forgot" && emailAvailable === false) {
      setError("Password recovery is temporarily unavailable. Please try again later.");
      return;
    }

    if (mode === "reset") {
      if (initialResetToken === null) {
        setError("This reset link is invalid or has expired.");
        return;
      }
      if (password !== confirmPassword) {
        setError("Those passwords do not match.");
        return;
      }
    }

    setBusy(true);
    try {
      if (mode === "login") {
        await login(email, password);
      } else if (mode === "register") {
        await register(email, password);
      } else if (mode === "forgot") {
        await authApi.forgotPassword(email);
        setInfo(
          "If that email has an account, we've sent a reset link. Check your inbox and spam folder.",
        );
      } else {
        await authApi.resetPassword(initialResetToken!, password);
        setInfo("Password updated. Sign in with your new password.");
        setMode("login");
        setPassword("");
        setConfirmPassword("");
      }
    } catch (err) {
      setError(
        err instanceof ApiError
          ? err.message
          : "Could not reach PitchPilot — check your connection and try again.",
      );
    } finally {
      setBusy(false);
    }
  }

  const submitLabel =
    mode === "login"
      ? "Sign in"
      : mode === "register"
        ? "Create account"
        : mode === "forgot"
          ? "Send reset link"
          : "Update password";

  const SubmitIcon =
    mode === "login"
      ? LogIn
      : mode === "register"
        ? UserPlus
        : mode === "forgot"
          ? Mail
          : KeyRound;

  return (
    <main className="flex min-h-full items-center justify-center px-4 py-10">
      <div className="grid w-full max-w-5xl items-center gap-10 lg:grid-cols-[1.05fr_0.95fr]">
        {/* ── Brand panel ─────────────────────────────────────────── */}
        <section className="hidden animate-rise lg:block">
          <div className="flex items-center gap-3">
            <Logo size={46} halo />
            <span className="text-sm font-semibold uppercase tracking-[0.35em] text-slate-300">
              PitchPilot
            </span>
          </div>

          <h1 className="mt-8 text-4xl font-bold leading-[1.1] text-balance text-white">
            Pitch with the calm of a{" "}
            <span className="text-gradient">coached pro</span>.
          </h1>
          <p className="mt-4 max-w-md text-[15px] leading-relaxed text-slate-400">
            A just-in-time live speaking coach that listens to your call,
            reads the room, and whispers the next right thing — only to you.
          </p>

          {/* live "listening" preview */}
          <div className="panel mt-8 flex items-center gap-4 p-4">
            <span className="relative flex h-11 w-11 shrink-0 items-center justify-center rounded-xl bg-gradient-copilot">
              <span className="absolute inset-0 animate-ping-soft rounded-xl bg-sky-400/20" />
              <Waveform active level={0.68} bars={5} />
            </span>
            <div className="min-w-0">
              <p className="text-xs font-semibold uppercase tracking-widest text-sky-300">
                Listening
              </p>
              <p className="truncate text-sm text-slate-300">
                “Say this — lead with the renewal number, then pause.”
              </p>
            </div>
          </div>

          <ul className="mt-8 flex flex-col gap-4">
            {HIGHLIGHTS.map(({ icon: Icon, title, body }) => (
              <li key={title} className="flex items-start gap-3">
                <span className="mt-0.5 rounded-xl border border-white/10 bg-white/[0.04] p-2 text-sky-300">
                  <Icon size={16} />
                </span>
                <div>
                  <p className="text-sm font-semibold text-slate-100">{title}</p>
                  <p className="text-[13px] leading-relaxed text-slate-500">
                    {body}
                  </p>
                </div>
              </li>
            ))}
          </ul>
        </section>

        {/* ── Auth card ───────────────────────────────────────────── */}
        <section className="animate-scale-in">
          <div className="mb-6 flex flex-col items-center gap-3 text-center lg:hidden">
            <Logo size={48} halo />
            <div>
              <h1 className="text-2xl font-bold tracking-tight text-white">
                PitchPilot
              </h1>
              <p className="mt-1 text-sm text-slate-400">
                Your just-in-time live pitch coach.
              </p>
            </div>
          </div>

          <div className="border-aurora panel p-6 sm:p-7">
            {isAux ? (
              <div className="mb-5">
                <button
                  type="button"
                  onClick={() => switchMode("login")}
                  className="mb-4 inline-flex items-center gap-1.5 text-xs font-medium text-slate-400 transition-colors hover:text-slate-100"
                >
                  <ArrowLeft size={13} /> Back to sign in
                </button>
                <h2 className="text-lg font-bold text-white">
                  {mode === "forgot" ? "Reset your password" : "Choose a new password"}
                </h2>
                <p className="mt-1 text-xs leading-relaxed text-slate-500">
                  {mode === "forgot"
                    ? "Enter the email you signed up with and we'll send a single-use reset link."
                    : "Use a password you haven't used elsewhere. This link can only be used once."}
                </p>
                {mode === "forgot" && emailAvailable === false && (
                  <p className="mt-2 rounded-xl border border-amber-300/20 bg-amber-300/10 px-3 py-2 text-xs leading-relaxed text-amber-100/80">
                    Password recovery is temporarily unavailable. Please try again
                    later.
                  </p>
                )}
              </div>
            ) : (
              <Tabs.Root
                value={mode === "register" ? "register" : "login"}
                onValueChange={(value) => switchMode(value as AuthMode)}
              >
                <Tabs.List className="mb-5 grid grid-cols-2 gap-1 rounded-xl border border-white/5 bg-white/[0.03] p-1">
                  {(
                    [
                      ["login", "Sign in", LogIn],
                      ["register", "Create account", UserPlus],
                    ] as const
                  ).map(([value, label, Icon]) => (
                    <Tabs.Trigger
                      key={value}
                      value={value}
                      className={cn(
                        "flex items-center justify-center gap-1.5 rounded-lg px-3 py-2 text-sm font-medium transition-all duration-200",
                        mode === value
                          ? "bg-gradient-brand text-slate-950 shadow-glow"
                          : "text-slate-400 hover:text-slate-100",
                      )}
                    >
                      <Icon size={14} /> {label}
                    </Tabs.Trigger>
                  ))}
                </Tabs.List>
              </Tabs.Root>
            )}

            <form onSubmit={handleSubmit} className="flex flex-col gap-3.5">
              {showEmail && (
                <label className="relative block">
                  <span className="field-label">Email</span>
                  <span className="relative block">
                    <AtSign
                      size={15}
                      className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-500"
                    />
                    <input
                      type="email"
                      required
                      autoComplete="email"
                      placeholder="you@company.com"
                      value={email}
                      onChange={(e) => setEmail(e.target.value)}
                      className="input input-icon"
                    />
                  </span>
                </label>
              )}

              {showPasswordField && (
                <label className="relative block">
                  <span className="flex items-center justify-between">
                    <span className="field-label !mb-0">Password</span>
                    {mode === "login" && emailAvailable === true && (
                      <button
                        type="button"
                        onClick={() => switchMode("forgot")}
                        className="mb-1.5 text-[11px] font-medium text-sky-300 transition-colors hover:text-sky-200"
                      >
                        Forgot password?
                      </button>
                    )}
                  </span>
                  <span className="relative mt-1.5 block">
                    <KeyRound
                      size={15}
                      className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-500"
                    />
                    <input
                      type={showPassword ? "text" : "password"}
                      required
                      minLength={8}
                      autoComplete={
                        mode === "login" ? "current-password" : "new-password"
                      }
                      placeholder={
                        mode === "login" ? "Your password" : "At least 8 characters"
                      }
                      value={password}
                      onChange={(e) => setPassword(e.target.value)}
                      className="input pl-10 pr-10"
                    />
                    <button
                      type="button"
                      onClick={() => setShowPassword((v) => !v)}
                      title={showPassword ? "Hide password" : "Show password"}
                      className="absolute right-2.5 top-1/2 -translate-y-1/2 rounded-md p-1 text-slate-500 transition-colors hover:text-slate-200"
                    >
                      {showPassword ? <EyeOff size={15} /> : <Eye size={15} />}
                    </button>
                  </span>
                </label>
              )}

              {mode === "reset" && (
                <label className="relative block">
                  <span className="field-label">Confirm new password</span>
                  <span className="relative block">
                    <ShieldCheck
                      size={15}
                      className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-500"
                    />
                    <input
                      type={showPassword ? "text" : "password"}
                      required
                      minLength={8}
                      autoComplete="new-password"
                      placeholder="Repeat your new password"
                      value={confirmPassword}
                      onChange={(e) => setConfirmPassword(e.target.value)}
                      className="input pl-10"
                    />
                  </span>
                </label>
              )}

              {info !== null && (
                <p className="flex animate-scale-in items-start gap-2 rounded-xl border border-emerald-400/25 bg-emerald-400/10 px-3 py-2 text-xs leading-relaxed text-emerald-200">
                  <CheckCircle2 size={14} className="mt-0.5 shrink-0" />
                  <span>{info}</span>
                </p>
              )}

              {error !== null && (
                <p className="animate-scale-in rounded-xl border border-rose-500/30 bg-rose-500/10 px-3 py-2 text-xs leading-relaxed text-rose-300">
                  {error}
                </p>
              )}

              <button
                type="submit"
                disabled={busy}
                className="btn-primary btn-sheen mt-1 w-full"
              >
                {busy ? (
                  <Loader2 size={16} className="animate-spin" />
                ) : (
                  <SubmitIcon size={16} />
                )}
                {submitLabel}
              </button>
            </form>

            {mode === "register" && (
              <p className="mt-4 text-center text-[11px] leading-relaxed text-slate-500">
                You'll confirm that you have permission to record the call before
                your first coaching session.
              </p>
            )}
          </div>

          <p className="mt-4 text-center text-xs text-slate-600">
            {isAux
              ? "Reset links are single-use and expire quickly."
              : "Cues stay private to you — your screen share stays clean."}
          </p>
        </section>
      </div>
    </main>
  );
}
