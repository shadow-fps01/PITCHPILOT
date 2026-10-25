/**
 * AuthContext — JWT session state for the SPA.
 * Token persists in localStorage; the user is re-hydrated via GET /auth/me.
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
import { authApi, getStoredToken, setStoredToken } from "@/lib/api";
import type { User } from "@/types";

interface AuthContextValue {
  user: User | null;
  token: string | null;
  /** True during the initial restore-from-storage check. */
  initializing: boolean;
  login: (email: string, password: string) => Promise<void>;
  register: (email: string, password: string) => Promise<void>;
  logout: () => void;
  /** Re-fetch the current user (plan state can change after checkout). */
  refreshUser: () => Promise<void>;
  /** Record permission to capture/transcribe the call. */
  acceptConsent: () => Promise<void>;
  /** Transient account message (e.g. the result of an email-verification link). */
  notice: string | null;
  dismissNotice: () => void;
}

const AuthContext = createContext<AuthContextValue | undefined>(undefined);

export function AuthProvider({ children }: { children: ReactNode }): JSX.Element {
  const [user, setUser] = useState<User | null>(null);
  const [token, setToken] = useState<string | null>(null);
  const [initializing, setInitializing] = useState(true);
  const [notice, setNotice] = useState<string | null>(null);

  // Handle the ?verify=<token> link from the verification email.
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const verificationToken = params.get("verify");
    if (verificationToken === null) return;

    // Strip the token from the address bar immediately (no replay on refresh).
    params.delete("verify");
    const query = params.toString();
    window.history.replaceState(
      {},
      "",
      `${window.location.pathname}${query.length > 0 ? `?${query}` : ""}${window.location.hash}`,
    );

    let cancelled = false;
    authApi
      .verifyEmail(verificationToken)
      .then(() => {
        if (cancelled) return;
        setNotice("Email verified — you're all set.");
        if (getStoredToken() !== null) {
          authApi
            .me()
            .then((me) => {
              if (!cancelled) setUser(me);
            })
            .catch(() => {
              /* token stale — the restore effect handles it */
            });
        }
      })
      .catch(() => {
        if (!cancelled) {
          setNotice("That verification link is invalid or has expired.");
        }
      });
    return () => {
      cancelled = true;
    };
  }, []);

  // Restore an existing session on first mount. A password-reset link must
  // work even in a browser that still holds an old (soon-to-be-invalid) token.
  useEffect(() => {
    let cancelled = false;
    const resetToken = new URLSearchParams(window.location.search).get("reset");
    if (resetToken !== null && resetToken.length > 0) {
      setStoredToken(null);
      setToken(null);
      setUser(null);
      setInitializing(false);
      return;
    }
    const stored = getStoredToken();
    if (stored === null) {
      setInitializing(false);
      return;
    }
    setToken(stored);
    authApi
      .me()
      .then((me) => {
        if (!cancelled) setUser(me);
      })
      .catch(() => {
        // Invalid/expired token — clear it.
        setStoredToken(null);
        if (!cancelled) {
          setToken(null);
          setUser(null);
        }
      })
      .finally(() => {
        if (!cancelled) setInitializing(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const login = useCallback(async (email: string, password: string): Promise<void> => {
    const response = await authApi.login(email, password);
    setStoredToken(response.access_token);
    setToken(response.access_token);
    setUser(response.user);
  }, []);

  const register = useCallback(async (email: string, password: string): Promise<void> => {
    const response = await authApi.register(email, password);
    setStoredToken(response.access_token);
    setToken(response.access_token);
    setUser(response.user);
  }, []);

  const logout = useCallback((): void => {
    setStoredToken(null);
    setToken(null);
    setUser(null);
    setNotice(null);
  }, []);

  const refreshUser = useCallback(async (): Promise<void> => {
    try {
      setUser(await authApi.me());
    } catch {
      /* token invalid — leave state untouched */
    }
  }, []);

  const acceptConsent = useCallback(async (): Promise<void> => {
    setUser(await authApi.consent());
  }, []);

  const dismissNotice = useCallback((): void => setNotice(null), []);

  const value = useMemo<AuthContextValue>(
    () => ({
      user,
      token,
      initializing,
      login,
      register,
      logout,
      refreshUser,
      acceptConsent,
      notice,
      dismissNotice,
    }),
    [
      user,
      token,
      initializing,
      login,
      register,
      logout,
      refreshUser,
      acceptConsent,
      notice,
      dismissNotice,
    ],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (ctx === undefined) {
    throw new Error("useAuth must be used within an AuthProvider");
  }
  return ctx;
}
