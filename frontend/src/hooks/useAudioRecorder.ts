/**
 * useAudioRecorder — captures the microphone via AudioWorklet and emits
 * 16 kHz 16-bit PCM frames (~every 160 ms) as ArrayBuffers, plus a
 * throttled RMS mic level for the live overlay meter.
 *
 * It can also capture the audio of a browser-shared tab/window (the other
 * party on a call). Microphone and shared audio use separate worklets and are
 * tagged by source, so the server can transcribe them independently and keep
 * speaking metrics limited to the presenter.
 *
 * The same display stream feeds a low-frequency JPEG frame pump for the live
 * screen-vision copilot; frames are sent over the WebSocket and never stored
 * locally. `displaySurface` is exposed so the UI can warn when the user picked
 * the whole screen instead of a tab/window.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import type { AudioSource } from "@/types";

export type DisplaySurface = "monitor" | "window" | "browser" | null;

export interface ScreenCaptureCallbacks {
  /** Called with a downscaled JPEG (base64, no data-URL prefix). */
  onFrame?: (image: string) => void;
  /** Called when the user or browser stops the display share. */
  onEnded?: () => void;
}

export interface CaptureStartOptions extends ScreenCaptureCallbacks {
  /** Also request a tab/window share (with its audio) in the same gesture. */
  withScreen?: boolean;
}

export interface CaptureStartResult {
  screenActive: boolean;
  screenAudioActive: boolean;
  screenSurface: DisplaySurface;
}

export interface UseAudioRecorder {
  start: (
    onAudioChunk: (chunk: ArrayBuffer, source: AudioSource) => void,
    options?: CaptureStartOptions,
  ) => Promise<CaptureStartResult>;
  /** Add a screen share to an already-running mic capture. */
  startScreenShare: (
    options: ScreenCaptureCallbacks,
  ) => Promise<CaptureStartResult>;
  stop: () => void;
  /** Stop only the display share; the microphone keeps recording. */
  stopScreenShare: () => void;
  isRecording: boolean;
  screenActive: boolean;
  screenAudioActive: boolean;
  screenSurface: DisplaySurface;
  screenSupported: boolean;
  micLevel: number; // 0..1, for a VU meter
  error: string | null;
}

const WORKLET_URL = "/worklets/pcm-recorder.js";
const SCREEN_FRAME_INTERVAL_MS = 2500;
const SCREEN_FIRST_FRAME_DELAY_MS = 1200;
const SCREEN_MAX_WIDTH = 1280;
const SCREEN_MAX_HEIGHT = 720;
const SCREEN_JPEG_QUALITY = 0.62;

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

function isPermissionDenial(error: unknown): boolean {
  return (
    error instanceof DOMException &&
    (error.name === "NotAllowedError" || error.name === "SecurityError")
  );
}

function readDisplaySurface(display: MediaStream): DisplaySurface {
  const track = display.getVideoTracks()[0];
  const value = track?.getSettings().displaySurface;
  return value === "monitor" || value === "window" || value === "browser"
    ? value
    : null;
}

