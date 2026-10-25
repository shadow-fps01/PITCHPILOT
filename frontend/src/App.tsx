/**
 * App — auth gate + view routing (Dashboard / live Overlay / Settings).
 * The live overlay also boots standalone via `?view=live` for the detached
 * overlay window (it reuses the token stored in localStorage).
 */

import { useAuth } from "@/context/AuthContext";
import { useApp } from "@/context/AppContext";
import { AccountNotice } from "@/components/AccountNotice";
import { AuthForm } from "@/components/AuthForm";
import { ConsentGate } from "@/components/ConsentGate";
import { Dashboard } from "@/components/Dashboard";
import { OverlayWidget } from "@/components/OverlayWidget";
import { SettingsForm } from "@/components/SettingsForm";
import { Aurora } from "@/components/ui/Aurora";
import { Logo } from "@/components/ui/Logo";

function Splash(): JSX.Element {
  return (
    <>
      <Aurora />
      <div className="flex min-h-full flex-col items-center justify-center gap-4">
        <Logo size={56} halo />
        <div className="flex items-center gap-1.5">
          <span className="text-xs font-semibold uppercase tracking-[0.35em] text-slate-400">
            PitchPilot
          </span>
        </div>
        <span className="h-1 w-24 overflow-hidden rounded-full bg-white/10">
          <span className="block h-full w-1/2 animate-shimmer rounded-full bg-gradient-brand bg-[length:200%_100%]" />
        </span>
      </div>
    </>
  );
}

function Shell(): JSX.Element {
  const { user, initializing } = useAuth();
  const { view } = useApp();

  if (initializing) return <Splash />;

  if (user === null) {
    return (
      <>
        <Aurora />
        <AuthForm />
      </>
    );
  }

  // The overlay owns its own (transparent) canvas — it must stay share-safe.
  if (view === "live") return <OverlayWidget />;

  return (
    <>
      <Aurora />
      {view === "settings" ? <SettingsForm /> : <Dashboard />}
    </>
  );
}

export default function App(): JSX.Element {
  return (
    <div className="relative min-h-full text-slate-100">
      <Shell />
      <AccountNotice />
      <ConsentGate />
    </div>
  );
}
