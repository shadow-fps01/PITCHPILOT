/**
 * AppContext — global SPA state: active view, active session/script and
 * user-tunable overlay settings (persisted to localStorage).
 */

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import type { AppSettings, Script, ViewMode } from "@/types";

interface AppContextValue {
  view: ViewMode;
  setView: (view: ViewMode) => void;
  activeSessionId: string | null;
  setActiveSessionId: (id: string | null) => void;
  activeScript: Script | null;
  setActiveScript: (script: Script | null) => void;
  settings: AppSettings;
  updateSettings: (patch: Partial<AppSettings>) => void;
  /** Increment to tell the Dashboard to refetch its session list. */
  sessionsVersion: number;
  refreshSessions: () => void;
}

const AppContext = createContext<AppContextValue | undefined>(undefined);

const SETTINGS_STORAGE_KEY = "pitchpilot.settings";

const DEFAULT_SETTINGS: AppSettings = {
  overlayOpacity: 0.82,
  targetWpmDefault: 140,
  cueDismissSeconds: 8,
  showTranscriptTicker: true,
  cueSensitivity: "balanced",
};

function loadSettings(): AppSettings {
  try {
    const raw = localStorage.getItem(SETTINGS_STORAGE_KEY);
    if (raw !== null) {
      const parsed = JSON.parse(raw) as Partial<AppSettings>;
      return { ...DEFAULT_SETTINGS, ...parsed };
    }
  } catch {
    /* corrupted entry — fall back to defaults */
  }
  return DEFAULT_SETTINGS;
}

/** Initial view: support `?view=live` for detached overlay windows. */
function initialViewFromQuery(): ViewMode {
  try {
    const param = new URLSearchParams(window.location.search).get("view");
    return param === "live" ? "live" : "dashboard";
  } catch {
    return "dashboard";
  }
}

export function AppProvider({
  children,
  initialView,
}: {
  children: ReactNode;
  /** Overrides `?view=` detection — used by the PiP overlay root. */
  initialView?: ViewMode;
}): JSX.Element {
  const [view, setView] = useState<ViewMode>(initialView ?? initialViewFromQuery);
  const [activeSessionId, setActiveSessionId] = useState<string | null>(null);
  const [activeScript, setActiveScript] = useState<Script | null>(null);
  const [settings, setSettings] = useState<AppSettings>(loadSettings);
  const [sessionsVersion, setSessionsVersion] = useState(0);

  useEffect(() => {
    try {
      localStorage.setItem(SETTINGS_STORAGE_KEY, JSON.stringify(settings));
    } catch {
      /* storage unavailable — settings stay session-only */
    }
  }, [settings]);

  const updateSettings = useCallback((patch: Partial<AppSettings>): void => {
    setSettings((current) => ({ ...current, ...patch }));
  }, []);

  const refreshSessions = useCallback((): void => {
    setSessionsVersion((v) => v + 1);
  }, []);

  const value = useMemo<AppContextValue>(
    () => ({
      view,
      setView,
      activeSessionId,
      setActiveSessionId,
      activeScript,
      setActiveScript,
      settings,
      updateSettings,
      sessionsVersion,
      refreshSessions,
    }),
    [
      view,
      activeSessionId,
      activeScript,
      settings,
      updateSettings,
      sessionsVersion,
      refreshSessions,
    ],
  );

  return <AppContext.Provider value={value}>{children}</AppContext.Provider>;
}

export function useApp(): AppContextValue {
  const ctx = useContext(AppContext);
  if (ctx === undefined) {
    throw new Error("useApp must be used within an AppProvider");
  }
  return ctx;
}
