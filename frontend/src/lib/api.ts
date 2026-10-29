/**
 * Typed REST client for the PitchPilot FastAPI backend.
 * All responses are cast to the interfaces declared in `src/types`.
 */

import type {
  AuthResponse,
  BillingEventListResponse,
  BillingStatus,
  CheckoutResponse,
  CoachingSession,
  EmailVerificationStatus,
  MessageResponse,
  Script,
  ScriptCreateInput,
  ScriptUpdateInput,
  SessionDetail,
  SessionEndResponse,
  SessionListResponse,
  User,
  VerifyResponse,
  WsTicket,
} from "@/types";

const RAW_BASE = import.meta.env.VITE_API_BASE_URL as string | undefined;
/** Same-origin "/api" (proxied by Vite) unless overridden by an env var. */
export const API_BASE: string = (
  RAW_BASE && RAW_BASE.length > 0 ? RAW_BASE : "/api"
).replace(/\/+$/, "");

export const AUTH_TOKEN_STORAGE_KEY = "pitchpilot.token";

export class ApiError extends Error {
  readonly status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

export function getStoredToken(): string | null {
  try {
    return localStorage.getItem(AUTH_TOKEN_STORAGE_KEY);
  } catch {
    return null;
  }
}

export function setStoredToken(token: string | null): void {
  try {
    if (token === null) {
      localStorage.removeItem(AUTH_TOKEN_STORAGE_KEY);
    } else {
      localStorage.setItem(AUTH_TOKEN_STORAGE_KEY, token);
    }
  } catch {
    /* storage unavailable — auth simply won't persist */
  }
}

/**
 * Turn a FastAPI error body into a single readable sentence. `detail` can be a
 * string (HTTPException) or a list of validation objects (422 responses).
 */
function extractDetail(body: unknown): string | null {
  if (typeof body !== "object" || body === null || !("detail" in body)) {
    return null;
  }
  const detail = (body as { detail: unknown }).detail;
  if (typeof detail === "string") {
    return detail;
  }
  if (Array.isArray(detail)) {
    const messages = detail
      .map((item): string | null => {
        if (
          typeof item !== "object" ||
          item === null ||
          !("msg" in item) ||
          typeof (item as { msg: unknown }).msg !== "string"
        ) {
          return null;
        }
        const message = (item as { msg: string }).msg;
        const loc = (item as { loc?: unknown }).loc;
        if (Array.isArray(loc)) {
          const path = loc
            .filter((part): part is string => typeof part === "string")
            .join(".");
          return path.length > 0 ? `${path}: ${message}` : message;
        }
        return message;
      })
      .filter((message): message is string => message !== null);
    if (messages.length > 0) {
      return messages.join("; ");
    }
  }
  return null;
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
  };
  const token = getStoredToken();
  if (token !== null) {
    headers["Authorization"] = `Bearer ${token}`;
  }
  const extraHeaders = init.headers as Record<string, string> | undefined;
  if (extraHeaders) {
    Object.assign(headers, extraHeaders);
  }

  const response = await fetch(`${API_BASE}${path}`, { ...init, headers });
  if (!response.ok) {
    let detail = response.statusText;
    try {
      const body: unknown = await response.json();
      const parsed = extractDetail(body);
      if (parsed !== null) {
        detail = parsed;
      }
    } catch {
      /* non-JSON error body — keep statusText */
    }
    throw new ApiError(response.status, detail);
  }
  if (response.status === 204) {
    return undefined as T;
  }
  return (await response.json()) as T;
}

