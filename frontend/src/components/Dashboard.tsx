/**
 * Dashboard — script management, session history and the "start coaching"
 * launchpad. Organised with Radix Tabs: Coach / Scripts / History / Plus.
 *
 * Visual system: glass panels over the aurora backdrop, a gradient hero
 * launchpad, animated usage meters and tone-coded stat tiles.
 */

import { useCallback, useEffect, useState, type FormEvent } from "react";
import * as Tabs from "@radix-ui/react-tabs";
import * as Dialog from "@radix-ui/react-dialog";
import * as Slider from "@radix-ui/react-slider";
import {
  Activity,
  BadgeCheck,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  Clock,
  Crown,
  ExternalLink,
  FileText,
  Gauge,
  History,
  Infinity as InfinityIcon,
  ListChecks,
  Loader2,
  LogOut,
  MessageSquareWarning,
  Mic,
  Pencil,
  Play,
  Plus,
  Settings,
  Sparkles,
  Timer,
  Trash2,
  X,
} from "lucide-react";

import { useAuth } from "@/context/AuthContext";
import { useApp } from "@/context/AppContext";
import { ApiError, billingApi, scriptsApi, sessionsApi } from "@/lib/api";
import { openPipOverlay, pipSupported } from "@/lib/pipOverlay";
import { AnalyticsCard } from "@/components/AnalyticsCard";
import { LegalLinks } from "@/components/LegalLinks";
import { UpgradeDialog } from "@/components/UpgradeDialog";
import { Logo } from "@/components/ui/Logo";
import { Waveform } from "@/components/ui/Waveform";
import { cn, formatDate, formatDuration, formatTimeAgo } from "@/lib/utils";
import type {
  BillingEvent,
  BillingStatus,
  CoachingSession,
  Script,
  SessionDetail,
} from "@/types";

interface ScriptFormState {
  title: string;
  outline: string;
  targetWpm: number;
  keywordsText: string;
}

const EMPTY_FORM: ScriptFormState = {
  title: "",
  outline: "",
  targetWpm: 140,
  keywordsText: "",
};

function parseKeywords(text: string): string[] {
  return text
    .split(",")
    .map((k) => k.trim())
    .filter((k) => k.length > 0)
    .slice(0, 30);
}

/** Friendly label for a payment source (avoids leaking internal names). */
function providerLabel(provider: string): string {
  if (provider === "paystack") return "Card";
  if (provider === "demo") return "Included";
  return "Payment";
}

/** Animated, gradient usage meter for the free-plan quotas. */
function UsageMeter({
  label,
  used,
  total,
}: {
  label: string;
  used: number;
  total: number;
}): JSX.Element {
  const pct = Math.min(100, total === 0 ? 0 : (used / total) * 100);
  const full = used >= total;
  return (
    <div>
      <div className="mb-1.5 flex items-center justify-between text-[11px] font-medium text-slate-400">
        <span>{label}</span>
        <span className="tabular-nums">
          <span className={cn("font-semibold", full ? "text-rose-300" : "text-slate-200")}>
            {used}
          </span>
          <span className="text-slate-600"> / {total}</span>
        </span>
      </div>
      <div className="h-2 w-full overflow-hidden rounded-full bg-white/[0.06]">
        <div
          className={cn(
            "h-full rounded-full transition-[width] duration-700 ease-out",
            full ? "bg-gradient-to-r from-rose-400 to-rose-500" : "bg-gradient-brand",
          )}
          style={{ width: `${pct}%` }}
        />
      </div>
    </div>
  );
}

