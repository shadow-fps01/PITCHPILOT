/**
 * Strict TypeScript interfaces mirroring the backend's Pydantic models
 * (backend/app/schemas/*). Every API response and WebSocket message maps 1:1.
 */

// ── Auth ────────────────────────────────────────────────────────────────
export type Plan = "free" | "plus";

export interface User {
  id: string;
  email: string;
  created_at: string;
  plan: Plan;
  is_plus: boolean;
  email_verified: boolean;
  /** The user accepted the current recording/AI-processing consent text. */
  consent_current: boolean;
}

export interface AuthResponse {
  access_token: string;
  token_type: string;
  user: User;
}

export interface WsTicket {
  ticket: string;
  expires_in: number;
}

export interface EmailVerificationStatus {
  verified: boolean;
  sent: boolean;
}

export interface MessageResponse {
  message: string;
}

// ── Billing (PitchPilot Plus) ─────────────────────────────────────────────
export interface BillingStatus {
  plan: Plan;
  is_plus: boolean;
  provider: "paystack" | "demo";
  payments_live: boolean;
  price_display: string;
  price_amount: number;
  currency: string;
  free_monthly_sessions: number;
  free_monthly_scripts: number;
  sessions_used_this_month: number;
  scripts_used_this_month: number;
  sessions_remaining: number | null;
  scripts_remaining: number | null;
  unlimited: boolean;
  plus_started_at: string | null;
  plus_renews_at: string | null;
  plus_cancel_at_period_end: boolean;
}

export interface CheckoutResponse {
  mode: "paystack" | "demo" | "already_plus";
  authorization_url: string | null;
  status: BillingStatus;
}

/** POST /billing/verify — client-driven payment confirmation. */
export interface VerifyResponse {
  verified: boolean;
  status: string;
  message: string;
  billing: BillingStatus;
}

export interface BillingEvent {
  id: string;
  event_type: string;
  provider: string;
  reference: string | null;
  amount: number | null;
  currency: string | null;
  message: string | null;
  created_at: string;
}

export interface BillingEventListResponse {
  events: BillingEvent[];
}

// ── Scripts ──────────────────────────────────────────────────────────────
export interface Script {
  id: string;
  user_id: string;
  title: string;
  content_outline: string;
  target_wpm: number;
  keywords: string[];
}

export interface ScriptCreateInput {
  title: string;
  content_outline: string;
  target_wpm?: number;
  keywords?: string[];
}

export interface ScriptUpdateInput {
  title?: string;
  content_outline?: string;
  target_wpm?: number;
  keywords?: string[];
}

// ── Coaching sessions ────────────────────────────────────────────────────
/** Cue types emitted by the live coach (narrow — keeps CueToast exhaustive). */
export type CoachingCueType = "FILLER_WORD" | "PACING_ALERT" | "SCRIPT_MISS";

/** Every event type that can be persisted on a session (detail/summary API). */
export type SessionEventType =
  | CoachingCueType
  | "CONTEXT_CARD"
  | "COPILOT_ASSIST";

export interface SessionEvent {
  id: number;
  session_id: string;
  timestamp: number;
  event_type: SessionEventType;
  message: string;
  raw_transcript_snippet: string | null;
}

export interface CoachingSession {
  id: string;
  user_id: string;
  script_id: string | null;
  started_at: string | null;
  ended_at: string | null;
  duration_seconds: number | null;
}

export interface SessionSummary {
  total_filler_words: number;
  pacing_alerts: number;
  script_misses: number;
  context_cards: number;
  event_count: number;
  duration_seconds: number | null;
}

export interface SessionDetail extends CoachingSession {
  events: SessionEvent[];
  summary: SessionSummary;
}

export interface SessionListResponse {
  sessions: CoachingSession[];
}

export interface SessionEndResponse {
  session: CoachingSession;
  summary: SessionSummary;
}

// ── WebSocket protocol (mirrors app/schemas/stream.py) ───────────────────
export type CueSeverity = "info" | "warn" | "alert";

export interface TranscriptMessage {
  type: "transcript";
  text: string;
  is_final: boolean;
}

export interface MetricsMessage {
  type: "metrics";
  wpm: number;
  filler_word_count: number;
  elapsed_seconds: number;
  target_wpm: number;
  mic_level: number;
}

