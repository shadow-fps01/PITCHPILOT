/**
 * OverlayWidget — the live coaching overlay, now a Cluely-style AI copilot
 * dialogue box.
 *
 * - A chronological chat feed: proactive copilot suggestions ("say this
 *   next"), answers to your typed questions, coaching cues and RAG context
 *   cards all flow through one dialogue.
 * - Ask anything at the bottom — the copilot answers using the live call
 *   transcript and your script outline.
 * - Copilot integrations kept from the previous versions:
 *   F3 "Next up" cards, F4 teleprompter, and browser screen/call-audio capture.
 *
 * Visual system: a translucent glass card whose ambient glow reacts to the
 * live microphone level, with animated status and level meters.
 */

import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type FormEvent,
  type KeyboardEvent,
} from "react";
import {
  ArrowLeft,
  BookOpen,
  Check,
  Copy,
  CornerDownLeft,
  AlertTriangle,
  Eye,
  EyeOff,
  ExternalLink,
  Loader2,
  Monitor,
  MessageSquareWarning,
  Mic,
  MicOff,
  Radio,
  SendHorizontal,
  Sparkles,
  X,
} from "lucide-react";

import { useAuth } from "@/context/AuthContext";
import { useApp } from "@/context/AppContext";
import { useAudioRecorder } from "@/hooks/useAudioRecorder";
import { useWebSocket } from "@/hooks/useWebSocket";
import { ApiError, API_BASE, authApi, getStoredToken, sessionsApi } from "@/lib/api";
import { Teleprompter } from "@/components/Teleprompter";
import { WpmGauge } from "@/components/WpmGauge";
import { Waveform } from "@/components/ui/Waveform";
import { cn, formatDuration, safeExternalUrl } from "@/lib/utils";
import type {
  DialogueEntry,
  MetricsMessage,
  ServerMessage,
  SessionEndedMessage,
  SessionStatusMessage,
} from "@/types";

const MAX_DIALOGUE_ENTRIES = 80;
const QUICK_ACTIONS: Array<{ label: string; question: string }> = [
  {
    label: "Answer that",
    question:
      "Answer the question that was just asked on this call — give me the exact words to say.",
  },
  {
    label: "What should I say next?",
    question:
      "What is the single best thing for me to say or do next in this conversation?",
  },
];

function wsUrlFor(sessionId: string, ticket: string): string {
  // Resolve against API_BASE so same-origin deployments (Vite dev proxy and
  // nginx in production) and split API hosts both work.
  const url = new URL(`${API_BASE}/ws/stream/${sessionId}`, window.location.href);
  url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
  url.searchParams.set("ticket", ticket);
  return url.toString();
}

