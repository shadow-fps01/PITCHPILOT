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
  EmailAvailability,
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
      if (
        typeof body === "object" &&
        body !== null &&
        "detail" in body &&
        typeof (body as { detail: unknown }).detail === "string"
      ) {
        detail = (body as { detail: string }).detail;
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
  register: (email: string, password: string): Promise<AuthResponse> =>
    request<AuthResponse>("/auth/register", {
      method: "POST",
      body: JSON.stringify({ email, password }),
    }),

  login: (email: string, password: string): Promise<AuthResponse> =>
    request<AuthResponse>("/auth/login", {
      method: "POST",
      body: JSON.stringify({ email, password }),
    }),

  me: (): Promise<User> => request<User>("/auth/me"),

  /** Whether this deployment can send password-reset and verification email. */
  emailAvailability: (): Promise<EmailAvailability> =>
    request<EmailAvailability>("/auth/email-available"),

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

  /** Short-lived credential for the WebSocket handshake. */
  wsTicket: (): Promise<WsTicket> => request<WsTicket>("/auth/ws-ticket", { method: "POST" }),
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