export interface CueMessage {
  type: "cue";
  cue_type: CoachingCueType;
  message: string;
  severity: CueSeverity;
  raw_transcript_snippet: string | null;
}

export interface SessionStartedMessage {
  type: "session_started";
  session_id: string;
  started_at: string;
}

export interface SessionEndedMessage {
  type: "session_ended";
  duration_seconds: number;
  summary: SessionSummary;
}

export interface ErrorMessage {
  type: "error";
  message: string;
}

export interface PongMessage {
  type: "pong";
  timestamp: number;
}

export type ServerMessage =
  | TranscriptMessage
  | MetricsMessage
  | CueMessage
  | SessionStartedMessage
  | SessionEndedMessage
  | ErrorMessage
  | PongMessage
  | SessionStatusMessage
  | CaptureStatusMessage
  | ContextCardMessage
  | CopilotMessage
  | CopilotDeltaMessage
  | DebriefMessage;

export type ClientMessage =
  | { type: "ping"; timestamp: number }
  | { type: "stop" }
  | { type: "capture"; action: "start" | "stop" }
  | { type: "ask"; id: string; text: string }
  | { type: "screen"; action: "start" | "stop" }
  | { type: "tune"; sensitivity: CueSensitivity };

// ── Copilot dialogue (Cluely-style) ─────────────────────────────────────
export type CopilotKind = "suggestion" | "answer" | "status";

/** A copilot dialogue message pushed by the server. */
export interface CopilotMessage {
  type: "copilot";
  id: string;
  kind: CopilotKind;
  text: string;
  trigger: string | null;
  reply_to: string | null;
  screen_used: boolean;
}

/** Incremental tokens while the copilot composes an answer (`text` = total so far). */
export interface CopilotDeltaMessage {
  type: "copilot_delta";
  id: string;
  reply_to: string;
  text: string;
}

/** End-of-session written review. */
export interface DebriefMessage {
  type: "debrief";
  summary: string;
  tips: string[];
  source: string;
}

/** Per-session coaching sensitivity (cue frequency). */
export type CueSensitivity = "calm" | "balanced" | "coach";

// ── Copilot upgrades ─────────────────────────────────────────────────────
/** Subsystem availability, sent right after session_started. */
export interface SessionStatusMessage {
  type: "status";
  stt_provider: string;
  capture_available: boolean;
  capture_active: boolean;
  vision_active: boolean;
  anticipation_active: boolean;
  copilot_active: boolean;
}

/** Native dual-audio capture lifecycle (mic + system loopback). */
export interface CaptureStatusMessage {
  type: "capture_status";
  active: boolean;
  mode: "off" | "mic-only" | "dual";
  mic: string | null;
  loopback: string | null;
  error: string | null;
}

/** A pre-fetched next-topic answer card from the RAG anticipator. */
export interface ContextCardMessage {
  type: "context_card";
  title: string;
  content: string;
  score: number;
  keywords: string[];
}

// ── Live overlay view-model ──────────────────────────────────────────────
export interface LiveCue {
  id: number;
  cue_type: CoachingCueType;
  message: string;
  severity: CueSeverity;
  receivedAt: number;
}

/** A surfaced context card with client-side lifecycle metadata. */
export interface LiveContextCard {
  id: number;
  title: string;
  content: string;
  score: number;
  keywords: string[];
  receivedAt: number;
}

/** One entry in the copilot dialogue feed (Cluely-style chat). */
export interface DialogueEntry {
  id: string;
  role: "user" | "copilot" | "system";
  text: string;
  /** Copilot message kind (suggestion / answer / status). */
  kind?: CopilotKind;
  trigger?: string | null;
  screenUsed?: boolean;
  receivedAt: number;
  /** True while the copilot is generating a reply to this user message. */
  pending?: boolean;
  /** True while answer tokens are still streaming into this bubble. */
  streaming?: boolean;
}

export type WsStatus = "idle" | "connecting" | "open" | "reconnecting" | "closed";

export type ViewMode = "dashboard" | "live" | "settings";

export interface AppSettings {
  overlayOpacity: number; // 0.5 – 0.95
  targetWpmDefault: number;
  cueDismissSeconds: number;
  showTranscriptTicker: boolean;
  /** How chatty the coach is: fewer cues (calm) → more (coach). */
  cueSensitivity: CueSensitivity;
}