// ── Auth ─────────────────────────────────────────────────────────────────
export const authApi = {
  /** Registration always returns the same generic 202 response. */
  register: (email: string, password: string): Promise<MessageResponse> =>
    request<MessageResponse>("/auth/register", {
      method: "POST",
      body: JSON.stringify({ email, password }),
    }),

  login: (email: string, password: string): Promise<AuthResponse> =>
    request<AuthResponse>("/auth/login", {
      method: "POST",
      body: JSON.stringify({ email, password }),
    }),

  me: (): Promise<User> => request<User>("/auth/me"),

  /** Revoke this session's token server-side (best effort). */
  logout: (): Promise<void> =>
    request<void>("/auth/logout", { method: "POST" }),

  /** Revoke every session for the account. */
  logoutAll: (): Promise<void> =>
    request<void>("/auth/logout-all", { method: "POST" }),

  /** Confirm an email address from the link sent at registration. */
  verifyEmail: (token: string): Promise<User> =>
    request<User>("/auth/verify", {
      method: "POST",
      body: JSON.stringify({ token }),
    }),

  /** Ask for a fresh verification email. */
  resendVerification: (): Promise<EmailVerificationStatus> =>
    request<EmailVerificationStatus>("/auth/resend-verification", {
      method: "POST",
    }),

  /** Request a password-reset link. The response is intentionally generic. */
  forgotPassword: (email: string): Promise<MessageResponse> =>
    request<MessageResponse>("/auth/forgot-password", {
      method: "POST",
      body: JSON.stringify({ email }),
    }),

  /** Complete a password reset from the emailed single-use link. */
  resetPassword: (token: string, password: string): Promise<MessageResponse> =>
    request<MessageResponse>("/auth/reset-password", {
      method: "POST",
      body: JSON.stringify({ token, password }),
    }),

  /** Change the password from a signed-in session; returns a fresh token. */
  changePassword: (
    currentPassword: string,
    newPassword: string,
  ): Promise<AuthResponse> =>
    request<AuthResponse>("/auth/change-password", {
      method: "POST",
      body: JSON.stringify({
        current_password: currentPassword,
        new_password: newPassword,
      }),
    }),

  /** Record permission to capture/transcribe the call. */
  consent: (): Promise<User> =>
    request<User>("/auth/consent", { method: "POST" }),

  /** Download all data stored for the signed-in account. */
  exportData: (): Promise<unknown> => request<unknown>("/auth/export"),

  /** Permanently delete the account (password confirmation required). */
  deleteAccount: (password: string): Promise<void> =>
    request<void>("/auth/account", {
      method: "DELETE",
      body: JSON.stringify({ password }),
    }),

  /** Short-lived credential for one session's WebSocket handshake. */
  wsTicket: (sessionId: string): Promise<WsTicket> =>
    request<WsTicket>("/auth/ws-ticket", {
      method: "POST",
      body: JSON.stringify({ session_id: sessionId }),
    }),
};

// ── Scripts ──────────────────────────────────────────────────────────────
export const scriptsApi = {
  list: (): Promise<Script[]> => request<Script[]>("/scripts"),

  create: (payload: ScriptCreateInput): Promise<Script> =>
    request<Script>("/scripts", {
      method: "POST",
      body: JSON.stringify(payload),
    }),

  update: (id: string, payload: ScriptUpdateInput): Promise<Script> =>
    request<Script>(`/scripts/${id}`, {
      method: "PUT",
      body: JSON.stringify(payload),
    }),

  remove: (id: string): Promise<void> =>
    request<void>(`/scripts/${id}`, { method: "DELETE" }),
};

// ── Sessions ─────────────────────────────────────────────────────────────
export const sessionsApi = {
  create: (scriptId: string | null): Promise<CoachingSession> =>
    request<CoachingSession>("/sessions", {
      method: "POST",
      body: JSON.stringify({ script_id: scriptId }),
    }),

  list: (limit?: number): Promise<SessionListResponse> =>
    request<SessionListResponse>(
      `/sessions${limit === undefined ? "" : `?limit=${limit}`}`,
    ),

  detail: (id: string): Promise<SessionDetail> =>
    request<SessionDetail>(`/sessions/${id}`),

  end: (id: string): Promise<SessionEndResponse> =>
    request<SessionEndResponse>(`/sessions/${id}/end`, { method: "POST" }),
};

// ── Billing ─────────────────────────────────────────────────────────────
export const billingApi = {
  usage: (): Promise<BillingStatus> => request<BillingStatus>("/billing/usage"),

  checkout: (): Promise<CheckoutResponse> =>
    request<CheckoutResponse>("/billing/checkout", { method: "POST" }),

  cancel: (): Promise<BillingStatus> =>
    request<BillingStatus>("/billing/cancel", { method: "POST" }),

  verify: (reference: string): Promise<VerifyResponse> =>
    request<VerifyResponse>("/billing/verify", {
      method: "POST",
      body: JSON.stringify({ reference }),
    }),

  events: (limit?: number): Promise<BillingEventListResponse> =>
    request<BillingEventListResponse>(
      `/billing/events${limit === undefined ? "" : `?limit=${limit}`}`,
    ),
};