export function useAudioRecorder(): UseAudioRecorder {
  const [isRecording, setIsRecording] = useState(false);
  const [screenActive, setScreenActive] = useState(false);
  const [screenAudioActive, setScreenAudioActive] = useState(false);
  const [screenSurface, setScreenSurface] = useState<DisplaySurface>(null);
  const [micLevel, setMicLevel] = useState(0);
  const [error, setError] = useState<string | null>(null);

  const ctxRef = useRef<AudioContext | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const displayStreamRef = useRef<MediaStream | null>(null);
  const micSourceRef = useRef<MediaStreamAudioSourceNode | null>(null);
  const displaySourceRef = useRef<MediaStreamAudioSourceNode | null>(null);
  const displayGainRef = useRef<GainNode | null>(null);
  const micWorkletRef = useRef<AudioWorkletNode | null>(null);
  const displayWorkletRef = useRef<AudioWorkletNode | null>(null);
  const sinkRef = useRef<GainNode | null>(null);
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const frameTimerRef = useRef<number | null>(null);
  const firstFrameTimerRef = useRef<number | null>(null);
  const onChunkRef = useRef<
    ((chunk: ArrayBuffer, source: AudioSource) => void) | null
  >(null);
  const onScreenFrameRef = useRef<((image: string) => void) | null>(null);
  const onScreenEndedRef = useRef<(() => void) | null>(null);
  const isRecordingRef = useRef(false);
  const screenActiveRef = useRef(false);
  const screenSurfaceRef = useRef<DisplaySurface>(null);
  const levelTickRef = useRef(0);

  const screenSupported =
    typeof navigator !== "undefined" &&
    navigator.mediaDevices !== undefined &&
    typeof navigator.mediaDevices.getDisplayMedia === "function";

  const handlePortMessage = useCallback(
    (source: AudioSource, event: MessageEvent<unknown>) => {
      if (!isWorkletMessage(event.data)) return;
      if (event.data.type === "audio") {
        // Copy: WebSocket.send() detaches the buffer it is given.
        const buffer = event.data.samples.buffer.slice(0);
        onChunkRef.current?.(buffer, source);
      } else if (source === "presenter") {
        levelTickRef.current += 1;
        if (levelTickRef.current % 2 === 0) {
          // Speech RMS typically sits far below 1; boost for visualisation.
          setMicLevel(Math.min(1, event.data.rms * 3.5));
        }
      }
    },
    [],
  );

  const stopFramePump = useCallback(() => {
    if (frameTimerRef.current !== null) {
      window.clearInterval(frameTimerRef.current);
      frameTimerRef.current = null;
    }
    if (firstFrameTimerRef.current !== null) {
      window.clearTimeout(firstFrameTimerRef.current);
      firstFrameTimerRef.current = null;
    }
    const video = videoRef.current;
    if (video !== null) {
      video.pause();
      video.srcObject = null;
    }
    videoRef.current = null;
    canvasRef.current = null;
  }, []);

  const disconnectDisplayAudio = useCallback(() => {
    if (displayWorkletRef.current !== null) {
      displayWorkletRef.current.port.onmessage = null;
      displayWorkletRef.current.disconnect();
      displayWorkletRef.current = null;
    }
    displaySourceRef.current?.disconnect();
    displaySourceRef.current = null;
    displayGainRef.current?.disconnect();
    displayGainRef.current = null;
  }, []);

  const stopDisplay = useCallback(
    (notify: boolean) => {
      stopFramePump();
      const display = displayStreamRef.current;
      displayStreamRef.current = null;
      if (display !== null) {
        display.getTracks().forEach((track) => {
          track.onended = null;
          track.stop();
        });
      }
      disconnectDisplayAudio();

      const wasActive = screenActiveRef.current;
      screenActiveRef.current = false;
      screenSurfaceRef.current = null;
      setScreenActive(false);
      setScreenAudioActive(false);
      setScreenSurface(null);

      const ended = onScreenEndedRef.current;
      onScreenFrameRef.current = null;
      onScreenEndedRef.current = null;
      if (notify && wasActive) ended?.();
    },
    [disconnectDisplayAudio, stopFramePump],
  );

  const teardown = useCallback(() => {
    stopFramePump();
    onChunkRef.current = null;
    onScreenFrameRef.current = null;
    onScreenEndedRef.current = null;

    const display = displayStreamRef.current;
    displayStreamRef.current = null;
    if (display !== null) {
      display.getTracks().forEach((track) => {
        track.onended = null;
        track.stop();
      });
    }
    disconnectDisplayAudio();

    if (micWorkletRef.current !== null) {
      micWorkletRef.current.port.onmessage = null;
      micWorkletRef.current.disconnect();
      micWorkletRef.current = null;
    }
    micSourceRef.current?.disconnect();
    micSourceRef.current = null;
    sinkRef.current?.disconnect();
    sinkRef.current = null;
    streamRef.current?.getTracks().forEach((track) => track.stop());
    streamRef.current = null;
    void ctxRef.current?.close();
    ctxRef.current = null;

    isRecordingRef.current = false;
    screenActiveRef.current = false;
    screenSurfaceRef.current = null;
    setIsRecording(false);
    setScreenActive(false);
    setScreenAudioActive(false);
    setScreenSurface(null);
    setMicLevel(0);
  }, [disconnectDisplayAudio, stopFramePump]);

  const requestDisplay = useCallback(async (): Promise<MediaStream> => {
    if (!screenSupported) {
      throw new Error("Screen sharing is not available in this browser.");
    }
    return navigator.mediaDevices.getDisplayMedia({
      video: {
        frameRate: { ideal: 8, max: 15 },
        width: { ideal: 1920 },
        height: { ideal: 1080 },
      },
      audio: {
        echoCancellation: false,
        noiseSuppression: false,
        autoGainControl: false,
        channelCount: 1,
      },
      // Chrome/Edge hints: hide this overlay from the picker and let the
      // user switch the shared surface without restarting the capture.
      selfBrowserSurface: "exclude",
      surfaceSwitching: "include",
      systemAudio: "include",
    } as DisplayMediaStreamOptions);
  }, [screenSupported]);

  const attachDisplay = useCallback(
    async (
      display: MediaStream,
      options: ScreenCaptureCallbacks,
    ): Promise<DisplaySurface> => {
      const ctx = ctxRef.current;
      const sink = sinkRef.current;
      if (ctx === null || sink === null) {
        throw new Error("Audio capture is not ready.");
      }

      displayStreamRef.current = display;
      onScreenFrameRef.current = options.onFrame ?? null;
      onScreenEndedRef.current = options.onEnded ?? null;

      const surface = readDisplaySurface(display);
      screenSurfaceRef.current = surface;
      setScreenSurface(surface);

      const audioTracks = display.getAudioTracks();
      if (audioTracks.length > 0) {
        const source = ctx.createMediaStreamSource(display);
        const gain = ctx.createGain();
        gain.gain.value = 0.9;

        // A separate worklet keeps the other party's audio in its own source
        // stream. It is never mixed into the presenter's PCM frames.
        const worklet = new AudioWorkletNode(ctx, "pcm-recorder", {
          numberOfInputs: 1,
          numberOfOutputs: 1,
          outputChannelCount: [1],
        });
        worklet.port.onmessage = (event) =>
          handlePortMessage("other", event);
        source.connect(gain);
        gain.connect(worklet);
        worklet.connect(sink);

        displaySourceRef.current = source;
        displayGainRef.current = gain;
        displayWorkletRef.current = worklet;
        audioTracks.forEach((track) => {
          track.onended = () => setScreenAudioActive(false);
        });
      }

      screenActiveRef.current = true;
      setScreenActive(true);
      setScreenAudioActive(audioTracks.length > 0);

      const videoTrack = display.getVideoTracks()[0];
      if (videoTrack !== undefined) {
        videoTrack.onended = () => {
          if (displayStreamRef.current === display) {
            stopDisplay(true);
          }
        };
      }

      const video = document.createElement("video");
      video.muted = true;
      video.playsInline = true;
      video.srcObject = display;
      videoRef.current = video;
      canvasRef.current = document.createElement("canvas");

      try {
        await video.play();
      } catch {
        // A muted captured stream normally autoplays; audio capture is still
        // useful even if this browser refuses to paint the video element.
      }

      const pumpFrame = (): void => {
        if (displayStreamRef.current !== display) return;
        const width = video.videoWidth;
        const height = video.videoHeight;
        const canvas = canvasRef.current;
        if (width <= 0 || height <= 0 || canvas === null) return;

        const scale = Math.min(
          1,
          SCREEN_MAX_WIDTH / width,
          SCREEN_MAX_HEIGHT / height,
        );
        const targetWidth = Math.max(1, Math.round(width * scale));
        const targetHeight = Math.max(1, Math.round(height * scale));
        if (canvas.width !== targetWidth) canvas.width = targetWidth;
        if (canvas.height !== targetHeight) canvas.height = targetHeight;

        const context = canvas.getContext("2d");
        if (context === null) return;
        context.drawImage(video, 0, 0, targetWidth, targetHeight);
        const dataUrl = canvas.toDataURL("image/jpeg", SCREEN_JPEG_QUALITY);
        const comma = dataUrl.indexOf(",");
        if (comma >= 0) {
          onScreenFrameRef.current?.(dataUrl.slice(comma + 1));
        }
      };

      firstFrameTimerRef.current = window.setTimeout(
        pumpFrame,
        SCREEN_FIRST_FRAME_DELAY_MS,
      );
      frameTimerRef.current = window.setInterval(
        pumpFrame,
        SCREEN_FRAME_INTERVAL_MS,
      );

      return surface;
    },
    [handlePortMessage, stopDisplay],
  );

  const start = useCallback(
    async (
      onAudioChunk: (chunk: ArrayBuffer, source: AudioSource) => void,
      options: CaptureStartOptions = {},
    ): Promise<CaptureStartResult> => {
      if (isRecordingRef.current) {
        return {
          screenActive: screenActiveRef.current,
          screenAudioActive:
            (displayStreamRef.current?.getAudioTracks().length ?? 0) > 0,
          screenSurface: screenSurfaceRef.current,
        };
      }

      setError(null);
      onChunkRef.current = onAudioChunk;
      let display: MediaStream | null = null;

      try {
        if (options.withScreen) {
          display = await requestDisplay();
        }

        if (navigator.mediaDevices === undefined) {
          throw new Error("Audio capture is not available in this browser.");
        }
        const mic = await navigator.mediaDevices.getUserMedia({
          audio: {
            channelCount: 1,
            echoCancellation: true,
            noiseSuppression: true,
            autoGainControl: true,
          },
        });
        streamRef.current = mic;

        const ctx = new AudioContext();
        ctxRef.current = ctx;
        await ctx.audioWorklet.addModule(WORKLET_URL);

        // Silent output keeps the audio graph pulling without echoing the mic.
        const sink = ctx.createGain();
        sink.gain.value = 0;
        sink.connect(ctx.destination);
        sinkRef.current = sink;

        const source = ctx.createMediaStreamSource(mic);
        micSourceRef.current = source;
        const micWorklet = new AudioWorkletNode(ctx, "pcm-recorder", {
          numberOfInputs: 1,
          numberOfOutputs: 1,
          outputChannelCount: [1],
        });
        micWorklet.port.onmessage = (event) =>
          handlePortMessage("presenter", event);
        source.connect(micWorklet);
        micWorklet.connect(sink);
        micWorkletRef.current = micWorklet;

        if (ctx.state === "suspended") {
          await ctx.resume();
        }

        isRecordingRef.current = true;
        setIsRecording(true);

        let surface: DisplaySurface = null;
        if (display !== null) {
          surface = await attachDisplay(display, options);
        }

        return {
          screenActive: display !== null,
          screenAudioActive: (display?.getAudioTracks().length ?? 0) > 0,
          screenSurface: surface,
        };
      } catch (err) {
        if (display !== null) {
          display.getTracks().forEach((track) => {
            track.onended = null;
            track.stop();
          });
        }
        teardown();
        const denied = isPermissionDenial(err);
        setError(
          options.withScreen && denied
            ? "Screen sharing was cancelled. Start again with the mic only, or allow sharing."
            : denied
              ? "Microphone permission denied — PitchPilot needs mic access to coach you."
              : options.withScreen
                ? "Could not start screen and audio capture. Check your browser permissions."
                : "Could not start audio capture. Check that no other app is holding the microphone.",
        );
        throw err;
      }
    },
    [attachDisplay, handlePortMessage, requestDisplay, teardown],
  );

  const startScreenShare = useCallback(
    async (options: ScreenCaptureCallbacks): Promise<CaptureStartResult> => {
      if (!isRecordingRef.current) {
        throw new Error("Start the mic before sharing your screen.");
      }
      if (screenActiveRef.current) {
        return {
          screenActive: true,
          screenAudioActive:
            (displayStreamRef.current?.getAudioTracks().length ?? 0) > 0,
          screenSurface: screenSurfaceRef.current,
        };
      }

      setError(null);
      let display: MediaStream | null = null;
      try {
        display = await requestDisplay();
        const surface = await attachDisplay(display, options);
        return {
          screenActive: true,
          screenAudioActive: display.getAudioTracks().length > 0,
          screenSurface: surface,
        };
      } catch (err) {
        if (display !== null) {
          display.getTracks().forEach((track) => {
            track.onended = null;
            track.stop();
          });
        }
        stopDisplay(false);
        setError(
          isPermissionDenial(err)
            ? "Screen sharing was cancelled. Your mic is still on."
            : "Could not start screen sharing. Your mic is still on.",
        );
        throw err;
      }
    },
    [attachDisplay, requestDisplay, stopDisplay],
  );

  const stop = useCallback(() => {
    if (!isRecordingRef.current && !screenActiveRef.current) return;
    isRecordingRef.current = false;
    screenActiveRef.current = false;
    setIsRecording(false);
    teardown();
  }, [teardown]);

  const stopScreenShare = useCallback(() => {
    if (!screenActiveRef.current) return;
    stopDisplay(false);
  }, [stopDisplay]);

  // Unmount safety: never leave a mic or display stream open.
  useEffect(() => teardown, [teardown]);

  return {
    start,
    startScreenShare,
    stop,
    stopScreenShare,
    isRecording,
    screenActive,
    screenAudioActive,
    screenSurface,
    screenSupported,
    micLevel,
    error,
  };
}
