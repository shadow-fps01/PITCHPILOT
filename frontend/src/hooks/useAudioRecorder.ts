/**
 * useAudioRecorder — captures the microphone via AudioWorklet and emits
 * 16 kHz 16-bit PCM frames (~every 160 ms) as ArrayBuffers, plus a
 * throttled RMS mic level for the live overlay meter.
 */

import { useCallback, useEffect, useRef, useState } from "react";

export interface UseAudioRecorder {
  start: (onAudioChunk: (chunk: ArrayBuffer) => void) => Promise<void>;
  stop: () => void;
  isRecording: boolean;
  micLevel: number; // 0..1, for a VU meter
  error: string | null;
}

const WORKLET_URL = "/worklets/pcm-recorder.js";

interface WorkletAudioMessage {
  type: "audio";
  samples: Int16Array;
}

interface WorkletLevelMessage {
  type: "level";
  rms: number;
}

type WorkletMessage = WorkletAudioMessage | WorkletLevelMessage;

function isWorkletMessage(data: unknown): data is WorkletMessage {
  if (typeof data !== "object" || data === null) return false;
  const type = (data as { type?: unknown }).type;
  return type === "audio" || type === "level";
}

export function useAudioRecorder(): UseAudioRecorder {
  const [isRecording, setIsRecording] = useState(false);
  const [micLevel, setMicLevel] = useState(0);
  const [error, setError] = useState<string | null>(null);

  const ctxRef = useRef<AudioContext | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const sourceRef = useRef<MediaStreamAudioSourceNode | null>(null);
  const workletRef = useRef<AudioWorkletNode | null>(null);
  const onChunkRef = useRef<((chunk: ArrayBuffer) => void) | null>(null);
  const levelTickRef = useRef(0);

  const handlePortMessage = useCallback((event: MessageEvent<unknown>) => {
    if (!isWorkletMessage(event.data)) return;
    if (event.data.type === "audio") {
      // Copy: WebSocket.send() detaches the buffer it is given.
      const buffer = event.data.samples.buffer.slice(0);
      onChunkRef.current?.(buffer);
    } else {
      levelTickRef.current += 1;
      if (levelTickRef.current % 2 === 0) {
        // Speech RMS typically sits far below 1; boost for visualisation.
        setMicLevel(Math.min(1, event.data.rms * 3.5));
      }
    }
  }, []);

  const teardown = useCallback(() => {
    try {
      workletRef.current?.disconnect();
      sourceRef.current?.disconnect();
      streamRef.current?.getTracks().forEach((track) => track.stop());
      void ctxRef.current?.close();
    } catch {
      /* already torn down */
    }
    workletRef.current = null;
    sourceRef.current = null;
    streamRef.current = null;
    ctxRef.current = null;
    setMicLevel(0);
  }, []);

  const start = useCallback(
    async (onAudioChunk: (chunk: ArrayBuffer) => void) => {
      if (isRecording) return;
      setError(null);
      onChunkRef.current = onAudioChunk;

      try {
        if (navigator.mediaDevices === undefined) {
          throw new Error("Audio capture is not available in this browser.");
        }
        const stream = await navigator.mediaDevices.getUserMedia({
          audio: {
            channelCount: 1,
            echoCancellation: true,
            noiseSuppression: true,
            autoGainControl: true,
          },
        });
        streamRef.current = stream;

        const ctx = new AudioContext();
        ctxRef.current = ctx;
        await ctx.audioWorklet.addModule(WORKLET_URL);

        const source = ctx.createMediaStreamSource(stream);
        sourceRef.current = source;

        const worklet = new AudioWorkletNode(ctx, "pcm-recorder", {
          numberOfInputs: 1,
          numberOfOutputs: 1,
          outputChannelCount: [1],
        });
        worklet.port.onmessage = handlePortMessage;
        workletRef.current = worklet;

        source.connect(worklet);
        // Route silent output to the destination so the graph keeps pulling.
        const sink = ctx.createGain();
        sink.gain.value = 0;
        worklet.connect(sink);
        sink.connect(ctx.destination);

        if (ctx.state === "suspended") {
          await ctx.resume();
        }
        setIsRecording(true);
      } catch (err) {
        teardown();
        const denied =
          err instanceof DOMException &&
          (err.name === "NotAllowedError" || err.name === "SecurityError");
        setError(
          denied
            ? "Microphone permission denied — PitchPilot needs mic access to coach you."
            : "Could not start audio capture. Check that no other app is holding the microphone.",
        );
      }
    },
    [isRecording, handlePortMessage, teardown],
  );

  const stop = useCallback(() => {
    if (!isRecording) return;
    setIsRecording(false);
    onChunkRef.current = null;
    teardown();
  }, [isRecording, teardown]);

  // Unmount safety: never leave a mic stream open.
  useEffect(() => teardown, [teardown]);

  return { start, stop, isRecording, micLevel, error };
}