export function Dashboard(): JSX.Element {
  const { user, logout, refreshUser } = useAuth();
  const { setView, setActiveSessionId, setActiveScript, sessionsVersion } =
    useApp();

  const [scripts, setScripts] = useState<Script[]>([]);
  const [scriptsLoading, setScriptsLoading] = useState(true);
  const [selectedScriptId, setSelectedScriptId] = useState<string>("");

  const [sessions, setSessions] = useState<CoachingSession[]>([]);
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [expandedDetail, setExpandedDetail] = useState<SessionDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);

  const [dialogOpen, setDialogOpen] = useState(false);
  const [editingScript, setEditingScript] = useState<Script | null>(null);
  const [form, setForm] = useState<ScriptFormState>(EMPTY_FORM);
  const [formError, setFormError] = useState<string | null>(null);
  const [formBusy, setFormBusy] = useState(false);

  const [starting, setStarting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // ── Billing (PitchPilot Plus) ──
  const [billing, setBilling] = useState<BillingStatus | null>(null);
  const [billingEvents, setBillingEvents] = useState<BillingEvent[]>([]);
  const [activeTab, setActiveTab] = useState("coach");
  const [upgradeOpen, setUpgradeOpen] = useState(false);
  const [upgradeReason, setUpgradeReason] = useState<string | null>(null);
  const [cancelBusy, setCancelBusy] = useState(false);
  const [billingNotice, setBillingNotice] = useState<string | null>(null);

  const loadBilling = useCallback(async (): Promise<BillingStatus | null> => {
    try {
      const status = await billingApi.usage();
      setBilling(status);
      return status;
    } catch {
      return null;
    }
  }, []);

  const loadBillingEvents = useCallback(async (): Promise<void> => {
    try {
      const response = await billingApi.events(50);
      setBillingEvents(response.events);
    } catch {
      /* payment history is non-critical */
    }
  }, []);

  /** Shared post-upgrade refresh: plan status, user object and history. */
  const afterPlanChange = useCallback((): void => {
    setBillingNotice(null);
    void loadBilling();
    void loadBillingEvents();
    void refreshUser();
  }, [loadBilling, loadBillingEvents, refreshUser]);

  // Keep the signed-in user object in sync once billing confirms Plus. This
  // covers every activation path — the verify callback, the webhook and
  // server-side reconciliation after a payment whose redirect was lost.
  useEffect(() => {
    if (billing?.is_plus && user !== null && !user.is_plus) {
      void refreshUser();
    }
  }, [billing?.is_plus, user, refreshUser]);

  // Returning from Paystack's hosted checkout: verify activation.
  // Paystack can't web-hook a localhost host, so first confirm the payment
  // directly via the transaction reference it appended to the callback URL,
  // then poll usage briefly while the webhook (if reachable) lands.
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const billingParam = params.get("billing");
    const reference = params.get("reference") ?? params.get("trxref");
    if (billingParam === null) return;
    window.history.replaceState({}, "", window.location.pathname);

    const check = async (): Promise<void> => {
      for (let attempt = 0; attempt < 6; attempt += 1) {
        // Attempt a direct Paystack verification (idempotent, works without
        // webhooks) as soon as we have a transaction reference.
        if (reference !== null) {
          try {
            const verification = await billingApi.verify(reference);
            if (verification.billing.is_plus) {
              setBilling(verification.billing);
              setBillingNotice(
                "Payment received — welcome to PitchPilot Plus! Every limit is now lifted.",
              );
              setActiveTab("plus");
              void refreshUser();
              void loadBillingEvents();
              return;
            }
          } catch {
            /* fall back to the usage poll below */
          }
        }
        const status = await loadBilling();
        if (status !== null && status.is_plus) {
          setBillingNotice(
            "Payment received — welcome to PitchPilot Plus! Every limit is now lifted.",
          );
          setActiveTab("plus");
          void refreshUser();
          void loadBillingEvents();
          return;
        }
        if (attempt < 5) {
          await new Promise((resolve) => setTimeout(resolve, 1500));
        }
      }
      setBillingNotice(
        "Your payment is still being confirmed — Plus activates automatically within a minute.",
      );
    };

    void check();
  }, [loadBilling, loadBillingEvents, refreshUser]);

  useEffect(() => {
    void loadBilling();
    void loadBillingEvents();
  }, [loadBilling, loadBillingEvents]);

  const loadScripts = useCallback(async (): Promise<void> => {
    try {
      setScripts(await scriptsApi.list());
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to load scripts");
    } finally {
      setScriptsLoading(false);
    }
  }, []);

  const loadSessions = useCallback(async (): Promise<void> => {
    try {
      const response = await sessionsApi.list(50);
      setSessions(response.sessions);
    } catch {
      /* history is non-critical */
    }
  }, []);

  useEffect(() => {
    void loadScripts();
  }, [loadScripts]);

  useEffect(() => {
    void loadSessions();
  }, [loadSessions, sessionsVersion]);

  const startCoaching = useCallback(async (): Promise<void> => {
    if (starting) return;
    setStarting(true);
    setError(null);
    try {
      const script =
        selectedScriptId.length > 0
          ? (scripts.find((s) => s.id === selectedScriptId) ?? null)
          : null;
      const session = await sessionsApi.create(script?.id ?? null);
      setActiveScript(script);
      setActiveSessionId(session.id);
      setView("live");
      void loadBilling(); // keep the free-plan usage meter fresh
    } catch (err) {
      if (err instanceof ApiError && err.status === 402) {
        setUpgradeReason(err.message);
        setUpgradeOpen(true);
      } else {
        setError(err instanceof ApiError ? err.message : "Could not start a session");
      }
    } finally {
      setStarting(false);
    }
  }, [
    starting,
    selectedScriptId,
    scripts,
    setActiveScript,
    setActiveSessionId,
    setView,
    loadBilling,
  ]);

  const openOverlayWindow = useCallback((): void => {
    // Preferred: an always-on-top Document-PiP overlay — never captured when
    // the call shares a tab/window (Chrome/Edge). Fallback: a popup window.
    void openPipOverlay().then((opened) => {
      if (!opened) {
        window.open(
          `${window.location.origin}/?view=live`,
          "pitchpilot-overlay",
          "popup=yes,width=460,height=780",
        );
      }
    });
  }, []);

  const openNewScriptDialog = useCallback((): void => {
    setEditingScript(null);
    setForm(EMPTY_FORM);
    setFormError(null);
    setDialogOpen(true);
  }, []);

  const openEditScriptDialog = useCallback((script: Script): void => {
    setEditingScript(script);
    setForm({
      title: script.title,
      outline: script.content_outline,
      targetWpm: script.target_wpm,
      keywordsText: script.keywords.join(", "),
    });
    setFormError(null);
    setDialogOpen(true);
  }, []);

  const submitScriptForm = useCallback(
    async (event: FormEvent<HTMLFormElement>): Promise<void> => {
      event.preventDefault();
      if (formBusy) return;
      setFormBusy(true);
      setFormError(null);
      try {
        const payload = {
          title: form.title,
          content_outline: form.outline,
          target_wpm: form.targetWpm,
          keywords: parseKeywords(form.keywordsText),
        };
        if (editingScript === null) {
          await scriptsApi.create(payload);
        } else {
          await scriptsApi.update(editingScript.id, payload);
        }
        setDialogOpen(false);
        await loadScripts();
        void loadBilling(); // free-plan script quota
      } catch (err) {
        if (
          err instanceof ApiError &&
          err.status === 402 &&
          editingScript === null
        ) {
          setDialogOpen(false);
          setUpgradeReason(err.message);
          setUpgradeOpen(true);
        } else {
          setFormError(
            err instanceof ApiError ? err.message : "Could not save the script",
          );
        }
      } finally {
        setFormBusy(false);
      }
    },
    [formBusy, form, editingScript, loadScripts],
  );

  const deleteScript = useCallback(
    async (script: Script): Promise<void> => {
      if (window.confirm(`Delete "${script.title}"? This cannot be undone.`)) {
        try {
          await scriptsApi.remove(script.id);
          if (selectedScriptId === script.id) setSelectedScriptId("");
          await loadScripts();
        } catch (err) {
          setError(err instanceof ApiError ? err.message : "Could not delete script");
        }
      }
    },
    [loadScripts, selectedScriptId],
  );

  /** Cancel the Plus subscription (Paystack: at period end; demo: now). */
  const cancelPlus = useCallback(async (): Promise<void> => {
    if (cancelBusy) return;
    if (
      !window.confirm(
        "Cancel PitchPilot Plus? You'll keep Plus until the end of the paid period.",
      )
    ) {
      return;
    }
    setCancelBusy(true);
    try {
      const status = await billingApi.cancel();
      setBilling(status);
      setBillingNotice(
        status.is_plus
          ? "Subscription cancelled — Plus stays active until the period ends."
          : "Subscription cancelled — you're back on the free plan.",
      );
      void refreshUser();
      void loadBillingEvents();
    } catch (err) {
      setError(
        err instanceof ApiError ? err.message : "Could not cancel the subscription",
      );
    } finally {
      setCancelBusy(false);
    }
  }, [cancelBusy, refreshUser, loadBillingEvents]);

  const toggleSessionDetail = useCallback(
    async (session: CoachingSession): Promise<void> => {
      if (expandedId === session.id) {
        setExpandedId(null);
        setExpandedDetail(null);
        return;
      }
      setExpandedId(session.id);
      setExpandedDetail(null);
      setDetailLoading(true);
      try {
        setExpandedDetail(await sessionsApi.detail(session.id));
      } catch {
        setExpandedId(null);
      } finally {
        setDetailLoading(false);
      }
    },
    [expandedId],
  );

  const latestCompleted = expandedDetail?.summary ?? null;

  const scriptTitleById = useCallback(
    (id: string | null): string => {
      if (id === null) return "Freeform";
      return scripts.find((s) => s.id === id)?.title ?? "Deleted script";
    },
    [scripts],
  );

  const selectedScript =
    scripts.find((s) => s.id === selectedScriptId) ?? null;

  const TABS = [
    ["coach", "Coach", Play],
    ["scripts", "Scripts", FileText],
    ["history", "History", History],
    ["plus", "Plus", Crown],
  ] as const;

  return (
    <div className="mx-auto w-full max-w-5xl px-4 pb-20 pt-5 sm:px-6">
      {/* ── Sticky header ── */}
      <header className="sticky top-0 z-30 -mx-4 mb-6 border-b border-white/5 bg-ink-950/70 px-4 py-3 backdrop-blur-xl sm:-mx-6 sm:px-6">
        <div className="flex items-center justify-between gap-3">
          <div className="flex min-w-0 items-center gap-3">
            <Logo size={38} />
            <div className="min-w-0">
              <div className="flex items-center gap-2">
                <h1 className="text-base font-bold leading-tight text-white">
                  PitchPilot
                </h1>
                {(user?.is_plus ?? billing?.is_plus) === true && (
                  <span className="chip-gold px-2 py-0.5 text-[10px] font-bold uppercase tracking-wider">
                    <Crown size={10} /> Plus
                  </span>
                )}
              </div>
              <p className="truncate text-xs text-slate-500">{user?.email}</p>
            </div>
          </div>
          <div className="flex items-center gap-2">
            <button
              onClick={() => setView("settings")}
              title="Settings"
              className="btn-outline h-9 w-9 !px-0"
            >
              <Settings size={15} />
            </button>
            <button
              onClick={logout}
              title="Sign out"
              className="btn-outline h-9 !px-3 text-xs"
            >
              <LogOut size={15} />
              <span className="hidden sm:inline">Sign out</span>
            </button>
          </div>
        </div>
      </header>

      {error !== null && (
        <p className="mb-4 animate-scale-in rounded-xl border border-rose-500/30 bg-rose-500/10 px-3.5 py-2.5 text-xs text-rose-300">
          {error}
        </p>
      )}

      {billingNotice !== null && (
        <div className="mb-4 flex animate-rise items-start justify-between gap-3 rounded-xl border border-emerald-500/30 bg-emerald-500/10 px-3.5 py-2.5 text-xs text-emerald-200">
          <span className="flex items-center gap-2">
            <BadgeCheck size={14} className="shrink-0" /> {billingNotice}
          </span>
          <button
            onClick={() => setBillingNotice(null)}
            className="shrink-0 text-emerald-400/70 transition-colors hover:text-emerald-100"
            title="Dismiss"
          >
            <X size={14} />
          </button>
        </div>
      )}

      <Tabs.Root value={activeTab} onValueChange={setActiveTab}>
        <Tabs.List className="mb-6 flex w-full gap-1 overflow-x-auto rounded-2xl border border-white/[0.08] bg-white/[0.03] p-1.5 backdrop-blur-xl sm:w-auto sm:inline-flex">
          {TABS.map(([value, label, Icon]) => (
            <Tabs.Trigger
              key={value}
              value={value}
              className={cn(
                "flex flex-1 items-center justify-center gap-1.5 whitespace-nowrap rounded-xl px-4 py-2 text-sm font-medium transition-all duration-200 sm:flex-none",
                "data-[state=active]:bg-gradient-brand data-[state=active]:text-slate-950 data-[state=active]:shadow-glow",
                "text-slate-400 hover:text-slate-100",
              )}
            >
              <Icon size={14} /> {label}
            </Tabs.Trigger>
          ))}
        </Tabs.List>

        {/* ── Coach tab ── */}
        <Tabs.Content value="coach" className="animate-fade-in focus:outline-none">
          <section className="border-aurora panel animate-rise overflow-hidden p-6 sm:p-7">
            <div className="flex flex-wrap items-start justify-between gap-4">
              <div className="max-w-xl">
                <span className="chip-sky mb-3">
                  <Sparkles size={11} /> Live coaching
                </span>
                <h2 className="text-2xl font-bold text-white">
                  Start a coaching session
                </h2>
                <p className="mt-2 text-sm leading-relaxed text-slate-400">
                  Pick an outline so PitchPilot can remind you of missed talking
                  points, then start speaking. Cues appear only in this overlay;
                  share just the call tab or window so they stay out of the
                  shared view.
                </p>
              </div>
              <div className="hidden items-center gap-3 rounded-2xl border border-white/[0.08] bg-white/[0.03] px-4 py-3 sm:flex">
                <Waveform active={false} bars={6} />
                <div>
                  <p className="text-[10px] font-semibold uppercase tracking-widest text-slate-500">
                    Engine
                  </p>
                  <p className="text-xs font-medium text-slate-300">
                    pacing · fillers · copilot
                  </p>
                </div>
              </div>
            </div>

            <div className="mt-6 grid gap-3 sm:grid-cols-[1fr_auto] sm:items-end">
              <label className="block">
                <span className="field-label">Script / outline</span>
                <span className="relative block">
                  <select
                    value={selectedScriptId}
                    onChange={(e) => setSelectedScriptId(e.target.value)}
                    className="input cursor-pointer appearance-none pr-10"
                  >
                    <option value="">Freeform (no outline)</option>
                    {scripts.map((script) => (
                      <option key={script.id} value={script.id}>
                        {script.title} — {script.target_wpm} WPM ·{" "}
                        {script.keywords.length} keywords
                      </option>
                    ))}
                  </select>
                  <ChevronDown
                    size={15}
                    className="pointer-events-none absolute right-3 top-1/2 -translate-y-1/2 text-slate-500"
                  />
                </span>
              </label>

              <div className="flex flex-wrap gap-2">
                <button
                  onClick={() => void startCoaching()}
                  disabled={starting}
                  className="btn-primary btn-sheen h-[42px] px-6"
                >
                  {starting ? (
                    <Loader2 size={16} className="animate-spin" />
                  ) : (
                    <Mic size={16} />
                  )}
                  Start coaching
                </button>
                <button
                  onClick={openOverlayWindow}
                  className="btn-outline h-[42px]"
                  title={
                    pipSupported()
                      ? "Always-on-top overlay that stays out of tab/window screen shares — perfect beside a call. A whole-screen share will include it."
                      : "Open the overlay in its own small window — perfect beside a call"
                  }
                >
                  <ExternalLink size={15} />
                  {pipSupported() ? "Floating overlay" : "Detached window"}
                </button>
              </div>
            </div>

            {selectedScript !== null && selectedScript.keywords.length > 0 && (
              <div className="mt-3 flex flex-wrap gap-1.5">
                {selectedScript.keywords.slice(0, 8).map((keyword) => (
                  <span key={keyword} className="chip-sky">
                    {keyword}
                  </span>
                ))}
              </div>
            )}

            {scriptsLoading && (
              <p className="mt-3 flex items-center gap-1.5 text-xs text-slate-500">
                <Loader2 size={12} className="animate-spin" /> Loading scripts...
              </p>
            )}

            {/* ── Plan / quota ── */}
            {billing === null ? (
              <p className="mt-5 flex items-center gap-1.5 text-xs text-slate-600">
                <Loader2 size={12} className="animate-spin" /> Loading plan...
              </p>
            ) : billing.is_plus ? (
              <div className="mt-5 flex items-center gap-2.5 rounded-2xl border border-amber-400/25 bg-gradient-to-r from-amber-400/10 to-transparent px-4 py-3 text-xs text-amber-200">
                <InfinityIcon size={15} className="shrink-0" />
                <span>
                  <span className="font-semibold">PitchPilot Plus</span> —
                  unlimited sessions and scripts.
                  {billing.plus_cancel_at_period_end &&
                    billing.plus_renews_at !== null &&
                    " Cancels at the end of the current period."}
                </span>
              </div>
            ) : (
              <div className="mt-5 flex flex-wrap items-center justify-between gap-4 rounded-2xl border border-white/[0.08] bg-white/[0.02] px-4 py-3.5">
                <div className="grid min-w-[240px] flex-1 gap-3 sm:grid-cols-2">
                  <UsageMeter
                    label="Coaching sessions"
                    used={billing.sessions_used_this_month}
                    total={billing.free_monthly_sessions}
                  />
                  <UsageMeter
                    label="Scripts"
                    used={billing.scripts_used_this_month}
                    total={billing.free_monthly_scripts}
                  />
                </div>
                <button
                  onClick={() => {
                    setUpgradeReason(null);
                    setUpgradeOpen(true);
                  }}
                  className="btn-gold"
                >
                  <Crown size={13} /> Upgrade — {billing.price_display}/mo
                </button>
              </div>
            )}
          </section>

          {expandedDetail !== null && latestCompleted !== null && (
            <section className="mt-8 animate-rise">
              <h3 className="mb-3 text-xs font-semibold uppercase tracking-widest text-slate-400">
                Latest session report
              </h3>
              <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
                <AnalyticsCard
                  icon={Timer}
                  label="Duration"
                  value={formatDuration(latestCompleted.duration_seconds ?? 0)}
                />
                <AnalyticsCard
                  icon={MessageSquareWarning}
                  label="Filler alerts"
                  value={latestCompleted.total_filler_words}
                  tone="warn"
                />
                <AnalyticsCard
                  icon={Gauge}
                  label="Pacing alerts"
                  value={latestCompleted.pacing_alerts}
                  tone="info"
                />
                <AnalyticsCard
                  icon={ListChecks}
                  label="Missed points"
                  value={latestCompleted.script_misses}
                  tone="alert"
                />
              </div>
            </section>
          )}
        </Tabs.Content>

        {/* ── Scripts tab ── */}
        <Tabs.Content value="scripts" className="animate-fade-in focus:outline-none">
          <div className="mb-4 flex items-center justify-between">
            <div>
              <h2 className="text-lg font-bold text-white">Pitch outlines</h2>
              <p className="text-xs text-slate-500">
                {scripts.length} saved · audited live against your delivery
              </p>
            </div>
            <button onClick={openNewScriptDialog} className="btn-outline">
              <Plus size={15} /> New script
            </button>
          </div>

          {scripts.length === 0 ? (
            <div className="flex flex-col items-center gap-2 rounded-2xl border border-dashed border-white/10 px-4 py-14 text-center">
              <span className="rounded-2xl bg-white/[0.04] p-3 text-slate-400">
                <FileText size={20} />
              </span>
              <p className="max-w-sm text-sm text-slate-400">
                No scripts yet. Create one with your talking points and keywords —
                PitchPilot will audit your delivery against it live.
              </p>
              <button onClick={openNewScriptDialog} className="btn-primary mt-2">
                <Plus size={15} /> Create your first script
              </button>
            </div>
          ) : (
            <ul className="grid gap-3 sm:grid-cols-2">
              {scripts.map((script) => (
                <li
                  key={script.id}
                  className="panel panel-hover group relative overflow-hidden p-4"
                >
                  <span className="absolute -right-8 -top-8 h-24 w-24 rounded-full bg-gradient-brand opacity-[0.07] blur-2xl transition-opacity group-hover:opacity-20" />
                  <div className="flex items-start justify-between gap-3">
                    <div className="min-w-0">
                      <p className="truncate text-sm font-semibold text-slate-100">
                        {script.title}
                      </p>
                      <p className="mt-1 line-clamp-2 text-xs leading-relaxed text-slate-500">
                        {script.content_outline || "No outline content yet."}
                      </p>
                    </div>
                    <div className="flex shrink-0 gap-1.5 opacity-0 transition-opacity duration-200 group-hover:opacity-100">
                      <button
                        onClick={() => openEditScriptDialog(script)}
                        title="Edit"
                        className="rounded-lg border border-white/10 p-2 text-slate-400 transition-colors hover:border-sky-400/50 hover:text-sky-300"
                      >
                        <Pencil size={13} />
                      </button>
                      <button
                        onClick={() => void deleteScript(script)}
                        title="Delete"
                        className="rounded-lg border border-white/10 p-2 text-slate-400 transition-colors hover:border-rose-500/50 hover:text-rose-400"
                      >
                        <Trash2 size={13} />
                      </button>
                    </div>
                  </div>
                  <div className="mt-3 flex flex-wrap gap-1.5">
                    <span className="chip">
                      <Gauge size={10} /> {script.target_wpm} WPM
                    </span>
                    {script.keywords.slice(0, 5).map((keyword) => (
                      <span key={keyword} className="chip-sky">
                        {keyword}
                      </span>
                    ))}
                    {script.keywords.length > 5 && (
                      <span className="chip">+{script.keywords.length - 5}</span>
                    )}
                  </div>
                </li>
              ))}
            </ul>
          )}
        </Tabs.Content>

        {/* ── History tab ── */}
        <Tabs.Content value="history" className="animate-fade-in focus:outline-none">
          <h2 className="mb-4 text-lg font-bold text-white">Recent sessions</h2>
          {sessions.length === 0 ? (
            <p className="rounded-2xl border border-dashed border-white/10 px-4 py-14 text-center text-sm text-slate-500">
              No sessions yet. Your coaching history will appear here.
            </p>
          ) : (
            <ul className="flex flex-col gap-2.5">
              {sessions.map((session) => {
                const isExpanded = expandedId === session.id;
                const live = session.ended_at === null;
                return (
                  <li
                    key={session.id}
                    className={cn(
                      "overflow-hidden rounded-2xl border bg-white/[0.03] backdrop-blur-xl transition-colors",
                      isExpanded ? "border-sky-400/30" : "border-white/[0.08] hover:border-white/20",
                    )}
                  >
                    <button
                      onClick={() => void toggleSessionDetail(session)}
                      className="flex w-full items-center gap-3 px-4 py-3.5 text-left transition-colors hover:bg-white/[0.03]"
                    >
                      {isExpanded ? (
                        <ChevronDown size={15} className="shrink-0 text-slate-500" />
                      ) : (
                        <ChevronRight size={15} className="shrink-0 text-slate-500" />
                      )}
                      <span
                        className={cn(
                          "relative flex h-8 w-8 shrink-0 items-center justify-center rounded-xl",
                          live
                            ? "bg-emerald-500/10 text-emerald-300"
                            : "bg-white/[0.04] text-slate-400",
                        )}
                      >
                        {live && (
                          <span className="absolute inset-0 animate-ping-soft rounded-xl bg-emerald-400/20" />
                        )}
                        <Activity size={15} />
                      </span>
                      <span className="min-w-0 flex-1">
                        <span className="block truncate text-sm font-medium text-slate-100">
                          {scriptTitleById(session.script_id)}
                        </span>
                        <span className="text-xs text-slate-500">
                          {session.started_at === null
                            ? "Not started"
                            : formatTimeAgo(session.started_at)}
                        </span>
                      </span>
                      <span
                        className={cn(
                          "shrink-0 rounded-lg px-2.5 py-1 text-[10px] font-semibold uppercase tracking-wider",
                          live
                            ? "bg-emerald-500/10 text-emerald-300"
                            : "bg-white/[0.05] text-slate-400",
                        )}
                      >
                        {live
                          ? "Live"
                          : formatDuration(session.duration_seconds ?? 0)}
                      </span>
                    </button>

                    {isExpanded && (
                      <div className="animate-scale-in border-t border-white/[0.08] px-4 py-4">
                        {detailLoading && (
                          <p className="flex items-center gap-2 py-4 text-xs text-slate-500">
                            <Loader2 size={12} className="animate-spin" /> Loading
                            report...
                          </p>
                        )}
                        {expandedDetail !== null && (
                          <>
                            <div className="grid grid-cols-2 gap-2.5 sm:grid-cols-4 lg:grid-cols-5">
                              <AnalyticsCard
                                icon={Clock}
                                label="Duration"
                                value={formatDuration(
                                  expandedDetail.summary.duration_seconds ?? 0,
                                )}
                              />
                              <AnalyticsCard
                                icon={MessageSquareWarning}
                                label="Fillers"
                                value={expandedDetail.summary.total_filler_words}
                                tone="warn"
                              />
                              <AnalyticsCard
                                icon={Gauge}
                                label="Pacing"
                                value={expandedDetail.summary.pacing_alerts}
                                tone="info"
                              />
                              <AnalyticsCard
                                icon={ListChecks}
                                label="Missed"
                                value={expandedDetail.summary.script_misses}
                                tone="alert"
                              />
                              <AnalyticsCard
                                icon={Sparkles}
                                label="Cards"
                                value={expandedDetail.summary.context_cards}
                                tone="success"
                              />
                            </div>
                            {expandedDetail.events.length === 0 ? (
                              <p className="mt-3 text-xs text-slate-500">
                                No coaching events were triggered in this session.
                              </p>
                            ) : (
                              <ul className="mt-3 max-h-60 space-y-1.5 overflow-y-auto pr-1">
                                {expandedDetail.events.map((event) => (
                                  <li
                                    key={event.id}
                                    className="flex items-start gap-2.5 rounded-xl bg-white/[0.03] px-3 py-2 text-xs"
                                  >
                                    <span className="shrink-0 pt-0.5 font-mono text-[10px] text-sky-300/80">
                                      {formatDuration(event.timestamp)}
                                    </span>
                                    <span className="text-slate-300">
                                      {event.message}
                                    </span>
                                  </li>
                                ))}
                              </ul>
                            )}
                          </>
                        )}
                      </div>
                    )}
                  </li>
                );
              })}
            </ul>
          )}
        </Tabs.Content>

        {/* ── Plus tab ── */}
        <Tabs.Content value="plus" className="animate-fade-in focus:outline-none">
          <h2 className="mb-4 text-lg font-bold text-white">Plan &amp; billing</h2>

          {billing === null ? (
            <p className="flex items-center gap-2 rounded-2xl border border-dashed border-white/10 px-4 py-14 text-sm text-slate-500">
              <Loader2 size={14} className="animate-spin" /> Loading plan...
            </p>
          ) : (
            <div className="grid gap-4 md:grid-cols-2">
              <section className="panel p-6">
                <div className="flex items-center justify-between">
                  <h3 className="text-sm font-semibold text-slate-100">
                    Current plan
                  </h3>
                  <span
                    className={cn(
                      "inline-flex items-center gap-1 rounded-full px-2.5 py-1 text-[10px] font-bold uppercase tracking-wider",
                      billing.is_plus
                        ? "bg-amber-400/10 text-amber-300"
                        : "bg-white/[0.06] text-slate-400",
                    )}
                  >
                    {billing.is_plus && <Crown size={10} />}
                    {billing.is_plus ? "Plus" : "Free"}
                  </span>
                </div>

                {billing.is_plus ? (
                  <ul className="mt-5 flex flex-col gap-3 text-xs text-slate-400">
                    <li className="flex items-center gap-2.5">
                      <InfinityIcon size={14} className="text-amber-300" />
                      Unlimited coaching sessions
                    </li>
                    <li className="flex items-center gap-2.5">
                      <InfinityIcon size={14} className="text-amber-300" />
                      Unlimited scripts
                    </li>
                    {billing.plus_renews_at !== null && (
                      <li className="flex items-center gap-2.5">
                        <Clock size={14} className="text-slate-500" />
                        {billing.plus_cancel_at_period_end ? "Access ends" : "Renews"}{" "}
                        on {formatDate(billing.plus_renews_at)}
                      </li>
                    )}
                    <li className="flex items-center gap-2.5">
                      <BadgeCheck size={14} className="text-emerald-400" />
                      {billing.price_display}/month · Secure checkout
                    </li>
                  </ul>
                ) : (
                  <>
                    <p className="mt-2 text-xs text-slate-500">
                      {billing.free_monthly_sessions} sessions and{" "}
                      {billing.free_monthly_scripts} scripts per month, resets on the
                      1st.
                    </p>
                    <div className="mt-5 flex flex-col gap-4">
                      <UsageMeter
                        label="Coaching sessions"
                        used={billing.sessions_used_this_month}
                        total={billing.free_monthly_sessions}
                      />
                      <UsageMeter
                        label="Scripts"
                        used={billing.scripts_used_this_month}
                        total={billing.free_monthly_scripts}
                      />
                    </div>
                  </>
                )}
              </section>

              <section
                className={cn(
                  "relative overflow-hidden rounded-2xl border p-6",
                  billing.is_plus
                    ? "border-amber-400/30 bg-gradient-to-b from-amber-400/10 to-transparent"
                    : "border-white/10 bg-white/[0.035]",
                )}
              >
                <div className="pointer-events-none absolute -right-10 -top-10 h-32 w-32 rounded-full bg-amber-400/20 blur-3xl" />
                <div className="flex items-center gap-2">
                  <span className="rounded-xl bg-gradient-gold p-1.5 text-slate-950">
                    <Crown size={16} />
                  </span>
                  <h3 className="text-sm font-semibold text-slate-100">
                    PitchPilot Plus
                  </h3>
                </div>
                <div className="mt-4 flex items-baseline gap-2">
                  <span className="text-4xl font-bold text-white">
                    {billing.price_display}
                  </span>
                  <span className="text-sm text-slate-400">/ month</span>
                </div>
                <ul className="mt-5 flex flex-col gap-2.5 text-xs text-slate-300">
                  <li className="flex items-center gap-2">
                    <CheckCircle2 size={14} className="text-emerald-400" />
                    Unlimited coaching sessions
                  </li>
                  <li className="flex items-center gap-2">
                    <CheckCircle2 size={14} className="text-emerald-400" />
                    Unlimited scripts &amp; outlines
                  </li>
                  <li className="flex items-center gap-2">
                    <CheckCircle2 size={14} className="text-emerald-400" />
                    Live cues, pacing &amp; next-topic cards
                  </li>
                </ul>

                {billing.is_plus ? (
                  <button
                    onClick={() => void cancelPlus()}
                    disabled={cancelBusy || billing.plus_cancel_at_period_end}
                    className="btn-outline mt-6 w-full"
                  >
                    {cancelBusy ? (
                      <>
                        <Loader2 size={13} className="animate-spin" /> Cancelling...
                      </>
                    ) : billing.plus_cancel_at_period_end ? (
                      "Already cancelling at period end"
                    ) : (
                      "Cancel subscription"
                    )}
                  </button>
                ) : (
                  <button
                    onClick={() => {
                      setUpgradeReason(null);
                      setUpgradeOpen(true);
                    }}
                    className="btn-gold btn-sheen mt-6 w-full"
                  >
                    Upgrade to Plus
                  </button>
                )}
                <p className="mt-2.5 text-center text-[11px] text-slate-600">
                  {billing.provider === "paystack"
                    ? "Secure checkout · cancel anytime"
                    : "No card required"}
                </p>
              </section>
            </div>
          )}

          <section className="mt-8">
            <h3 className="mb-3 text-xs font-semibold uppercase tracking-widest text-slate-400">
              Payment &amp; plan history
            </h3>
            {billingEvents.length === 0 ? (
              <p className="rounded-2xl border border-dashed border-white/10 px-4 py-10 text-center text-xs text-slate-500">
                No payments or plan changes yet.
              </p>
            ) : (
              <ul className="flex flex-col gap-2">
                {billingEvents.map((event) => (
                  <li
                    key={event.id}
                    className="flex flex-wrap items-center justify-between gap-2 rounded-xl border border-white/[0.08] bg-white/[0.03] px-4 py-3"
                  >
                    <div className="min-w-0">
                      <p className="text-xs font-medium text-slate-200">
                        {event.message ?? event.event_type}
                      </p>
                      <p className="mt-0.5 text-[11px] text-slate-500">
                        {providerLabel(event.provider)}
                        {event.reference !== null && ` · ${event.reference}`}
                      </p>
                    </div>
                    <div className="flex shrink-0 items-center gap-3 text-[11px] text-slate-500">
                      {event.amount !== null && (
                        <span className="font-semibold tabular-nums text-slate-200">
                          {(event.amount / 100).toFixed(2)} {event.currency ?? ""}
                        </span>
                      )}
                      <span>{formatTimeAgo(event.created_at)}</span>
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </section>
        </Tabs.Content>
      </Tabs.Root>

      <LegalLinks className="mt-8 justify-center" />

      {/* ── Script editor dialog ── */}
      <Dialog.Root open={dialogOpen} onOpenChange={setDialogOpen}>
        <Dialog.Portal>
          <Dialog.Overlay className="fixed inset-0 z-40 bg-ink-950/80 backdrop-blur-sm" />
          <Dialog.Content className="panel fixed left-1/2 top-1/2 z-50 max-h-[86vh] w-[calc(100%-2rem)] max-w-lg -translate-x-1/2 -translate-y-1/2 animate-scale-in overflow-y-auto p-6">
            <div className="mb-5 flex items-start justify-between">
              <div>
                <Dialog.Title className="text-lg font-bold text-white">
                  {editingScript === null ? "New script" : "Edit script"}
                </Dialog.Title>
                <p className="mt-0.5 text-xs text-slate-500">
                  Talking points, pace target and keywords to watch.
                </p>
              </div>
              <Dialog.Close className="rounded-lg border border-white/10 p-1.5 text-slate-400 transition-colors hover:text-slate-100">
                <X size={14} />
              </Dialog.Close>
            </div>

            <form onSubmit={submitScriptForm} className="flex flex-col gap-4">
              <label className="block">
                <span className="field-label">Title</span>
                <input
                  required
                  maxLength={200}
                  value={form.title}
                  onChange={(e) => setForm({ ...form, title: e.target.value })}
                  placeholder="Acme investor pitch — 5 minutes"
                  className="input"
                />
              </label>

              <label className="block">
                <span className="field-label">Talking points / outline</span>
                <textarea
                  rows={6}
                  value={form.outline}
                  onChange={(e) => setForm({ ...form, outline: e.target.value })}
                  placeholder={
                    "1. Problem: founders lose deals to rambling\n2. Solution: live overlay cues\n3. Traction: 40 pilots signed\n4. Pricing: $49/seat/month with annual discount"
                  }
                  className="input resize-y font-mono text-xs leading-relaxed"
                />
              </label>

              <div>
                <div className="mb-2 flex items-center justify-between">
                  <span className="field-label !mb-0">Target pace</span>
                  <span className="text-xs font-semibold tabular-nums text-sky-300">
                    {form.targetWpm} WPM
                  </span>
                </div>
                <Slider.Root
                  min={80}
                  max={220}
                  step={5}
                  value={[form.targetWpm]}
                  onValueChange={([value]) => setForm({ ...form, targetWpm: value })}
                  className="relative flex h-5 w-full touch-none select-none items-center"
                >
                  <Slider.Track className="relative h-1.5 w-full grow rounded-full bg-white/10">
                    <Slider.Range className="absolute h-full rounded-full bg-gradient-brand" />
                  </Slider.Track>
                  <Slider.Thumb className="block h-4 w-4 rounded-full border-2 border-white bg-sky-500 shadow-glow transition-transform hover:scale-110 focus:outline-none focus:ring-4 focus:ring-sky-500/30" />
                </Slider.Root>
              </div>

              <label className="block">
                <span className="field-label">Keywords to watch (comma-separated)</span>
                <input
                  value={form.keywordsText}
                  onChange={(e) => setForm({ ...form, keywordsText: e.target.value })}
                  placeholder="pricing, discount, traction, roadmap, the ask"
                  className="input"
                />
              </label>

              {formError !== null && (
                <p className="rounded-xl border border-rose-500/30 bg-rose-500/10 px-3 py-2 text-xs text-rose-300">
                  {formError}
                </p>
              )}

              <div className="mt-1 flex justify-end gap-2">
                <Dialog.Close asChild>
                  <button type="button" className="btn-ghost">
                    Cancel
                  </button>
                </Dialog.Close>
                <button type="submit" disabled={formBusy} className="btn-primary">
                  {formBusy && <Loader2 size={14} className="animate-spin" />}
                  {editingScript === null ? "Create script" : "Save changes"}
                </button>
              </div>
            </form>
          </Dialog.Content>
        </Dialog.Portal>
      </Dialog.Root>

      {/* ── Upgrade paywall dialog ── */}
      <UpgradeDialog
        open={upgradeOpen}
        onOpenChange={setUpgradeOpen}
        reason={upgradeReason}
        priceDisplay={billing?.price_display ?? "₦22,500"}
        freeSessions={billing?.free_monthly_sessions ?? 3}
        freeScripts={billing?.free_monthly_scripts ?? 3}
        paymentsLive={billing?.payments_live ?? false}
        onUpgraded={afterPlanChange}
      />
    </div>
  );
}