export function OverlayWidget(): JSX.Element {
  const { token } = useAuth();
  // True when this document is the always-on-top Document-PiP overlay
  // (src/lib/pipOverlay.ts) — the web counterpart of the Electron shell.
  const pipMode = window.pitchpilotPip === true;
  const {
    activeSessionId,
    setActiveSessionId,
    activeScript,
    setActiveScript,
    setView,
    settings,
    refreshSessions,
  } = useApp();

  const [sessionId, setSessionId] = useState<string | null>(activeSessionId);
  const [sessionError, setSessionError] = useState<string | null>(null);

  const [liveStarted, setLiveStarted] = useState(false);
  const [metrics, setMetrics] = useState<MetricsMessage | null>(null);
  const [interimText, setInterimText] = useState("");
  const [finalLines, setFinalLines] = useState<string[]>([]);
  const [ended, setEnded] = useState<{
    durationSeconds: number;
    summary: SessionEndedMessage["summary"];
  } | null>(null);

  // â”€â”€ Copilot state â”€â”€
  const [status, setStatus] = useState<SessionStatusMessage | null>(null);
  const [prompterOn, setPrompterOn] = useState(false);
  const [dialogue, setDialogue] = useState<DialogueEntry[]>([]);
  const [draft, setDraft] = useState("");
  const [copiedId, setCopiedId] = useState<string | null>(null);
  const [debrief, setDebrief] = useState<{
    summary: string;
    tips: string[];
    source: string;
  } | null>(null);

  const entryIdRef = useRef(0);
  const feedRef = useRef<HTMLDivElement | null>(null);

  const nextEntryId = useCallback((): string => {
    entryIdRef.current += 1;
    return `e${entryIdRef.current}-${Date.now()}`;
  }, []);

  const pushEntry = useCallback(
    (entry: Omit<DialogueEntry, "id" | "receivedAt">): string => {
      const id = nextEntryId();
      setDialogue((prev) =>
        [...prev, { ...entry, id, receivedAt: Date.now() }].slice(
          -MAX_DIALOGUE_ENTRIES,
        ),
      );
      return id;
    },
    [nextEntryId],
  );

  const recorder = useAudioRecorder();

  // â”€â”€ Resolve or create the coaching session â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
  useEffect(() => {
    if (sessionId !== null) return;
    let cancelled = false;
    (async () => {
      try {
        const session = await sessionsApi.create(activeScript?.id ?? null);
        if (cancelled) return;
        setSessionId(session.id);
        setActiveSessionId(session.id);
      } catch (err) {
        if (!cancelled) {
          setSessionError(
            err instanceof ApiError
              ? err.message
              : "Could not start a session — please try again.",
          );
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [sessionId, activeScript, setActiveSessionId]);

  // Resolve a fresh WS ticket per connection attempt (including reconnects)
  // so a short-lived credential can never strand a live session. The JWT is
  // deliberately never placed in the URL or proxy logs.
  const wsUrl = useCallback(async (): Promise<string> => {
    if (sessionId === null) return "";
    try {
      const { ticket } = await authApi.wsTicket(sessionId);
      return wsUrlFor(sessionId, ticket);
    } catch (err) {
      setSessionError(
        err instanceof ApiError && err.status === 401
          ? "Your session has expired — please sign in again."
          : "Live connection interrupted — reconnecting…",
      );
      throw err;
    }
  }, [sessionId]);

  // The mic meter always comes from the presenter's microphone. Shared call
  // audio is transcribed separately and must never drive the speaking coach.
  const effectiveMicLevel = recorder.micLevel;

  // â”€â”€ WebSocket wiring â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
  const handleMessage = useCallback(
    (message: ServerMessage): void => {
      switch (message.type) {
        case "transcript": {
          if (message.is_final) {
            setFinalLines((prev) => [...prev.slice(-3), message.text]);
            setInterimText("");
          } else {
            setInterimText(message.text);
          }
          break;
        }
        case "metrics": {
          setMetrics(message);
          break;
        }
        case "cue": {
          // Coaching cues flow through the dialogue as compact system lines.
          pushEntry({ role: "system", text: message.message, kind: undefined });
          break;
        }
        case "status": {
          setStatus(message);
          break;
        }
        case "context_card": {
          pushEntry({
            role: "system",
            text: `Next up · ${message.title} — ${message.content}`,
            kind: undefined,
          });
          break;
        }
        case "copilot_delta": {
          // Streamed answer tokens — grow the bubble in place (same id as the
          // final `copilot` message that will close it).
          setDialogue((prev) => {
            const cleared = prev.map((entry) =>
              entry.id === message.reply_to && entry.pending
                ? { ...entry, pending: false }
                : entry,
            );
            const index = cleared.findIndex((entry) => entry.id === message.id);
            if (index >= 0) {
              const copy = cleared.slice();
              copy[index] = { ...copy[index], text: message.text, streaming: true };
              return copy;
            }
            return [
              ...cleared,
              {
                id: message.id,
                role: "copilot" as const,
                kind: "answer" as const,
                text: message.text,
                streaming: true,
                receivedAt: Date.now(),
              },
            ].slice(-MAX_DIALOGUE_ENTRIES);
          });
          break;
        }
        case "copilot": {
          if (message.kind === "status") {
            pushEntry({
              role: "system",
              text: message.text,
            });
            break;
          }
          setDialogue((prev) => {
            // Clear the "thinking..." marker on the originating user bubble.
            const cleared = prev.map((entry) =>
              entry.id === message.reply_to && entry.pending
                ? { ...entry, pending: false }
                : entry,
            );
            // Finalize the streamed bubble if one exists for this id.
            const index = cleared.findIndex(
              (entry) => entry.id === message.id && entry.role === "copilot",
            );
            if (index >= 0) {
              const copy = cleared.slice();
              copy[index] = {
                ...copy[index],
                text: message.text,
                kind: message.kind,
                trigger: message.trigger,
                reply_to: message.reply_to,
                sources: message.sources,
                streaming: false,
                pending: false,
              };
              return copy;
            }
            return [
              ...cleared,
              {
                id: message.id,
                role: "copilot" as const,
                kind: message.kind,
                text: message.text,
                trigger: message.trigger,
                reply_to: message.reply_to,
                sources: message.sources,
                receivedAt: Date.now(),
              },
            ].slice(-MAX_DIALOGUE_ENTRIES);
          });
          break;
        }
        case "debrief": {
          setDebrief({
            summary: message.summary,
            tips: message.tips,
            source: message.source,
          });
          break;
        }
        case "session_started": {
          setLiveStarted(true);
          break;
        }
        case "session_ended": {
          setEnded({
            durationSeconds: message.duration_seconds,
            summary: message.summary,
          });
          recorder.stop();
          break;
        }
        case "error": {
          setSessionError(message.message);
          break;
        }
        default:
          break;
      }
    },
    [pushEntry, recorder],
  );

  const handleOpen = useCallback((): void => setSessionError(null), []);
  const handleFinalClose = useCallback((): void => {
    setSessionError("Live connection lost — refresh to reconnect.");
  }, []);

  const {
    status: wsStatus,
    reconnectAttempt,
    connect,
    disconnect,
    sendAudio,
    sendControl,
    sendScreenFrame,
  } = useWebSocket({
    url: wsUrl,
    autoConnect: true,
    onMessage: handleMessage,
    onOpen: handleOpen,
    onFinalClose: handleFinalClose,
  });

  // Push the chosen coaching sensitivity to the server on (re)connect and
  // whenever the setting changes.
  useEffect(() => {
    if (wsStatus === "open") {
      sendControl({ type: "tune", sensitivity: settings.cueSensitivity });
    }
  }, [wsStatus, settings.cueSensitivity, sendControl]);

  // Re-arm screen vision only when the socket itself (re)opens. Tracking the
  // display state in a ref avoids sending a duplicate start command when the
  // user first begins sharing on an already-open socket.
  const screenActiveRef = useRef(recorder.screenActive);
  useEffect(() => {
    screenActiveRef.current = recorder.screenActive;
  }, [recorder.screenActive]);
  useEffect(() => {
    if (wsStatus === "open" && screenActiveRef.current) {
      sendControl({ type: "screen", action: "start" });
    }
  }, [wsStatus, sendControl]);

  // â”€â”€ Auto-scroll the dialogue feed â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
  useEffect(() => {
    const feed = feedRef.current;
    if (feed !== null) {
      feed.scrollTop = feed.scrollHeight;
    }
  }, [dialogue]);

  // â”€â”€ Controls â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
  const handleScreenEnded = useCallback((): void => {
    sendControl({ type: "screen", action: "stop" });
  }, [sendControl]);

  const startCapture = useCallback(
    async (withScreen: boolean): Promise<void> => {
      setSessionError(null);
      try {
        const result = await recorder.start(
          (chunk, source) => sendAudio(chunk, source),
          withScreen
            ? {
                withScreen: true,
                onFrame: sendScreenFrame,
                onEnded: handleScreenEnded,
              }
            : undefined,
        );
        if (withScreen) {
          sendControl({ type: "screen", action: "start" });
          if (result.screenSurface === "monitor") {
            pushEntry({
              role: "system",
              text: "You are sharing your whole screen — this overlay is visible in that share. Stop and choose the call tab or window instead.",
            });
          }
          if (!result.screenAudioActive) {
            pushEntry({
              role: "system",
              text: "No call audio was shared. Choose the call tab/window and tick “Share system audio”. Use headphones so the coach measures only your voice.",
            });
          }
        }
      } catch {
        // The recorder shows a specific permission message in its banner;
        // this is only a fallback for unexpected microphone failures.
        if (!withScreen) {
          setSessionError("Microphone unavailable — check browser permissions.");
        }
      }
    },
    [
      recorder,
      sendAudio,
      sendScreenFrame,
      sendControl,
      handleScreenEnded,
      pushEntry,
    ],
  );

  const startMic = useCallback((): void => {
    void startCapture(false);
  }, [startCapture]);

  const startScreen = useCallback(async (): Promise<void> => {
    setSessionError(null);
    try {
      const result = await recorder.startScreenShare({
        onFrame: sendScreenFrame,
        onEnded: handleScreenEnded,
      });
      sendControl({ type: "screen", action: "start" });
      if (result.screenSurface === "monitor") {
        pushEntry({
          role: "system",
          text: "You are sharing your whole screen — this overlay is visible in that share. Stop and choose the call tab or window instead.",
        });
      }
      if (!result.screenAudioActive) {
        pushEntry({
          role: "system",
          text: "No call audio was shared. Choose the call tab/window and tick “Share system audio”. Use headphones so the coach measures only your voice.",
        });
      }
    } catch {
      // The recorder explains what happened and keeps the mic running.
    }
  }, [recorder, sendScreenFrame, sendControl, handleScreenEnded, pushEntry]);

  const stopScreen = useCallback((): void => {
    recorder.stopScreenShare();
    sendControl({ type: "screen", action: "stop" });
  }, [recorder, sendControl]);

  const askCopilot = useCallback(
    (question: string): void => {
      const trimmed = question.trim();
      if (trimmed.length === 0 || wsStatus !== "open") return;
      const pendingId = pushEntry({ role: "user", text: trimmed, pending: true });
      sendControl({ type: "ask", id: pendingId, text: trimmed });
      setDraft("");
    },
    [pushEntry, sendControl, wsStatus],
  );

  const onSubmitDraft = useCallback(
    (event: FormEvent<HTMLFormElement> | KeyboardEvent<HTMLTextAreaElement>): void => {
      event.preventDefault();
      askCopilot(draft);
    },
    [askCopilot, draft],
  );

  const copyEntry = useCallback((entry: DialogueEntry): void => {
    void navigator.clipboard?.writeText(entry.text).then(() => {
      setCopiedId(entry.id);
      window.setTimeout(() => setCopiedId((id) => (id === entry.id ? null : id)), 1600);
    });
  }, []);

  const endSession = useCallback((): void => {
    const hadScreen = recorder.screenActive;
    recorder.stop();
    if (hadScreen) {
      sendControl({ type: "screen", action: "stop" });
    }
    disconnect(true);
  }, [recorder, sendControl, disconnect]);

  const leaveSession = useCallback((): void => {
    if (recorder.screenActive) {
      sendControl({ type: "screen", action: "stop" });
    }
    recorder.stop();
    if (wsStatus === "open" || wsStatus === "reconnecting") {
      disconnect(false);
    }
    refreshSessions();
    setActiveSessionId(null);
    setActiveScript(null);
    if (window.opener !== null || pipMode) {
      // Detached popup or the PiP overlay — close this window.
      window.close();
    }
    setView("dashboard");
  }, [
    wsStatus,
    recorder,
    sendControl,
    disconnect,
    refreshSessions,
    setActiveSessionId,
    setActiveScript,
    setView,
  ]);

  // â”€â”€ Keyboard shortcuts (hands-free during a call) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
  // Space = start / stop, Esc = end session, C = copy the last copilot line.
  useEffect(() => {
    const onKeyDown = (event: globalThis.KeyboardEvent): void => {
      const target = event.target as HTMLElement | null;
      if (
        target !== null &&
        (target.tagName === "INPUT" ||
          target.tagName === "TEXTAREA" ||
          target.tagName === "SELECT" ||
          target.tagName === "BUTTON" ||
          target.isContentEditable)
      ) {
        return;
      }
      if (event.metaKey || event.ctrlKey || event.altKey) return;
      if (ended !== null) return;
      if (event.key === " ") {
        event.preventDefault();
        if (recorder.isRecording) {
          endSession();
        } else {
          void startMic();
        }
      } else if (event.key === "Escape") {
        event.preventDefault();
        endSession();
      } else if (event.key === "c" || event.key === "C") {
        const last = [...dialogue].reverse().find((entry) => entry.role === "copilot");
        if (last !== undefined) copyEntry(last);
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [
    ended,
    recorder.isRecording,
    startMic,
    endSession,
    dialogue,
    copyEntry,
  ]);

  const effectiveToken: string | null = token ?? getStoredToken();

  const targetWpm: number =
    metrics?.target_wpm ?? activeScript?.target_wpm ?? settings.targetWpmDefault;
  const wpm: number = metrics?.wpm ?? 0;
  const fillers: number = metrics?.filler_word_count ?? 0;
  const elapsed: number = metrics?.elapsed_seconds ?? 0;

  const statusDot: { color: string; label: string; ping: boolean } =
    wsStatus === "open"
      ? { color: "bg-emerald-400", label: "Live", ping: true }
      : wsStatus === "connecting"
        ? { color: "bg-amber-400", label: "Connecting", ping: true }
        : wsStatus === "reconnecting"
          ? { color: "bg-amber-400", label: "Reconnecting", ping: true }
          : { color: "bg-slate-500", label: "Offline", ping: false };

  const cardStyle = {
    backgroundColor: `rgba(10, 9, 15, ${settings.overlayOpacity})`,
  };

  const prompterAvailable =
    activeScript !== null && activeScript.content_outline.trim().length > 0;
  const prompterVisible = prompterOn && prompterAvailable;
  const screenAvailable =
    status?.vision_available === true && recorder.screenSupported;

  // Ambient glow behind the card, driven by the live mic level.
  const energy = Math.min(1, effectiveMicLevel);
  const glowStyle = {
    background: `radial-gradient(circle at 50% 0%, rgba(251,191,36,${0.28 + energy * 0.5}), transparent 62%)`,
    opacity: 0.55 + energy * 0.45,
  };

  return (
    <main className="relative flex min-h-full items-start justify-center overflow-hidden bg-ink-950 px-3 py-6">
      {/* reactive ambience */}
      <div className="pointer-events-none fixed inset-0 -z-10" style={glowStyle} />
      <div className="pointer-events-none fixed bottom-0 left-1/2 -z-10 h-72 w-72 -translate-x-1/2 rounded-full bg-amber-500/20 blur-3xl" />

      <div
        className="relative z-10 flex h-[calc(100vh-3rem)] w-full max-w-[420px] flex-col overflow-hidden rounded-3xl border border-white/10 shadow-panel backdrop-blur-2xl transition-colors"
        style={cardStyle}
      >
        {/* â”€â”€ Top bar â”€â”€ */}
        <div className="desktop-drag flex items-center gap-2 px-4 pt-3.5">
          <span className="relative flex h-2.5 w-2.5 shrink-0">
            {statusDot.ping && (
              <span
                className={cn(
                  "absolute inset-0 animate-ping-soft rounded-full",
                  statusDot.color,
                )}
              />
            )}
            <span
              className={cn("relative h-2.5 w-2.5 rounded-full", statusDot.color)}
            />
          </span>
          <span className="text-[11px] font-semibold uppercase tracking-[0.2em] text-slate-400">
            {statusDot.label}
          </span>
          {(window.pitchpilotDesktop === true || pipMode) && (
            <span
              className="chip-emerald px-1.5 py-0.5 text-[9px] font-bold uppercase tracking-wider"
              title={
                window.pitchpilotDesktop === true
                  ? "This window is excluded from screen capture (desktop shell)"
                  : "Always-on-top PiP window — never captured when you share a tab or window"
              }
            >
              <EyeOff size={9} /> share-safe
            </span>
          )}
          {wsStatus === "reconnecting" && (
            <span className="text-[10px] text-amber-300/80">
              #{reconnectAttempt} · buffered
            </span>
          )}
          <span className="ml-auto font-mono text-xs tabular-nums text-slate-300">
            {formatDuration(ended?.durationSeconds ?? elapsed)}
          </span>
          <button
            onClick={leaveSession}
            title="Leave overlay"
            className="rounded-lg p-1 text-slate-500 transition-colors hover:bg-white/5 hover:text-slate-200"
          >
            <X size={14} />
          </button>
        </div>

        {/* â”€â”€ Copilot subsystem chips â”€â”€ */}
        {status !== null && (
          <div className="mt-2.5 flex flex-wrap gap-1.5 px-4">
            <span className="chip">
              <Radio size={9} /> Listening
            </span>
            {recorder.screenActive && (
              <span className="chip-emerald">
                <Eye size={9} />{" "}
                {recorder.screenAudioActive ? "screen + call audio" : "screen"}
              </span>
            )}
            {recorder.screenActive && recorder.screenSurface === "monitor" && (
              <span className="chip-gold">
                <EyeOff size={9} /> whole screen visible
              </span>
            )}
            {status.copilot_active && (
              <span className="chip-aqua">
                <Sparkles size={9} /> copilot
              </span>
            )}
          </div>
        )}

        {/* â”€â”€ Stats strip â”€â”€ */}
        <div className="mt-2 flex items-center justify-between gap-3 px-4">
          <WpmGauge wpm={wpm} target={targetWpm} />

          <div className="flex flex-col items-end gap-2.5">
            <span
              className={cn(
                "flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-semibold tabular-nums transition-colors",
                fillers > 0
                  ? "bg-amber-500/15 text-amber-300"
                  : "bg-white/[0.05] text-slate-400",
              )}
            >
              <MessageSquareWarning size={13} />
              {fillers} fillers
            </span>
            <div className="flex items-center gap-2">
              <span className="text-[9px] font-semibold uppercase tracking-widest text-slate-600">
                {recorder.screenAudioActive ? "call + mic" : "mic"}
              </span>
              <Waveform active={liveStarted} level={effectiveMicLevel} bars={6} />
            </div>
          </div>
        </div>

        {/* â”€â”€ Error banner â”€â”€ */}
        {(sessionError ?? recorder.error) !== null && (
          <p className="mx-4 mt-3 animate-scale-in rounded-xl border border-rose-500/30 bg-rose-500/10 px-3 py-2 text-[11px] leading-snug text-rose-300">
            {sessionError ?? recorder.error}
          </p>
        )}

        {/* â”€â”€ Teleprompter (F4) â”€â”€ */}
        {prompterVisible && (
          <div className="relative mt-3">
            <Teleprompter
              outline={activeScript?.content_outline ?? ""}
              finalTranscript={finalLines.join(" ")}
              micLevel={effectiveMicLevel}
              wpm={wpm}
              targetWpm={targetWpm}
              active={liveStarted && ended === null}
            />
          </div>
        )}

        {/* â”€â”€ Copilot dialogue feed â”€â”€ */}
        <div
          ref={feedRef}
          role="log"
          aria-live="polite"
          aria-label="Copilot dialogue"
          className="mt-3 min-h-[120px] flex-1 overflow-y-auto px-4"
        >
          {dialogue.length === 0 && ended === null && (
            <div className="flex h-full flex-col items-center justify-center gap-2 py-6 text-center">
              <span className="relative flex h-11 w-11 items-center justify-center rounded-2xl bg-gradient-copilot">
                <span className="absolute inset-0 animate-ping-soft rounded-2xl bg-amber-400/20" />
                <Sparkles size={18} className="text-amber-300" />
              </span>
              <p className="text-xs font-semibold text-slate-200">
                Your copilot is listening
              </p>
              <p className="max-w-[260px] text-[11px] leading-relaxed text-slate-500">
                Start the mic — suggestions appear here
                automatically as the call unfolds. Or ask anything below.
              </p>
            </div>
          )}
          <div className="flex flex-col gap-2 pb-1">
            {dialogue.map((entry) => {
              if (entry.role === "user") {
                return (
                  <div key={entry.id} className="flex justify-end">
                    <div
                      className={cn(
                        "max-w-[85%] rounded-2xl rounded-br-md bg-gradient-brand px-3 py-2 text-[12.5px] font-medium leading-snug text-slate-950 shadow-glow",
                        entry.pending && "opacity-90",
                      )}
                    >
                      {entry.text}
                      {entry.pending && (
                        <span className="ml-2 inline-flex items-center gap-1 align-middle">
                          <span className="inline-flex gap-0.5">
                            {[0, 1, 2].map((i) => (
                              <span
                                key={i}
                                className="h-1 w-1 animate-typing-dot rounded-full bg-slate-950/70"
                                style={{ animationDelay: `${i * 0.15}s` }}
                              />
                            ))}
                          </span>
                        </span>
                      )}
                    </div>
                  </div>
                );
              }
              if (entry.role === "system") {
                return (
                  <p
                    key={entry.id}
                    className="animate-cue-in rounded-xl border border-white/5 bg-white/[0.03] px-2.5 py-1.5 text-[11px] leading-snug text-slate-400"
                  >
                    {entry.text}
                  </p>
                );
              }
              // The AI/search service failed: show it plainly with a retry.
              if (entry.kind === "error") {
                const retryQuestion = [...dialogue]
                  .reverse()
                  .find((item) => item.role === "user" && item.id === entry.reply_to);
                return (
                  <div
                    key={entry.id}
                    className="animate-cue-in rounded-2xl rounded-bl-md border border-amber-400/25 bg-amber-500/[0.08] px-3 py-2.5"
                  >
                    <div className="flex items-center gap-1.5">
                      <AlertTriangle size={10} className="text-amber-300" />
                      <p className="text-[9.5px] font-bold uppercase tracking-widest text-amber-300/90">
                        Copilot offline
                      </p>
                    </div>
                    <p className="mt-1 text-[12.5px] leading-snug text-amber-50/90">
                      {entry.text}
                    </p>
                    {retryQuestion !== undefined && (
                      <button
                        type="button"
                        onClick={() => askCopilot(retryQuestion.text)}
                        className="mt-2 rounded-lg border border-amber-300/30 px-2 py-1 text-[10.5px] font-semibold text-amber-200 transition-colors hover:bg-amber-300/10"
                      >
                        Retry
                      </button>
                    )}
                  </div>
                );
              }
              const isSuggestion = entry.kind === "suggestion";
              return (
                <div
                  key={entry.id}
                  className={cn(
                    "group relative animate-cue-in rounded-2xl rounded-bl-md border px-3 py-2.5 backdrop-blur-md",
                    isSuggestion
                      ? "border-amber-400/25 bg-gradient-to-br from-amber-500/[0.12] to-transparent"
                      : "border-yellow-300/20 bg-gradient-to-br from-yellow-400/[0.09] to-transparent",
                  )}
                >
                  <div className="flex items-center gap-1.5">
                    <Sparkles
                      size={10}
                      className={isSuggestion ? "text-amber-300" : "text-yellow-200"}
                    />
                    <p
                      className={cn(
                        "text-[9.5px] font-bold uppercase tracking-widest",
                        isSuggestion ? "text-amber-300/90" : "text-yellow-200/90",
                      )}
                    >
                      {isSuggestion ? "Say this" : "Copilot"}
                    </p>
                    <button
                      onClick={() => copyEntry(entry)}
                      title="Copy"
                      className="ml-auto rounded p-0.5 text-slate-600 opacity-0 transition-opacity hover:text-slate-200 group-hover:opacity-100"
                    >
                      {copiedId === entry.id ? <Check size={11} /> : <Copy size={11} />}
                    </button>
                  </div>
                  {entry.trigger && (
                    <p className="mt-0.5 line-clamp-1 text-[10px] italic text-slate-500">
                      {entry.trigger}
                    </p>
                  )}
                  <p className="mt-1 text-[12.5px] leading-snug text-slate-50">
                    {entry.text}
                    {entry.streaming === true && (
                      <span className="ml-0.5 inline-block h-3 w-1.5 translate-y-0.5 animate-pulse rounded-sm bg-amber-300/80 align-middle" />
                    )}
                  </p>
                  {entry.sources !== undefined && entry.sources.length > 0 && (
                    <div className="mt-1.5 flex flex-wrap gap-x-2 gap-y-1 border-t border-white/[0.06] pt-1.5">
                      {entry.sources.map((source) => {
                        const href = safeExternalUrl(source.url);
                        if (href === null) return null;
                        return (
                          <a
                            key={href}
                            href={href}
                            target="_blank"
                            rel="noreferrer noopener"
                            title={source.title}
                            className="inline-flex max-w-full items-center gap-1 text-[10px] text-amber-300/80 hover:text-amber-200"
                          >
                            <ExternalLink size={9} className="shrink-0" />
                            <span className="truncate">{source.title}</span>
                          </a>
                        );
                      })}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        </div>

        {/* â”€â”€ Transcript ticker â”€â”€ */}
        {settings.showTranscriptTicker && ended === null && (
          <div className="border-t border-white/5 px-4 py-2">
            <p className="line-clamp-1 text-[11px] leading-relaxed text-slate-400/80">
              {finalLines.length > 0 ? finalLines[finalLines.length - 1] : "—"}
            </p>
            <p className="line-clamp-1 text-[11px] italic leading-relaxed text-amber-300/70">
              {interimText.length > 0 ? interimText : ""}
            </p>
          </div>
        )}

        {/* â”€â”€ Ask box (the dialogue input) â”€â”€ */}
        {ended === null && (
          <div className="border-t border-white/5 px-4 py-3">
            <form onSubmit={onSubmitDraft} className="flex items-end gap-2">
              <textarea
                value={draft}
                onChange={(event) => setDraft(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === "Enter" && !event.shiftKey) {
                    onSubmitDraft(event);
                  }
                }}
                rows={1}
                maxLength={500}
                placeholder="Ask anything — I hear the call and suggestions appear here..."
                disabled={wsStatus !== "open"}
                className="input max-h-24 min-h-[38px] flex-1 resize-none rounded-xl py-2 text-[12.5px] leading-snug"
              />
              <button
                type="submit"
                disabled={wsStatus !== "open" || draft.trim().length === 0}
                title="Send"
                className="flex h-[38px] w-[38px] shrink-0 items-center justify-center rounded-xl bg-gradient-brand text-slate-950 shadow-glow transition-all hover:brightness-110 disabled:cursor-not-allowed disabled:opacity-40 disabled:shadow-none"
              >
                <SendHorizontal size={15} />
              </button>
            </form>
            <div className="mt-2 flex flex-wrap gap-1.5">
              {QUICK_ACTIONS.map((action) => (
                <button
                  key={action.label}
                  onClick={() => askCopilot(action.question)}
                  disabled={wsStatus !== "open"}
                  className="chip hover:border-amber-400/40 hover:bg-amber-400/10 hover:text-amber-200 disabled:opacity-40"
                >
                  <Sparkles size={9} /> {action.label}
                </button>
              ))}
              <span className="ml-auto hidden items-center gap-1 text-[9px] text-slate-600 sm:flex">
                Enter <CornerDownLeft size={9} />
              </span>
            </div>
          </div>
        )}

        {/* â”€â”€ Controls / summary â”€â”€ */}
        <div className="border-t border-white/5 px-4 py-3">
          {ended !== null ? (
            <div className="animate-fade-in">
              <p className="mb-3 text-center text-xs font-bold uppercase tracking-[0.2em] text-emerald-300">
                Session complete · {formatDuration(ended.durationSeconds)}
              </p>
              <div className="mb-3 grid grid-cols-4 gap-2 text-center">
                {(
                  [
                    ["Fillers", ended.summary.total_filler_words, "text-amber-300"],
                    ["Pacing", ended.summary.pacing_alerts, "text-amber-300"],
                    ["Missed", ended.summary.script_misses, "text-rose-300"],
                    ["Cards", ended.summary.context_cards, "text-amber-300"],
                  ] as Array<[string, number, string]>
                ).map(([label, value, color]) => (
                  <div key={label} className="rounded-xl border border-white/5 bg-white/[0.03] py-2.5">
                    <p className={cn("text-lg font-bold tabular-nums", color)}>
                      {String(value)}
                    </p>
                    <p className="text-[10px] uppercase tracking-wider text-slate-500">
                      {label}
                    </p>
                  </div>
                ))}
              </div>
              {/* Post-session debrief */}
              <div className="mb-3 rounded-xl border border-white/5 bg-white/[0.03] p-3">
                <p className="mb-1.5 flex items-center gap-1.5 text-[10px] font-bold uppercase tracking-widest text-amber-300">
                  <Sparkles size={11} /> Debrief
                </p>
                {debrief === null ? (
                  <p className="flex items-center gap-1.5 text-[11px] text-slate-500">
                    <Loader2 size={11} className="animate-spin" /> Preparing your
                    debrief…
                  </p>
                ) : (
                  <>
                    <p className="text-[12px] leading-snug text-slate-300">
                      {debrief.summary}
                    </p>
                    <ul className="mt-2 flex flex-col gap-1.5">
                      {debrief.tips.map((tip, index) => (
                        <li
                          key={index}
                          className="flex gap-1.5 text-[11.5px] leading-snug text-slate-400"
                        >
                          <span className="shrink-0 text-amber-400/80">•</span>
                          <span>{tip}</span>
                        </li>
                      ))}
                    </ul>
                  </>
                )}
              </div>
              <button onClick={leaveSession} className="btn-primary w-full">
                <ArrowLeft size={15} /> Back to dashboard
              </button>
            </div>
          ) : (
            <div className="flex flex-col gap-2">
              {/* teleprompter toggle */}
              {prompterAvailable && (
                <button
                  onClick={() => setPrompterOn((on) => !on)}
                  className={cn(
                    "flex items-center justify-center gap-1.5 rounded-xl border px-3 py-1.5 text-[11px] font-medium transition-colors",
                    prompterOn
                      ? "border-amber-400/40 bg-amber-500/10 text-amber-300"
                      : "border-white/10 text-slate-400 hover:border-white/20 hover:text-slate-200",
                  )}
                >
                  <BookOpen size={12} />
                  {prompterOn ? "Teleprompter on" : "Teleprompter"}
                </button>
              )}

              {!recorder.isRecording ? (
                <div className="flex flex-col gap-2">
                  <button
                    onClick={() => void startMic()}
                    disabled={
                      wsStatus === "connecting" ||
                      wsStatus === "reconnecting" ||
                      sessionId === null ||
                      effectiveToken === null
                    }
                    className="btn-primary btn-sheen w-full animate-pulse-ring"
                  >
                    <Mic size={15} /> Start speaking
                  </button>
                  {screenAvailable && (
                    <button
                      onClick={() => void startCapture(true)}
                      disabled={
                        wsStatus === "connecting" ||
                        wsStatus === "reconnecting" ||
                        sessionId === null ||
                        effectiveToken === null
                      }
                      className="flex w-full items-center justify-center gap-1.5 rounded-xl border border-amber-400/30 bg-amber-500/10 px-3 py-2.5 text-[12px] font-semibold text-amber-200 transition-colors hover:border-amber-400/50 hover:bg-amber-500/15 disabled:cursor-not-allowed disabled:opacity-40"
                    >
                      <Monitor size={14} /> Share screen + call audio
                    </button>
                  )}
                  {screenAvailable && (
                    <p className="text-center text-[9px] leading-tight text-slate-600">
                      Pick the call tab or window, then tick “Share system audio”.
                    </p>
                  )}
                </div>
              ) : (
                <div className="flex flex-col gap-2">
                  {screenAvailable && !recorder.screenActive && (
                    <button
                      onClick={() => void startScreen()}
                      className="flex w-full items-center justify-center gap-1.5 rounded-xl border border-amber-400/30 bg-amber-500/10 px-3 py-2 text-[11.5px] font-semibold text-amber-200 transition-colors hover:border-amber-400/50 hover:bg-amber-500/15"
                    >
                      <Monitor size={13} /> Share screen + call audio
                    </button>
                  )}
                  {screenAvailable && !recorder.screenActive && (
                    <p className="text-center text-[9px] leading-tight text-slate-600">
                      Pick the call tab or window, then tick "Share system
                      audio".
                    </p>
                  )}
                  {recorder.screenActive && (
                    <button
                      onClick={stopScreen}
                      className="flex w-full items-center justify-center gap-1.5 rounded-xl border border-emerald-500/40 bg-emerald-500/10 px-3 py-2 text-[11.5px] font-semibold text-emerald-300 transition-colors hover:bg-emerald-500/15"
                    >
                      <Eye size={13} /> Screen sharing on
                      {recorder.screenAudioActive ? " · call audio" : ""} — stop
                    </button>
                  )}
                  <button onClick={endSession} className="btn-danger w-full">
                    <MicOff size={15} /> End session
                  </button>
                </div>
              )}

              <div className="flex items-center justify-between">
                {(wsStatus === "closed" || wsStatus === "idle") &&
                  sessionId !== null && (
                    <button onClick={connect} className="btn-outline text-xs">
                      Reconnect
                    </button>
                  )}
                {wsStatus === "connecting" && (
                  <span className="flex items-center px-2 text-slate-500">
                    <Loader2 size={16} className="animate-spin" />
                  </span>
                )}
                <p className="text-[9px] leading-tight text-slate-600">
                  {pipMode
                    ? "Share the call tab or window (not your whole screen) — this overlay stays out of it."
                    : "Share the call tab or window. A whole-screen share includes this overlay."}
                </p>
              </div>
              <p className="mt-1 text-center text-[9px] text-slate-600">
                <kbd className="rounded border border-white/10 px-1 py-0.5 font-sans">Space</kbd>{" "}
                start/stop ·{" "}
                <kbd className="rounded border border-white/10 px-1 py-0.5 font-sans">Esc</kbd>{" "}
                end ·{" "}
                <kbd className="rounded border border-white/10 px-1 py-0.5 font-sans">C</kbd>{" "}
                copy
              </p>
            </div>
          )}
        </div>
      </div>
    </main>
  );
}
