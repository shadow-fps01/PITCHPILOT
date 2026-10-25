/**
 * useWebSocket — resilient duplex WebSocket manager for the live stream.
 *
 * - Sends binary PCM audio frames (queued while disconnected, capped).
 * - Reconnects with capped exponential backoff + jitter on abnormal closes.
 * - Heartbeats every 15 s; a 35 s silent watchdog forces a reconnect.
 * - `url` may be a string or an async resolver, so each connection attempt
 *   (including reconnects) can mint a fresh short-lived credential.
 * - `disconnect(true)` sends the graceful `stop` control message and waits
 *   for the server's `session_ended` reply; `disconnect(false)` bails hard.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import type { AudioSource, ClientMessage, ServerMessage, WsStatus } from "@/types";

export interface UseWebSocket {
  status: WsStatus;
  reconnectAttempt: number;
  connect: () => void;
  disconnect: (graceful: boolean) => void;
  /** Tagged PCM frame: presenter/mic or other party/shared call audio. */
  sendAudio: (chunk: ArrayBuffer, source?: AudioSource) => void;
  /** Send a JSON control message (ping/stop/capture) when the socket is open. */
  sendControl: (message: ClientMessage) => void;
  /** Send one browser-shared screen frame; dropped if the socket is down. */
  sendScreenFrame: (image: string) => void;
}

type UrlSource = string | (() => Promise<string>);

interface UseWebSocketOptions {
  url: UrlSource;
  autoConnect: boolean;
  onMessage: (message: ServerMessage) => void;
  onOpen?: () => void;
  onFinalClose?: () => void;
}

const MAX_RECONNECT_ATTEMPTS = 10;
const BASE_DELAY_MS = 1000;
const MAX_DELAY_MS = 15000;
const PING_INTERVAL_MS = 15000;
const PONG_WATCHDOG_MS = 35000;
const MAX_QUEUED_CHUNKS = 60;

// `PPA1` + source byte + PCM16. The server treats untagged legacy frames as
// presenter audio, so a stale tab cannot break the live stream.
const AUDIO_MAGIC = new Uint8Array([0x50, 0x50, 0x41, 0x31]);
const AUDIO_PRESENTER = 1;
const AUDIO_OTHER = 2;

function tagAudioFrame(chunk: ArrayBuffer, source: AudioSource): ArrayBuffer {
  const tagged = new Uint8Array(AUDIO_MAGIC.length + 1 + chunk.byteLength);
  tagged.set(AUDIO_MAGIC, 0);
  tagged[AUDIO_MAGIC.length] =
    source === "other" ? AUDIO_OTHER : AUDIO_PRESENTER;
  tagged.set(new Uint8Array(chunk), AUDIO_MAGIC.length + 1);
  return tagged.buffer;
}

