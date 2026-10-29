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
import { clearUrlToken, readUrlToken } from "@/lib/utils";
import type { MessageResponse, User } from "@/types";

interface AuthContextValue {
  user: User | null;
  token: string | null;
  /** True during the initial restore-from-storage check. */
  initializing: boolean;
  login: (email: string, password: string) => Promise<void>;
  /** Register and return the backend's generic confirmation message. */
  register: (email: string, password: string) => Promise<MessageResponse>;
  /** Change the password and replace the current session token. */
  changePassword: (currentPassword: string, newPassword: string) => Promise<void>;
  /** Revoke this session server-side, then clear local auth state. */
  logout: () => Promise<void>;
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

  // Handle the #verify=<token> fragment from the verification email.
  useEffect(() => {
    const verificationToken = readUrlToken("verify");
    if (verificationToken === null) return;

    // Strip the token from the address bar immediately (no replay on refresh).
    clearUrlToken("verify");

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
    const resetToken = readUrlToken("reset");
    if (resetToken !== null) {
      clearUrlToken("reset");
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

  const register = useCallback(
    async (email: string, password: string): Promise<MessageResponse> =>
      authApi.register(email, password),
    [],
  );

  const changePassword = useCallback(
    async (currentPassword: string, newPassword: string): Promise<void> => {
      const response = await authApi.changePassword(currentPassword, newPassword);
      setStoredToken(response.access_token);
      setToken(response.access_token);
      setUser(response.user);
    },
    [],
  );

  const logout = useCallback(async (): Promise<void> => {
    try {
      // Revoke the token server-side so it cannot be replayed if it leaks.
      await authApi.logout();
    } catch {
      /* network failure must not trap the user in a signed-in shell */
    }
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
      changePassword,
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
      changePassword,
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