export function useWebSocket({
  url,
  autoConnect,
  onMessage,
  onOpen,
  onFinalClose,
}: UseWebSocketOptions): UseWebSocket {
  const [status, setStatus] = useState<WsStatus>("idle");
  const [reconnectAttempt, setReconnectAttempt] = useState(0);

  const wsRef = useRef<WebSocket | null>(null);
  const queueRef = useRef<ArrayBuffer[]>([]);
  const attemptRef = useRef(0);
  const reconnectTimerRef = useRef<number | null>(null);
  const pingTimerRef = useRef<number | null>(null);
  const lastPongRef = useRef(0);
  const intentionalCloseRef = useRef(false);
  const mintingRef = useRef(false);
  const urlRef = useRef<UrlSource>(url);
  const connectRef = useRef<() => void>(() => {});

  // Latest-callback refs (no manager re-instantiation on parent renders).
  const onMessageRef = useRef(onMessage);
  const onOpenRef = useRef(onOpen);
  const onFinalCloseRef = useRef(onFinalClose);

  useEffect(() => {
    onMessageRef.current = onMessage;
    onOpenRef.current = onOpen;
    onFinalCloseRef.current = onFinalClose;
  }, [onMessage, onOpen, onFinalClose]);

  useEffect(() => {
    urlRef.current = url;
  }, [url]);

  const clearTimers = useCallback(() => {
    if (reconnectTimerRef.current !== null) {
      window.clearTimeout(reconnectTimerRef.current);
      reconnectTimerRef.current = null;
    }
    if (pingTimerRef.current !== null) {
      window.clearInterval(pingTimerRef.current);
      pingTimerRef.current = null;
    }
  }, []);

  const sendJson = useCallback((message: ClientMessage): void => {
    const ws = wsRef.current;
    if (ws !== null && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify(message));
    }
  }, []);

  const sendControl = sendJson;

  const scheduleReconnect = useCallback((): void => {
    attemptRef.current += 1;
    if (attemptRef.current > MAX_RECONNECT_ATTEMPTS) {
      setStatus("closed");
      onFinalCloseRef.current?.();
      return;
    }
    const delay = Math.min(
      MAX_DELAY_MS,
      BASE_DELAY_MS * 2 ** (attemptRef.current - 1),
    );
    const jittered = delay * (0.8 + Math.random() * 0.4); // ±20 %
    setStatus("reconnecting");
    setReconnectAttempt(attemptRef.current);
    reconnectTimerRef.current = window.setTimeout(() => {
      reconnectTimerRef.current = null;
      connectRef.current();
    }, jittered);
  }, []);

  const openSocket = useCallback(
    (target: string): void => {
      const existing = wsRef.current;
      if (
        existing !== null &&
        (existing.readyState === WebSocket.OPEN ||
          existing.readyState === WebSocket.CONNECTING)
      ) {
        return;
      }

      setStatus(attemptRef.current > 0 ? "reconnecting" : "connecting");

      const ws = new WebSocket(target);
      ws.binaryType = "arraybuffer";
      wsRef.current = ws;

      ws.onopen = () => {
        attemptRef.current = 0;
        setReconnectAttempt(0);
        setStatus("open");
        lastPongRef.current = Date.now();

        // Flush audio queued during the outage (drop nothing but the oldest).
        const queued = queueRef.current;
        queueRef.current = [];
        for (const chunk of queued) {
          ws.send(chunk);
        }

        // Heartbeat + watchdog.
        pingTimerRef.current = window.setInterval(() => {
          sendJson({ type: "ping", timestamp: Date.now() });
          if (Date.now() - lastPongRef.current > PONG_WATCHDOG_MS) {
            ws.close(); // dead connection → onclose → backoff reconnect
          }
        }, PING_INTERVAL_MS);

        onOpenRef.current?.();
      };

      ws.onmessage = (event: MessageEvent) => {
        if (typeof event.data !== "string") return;
        try {
          const parsed = JSON.parse(event.data) as ServerMessage;
          if (
            typeof parsed !== "object" ||
            parsed === null ||
            !("type" in parsed)
          ) {
            return;
          }
          if (parsed.type === "pong") {
            lastPongRef.current = Date.now();
          }
          onMessageRef.current(parsed);
        } catch {
          /* malformed frame — ignore */
        }
      };

      ws.onclose = () => {
        clearTimers();
        // Only clear the ref if it still points at this socket: a stale close
        // from a previous connection must not orphan the current one.
        if (wsRef.current === ws) {
          wsRef.current = null;
        }
        if (intentionalCloseRef.current) {
          setStatus("closed");
          return;
        }
        scheduleReconnect();
      };

      ws.onerror = () => {
        /* onclose follows; nothing to do here */
      };
    },
    [clearTimers, scheduleReconnect, sendJson],
  );

  const connect = useCallback((): void => {
    const existing = wsRef.current;
    if (
      existing !== null &&
      (existing.readyState === WebSocket.OPEN ||
        existing.readyState === WebSocket.CONNECTING)
    ) {
      return;
    }
    if (mintingRef.current) return;

    intentionalCloseRef.current = false;
    const source = urlRef.current;

    if (typeof source === "string") {
      if (source.length === 0) return;
      openSocket(source);
      return;
    }

    // Async resolver: mint a fresh credential before each attempt.
    mintingRef.current = true;
    setStatus(attemptRef.current > 0 ? "reconnecting" : "connecting");
    void source()
      .then((target) => {
        if (intentionalCloseRef.current) return;
        if (target.length > 0) {
          openSocket(target);
        } else {
          setStatus("idle");
        }
      })
      .catch(() => {
        scheduleReconnect();
      })
      .finally(() => {
        mintingRef.current = false;
      });
  }, [openSocket, scheduleReconnect]);

  useEffect(() => {
    connectRef.current = connect;
  }, [connect]);

  const disconnect = useCallback(
    (graceful: boolean): void => {
      intentionalCloseRef.current = true;
      clearTimers();
      const ws = wsRef.current;
      if (ws === null) {
        setStatus("closed");
        return;
      }
      if (graceful && ws.readyState === WebSocket.OPEN) {
        sendJson({ type: "stop" }); // server replies with session_ended, then closes
        setStatus("closed");
        return;
      }
      if (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING) {
        ws.close(4000, "client-left");
      }
      setStatus("closed");
    },
    [clearTimers, sendJson],
  );

  const sendAudio = useCallback(
    (chunk: ArrayBuffer, source: AudioSource = "presenter"): void => {
      const tagged = tagAudioFrame(chunk, source);
      const ws = wsRef.current;
      if (ws !== null && ws.readyState === WebSocket.OPEN) {
        ws.send(tagged);
        return;
      }
      // Buffer during outages; shed the oldest frames if the cap is hit.
      queueRef.current.push(tagged);
      if (queueRef.current.length > MAX_QUEUED_CHUNKS) {
        queueRef.current.shift();
      }
    },
    [],
  );

  const sendScreenFrame = useCallback(
    (image: string): void => {
      // Screen frames are large and time-sensitive: never queue them during
      // an outage. The next live frame will carry the current screen.
      sendJson({ type: "screen_frame", image });
    },
    [sendJson],
  );

  // Auto-connect when a URL (or resolver) is provided; hard cleanup on unmount.
  useEffect(() => {
    if (!autoConnect) return;
    const source = urlRef.current;
    if (typeof source === "string" && source.length === 0) return;
    connect();
    return () => {
      intentionalCloseRef.current = true;
      clearTimers();
      const ws = wsRef.current;
      if (ws !== null && ws.readyState <= WebSocket.OPEN) {
        ws.close(4000, "unmount");
      }
      wsRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [url, autoConnect]);

  return {
    status,
    reconnectAttempt,
    connect,
    disconnect,
    sendAudio,
    sendControl,
    sendScreenFrame,
  };
}
