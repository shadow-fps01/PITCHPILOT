/**
 * useAudioRecorder — captures the microphone via AudioWorklet and emits
 * 16 kHz 16-bit PCM frames (~every 160 ms) as ArrayBuffers, plus a
 * throttled RMS mic level for the live overlay meter.
 *
 * It can optionally mix the audio of a browser-shared tab/window (system or
 * call audio) into the same PCM stream. The same display stream also feeds a
 * low-frequency JPEG frame pump for the live screen-vision copilot; frames
 * are sent over the WebSocket and never stored locally.
 */

import { useCallback, useEffect, useRef, useState } from "react";

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
}

export interface UseAudioRecorder {
  start: (
    onAudioChunk: (chunk: ArrayBuffer) => void,
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

export function useAudioRecorder(): UseAudioRecorder {
  const [isRecording, setIsRecording] = useState(false);
  const [screenActive, setScreenActive] = useState(false);
  const [screenAudioActive, setScreenAudioActive] = useState(false);
  const [micLevel, setMicLevel] = useState(0);
  const [error, setError] = useState<string | null>(null);

  const ctxRef = useRef<AudioContext | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const displayStreamRef = useRef<MediaStream | null>(null);
  const micSourceRef = useRef<MediaStreamAudioSourceNode | null>(null);
  const displaySourceRef = useRef<MediaStreamAudioSourceNode | null>(null);
  const displayGainRef = useRef<GainNode | null>(null);
  const mixerRef = useRef<GainNode | null>(null);
  const workletRef = useRef<AudioWorkletNode | null>(null);
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const frameTimerRef = useRef<number | null>(null);
  const firstFrameTimerRef = useRef<number | null>(null);
  const onChunkRef = useRef<((chunk: ArrayBuffer) => void) | null>(null);
  const onScreenFrameRef = useRef<((image: string) => void) | null>(null);
  const onScreenEndedRef = useRef<(() => void) | null>(null);
  const isRecordingRef = useRef(false);
  const screenActiveRef = useRef(false);
  const levelTickRef = useRef(0);

  const screenSupported =
    typeof navigator !== "undefined" &&
    navigator.mediaDevices !== undefined &&
    typeof navigator.mediaDevices.getDisplayMedia === "function";

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
      displaySourceRef.current?.disconnect();
      displayGainRef.current?.disconnect();
      displaySourceRef.current = null;
      displayGainRef.current = null;

      const wasActive = screenActiveRef.current;
      screenActiveRef.current = false;
      setScreenActive(false);
      setScreenAudioActive(false);

      const ended = onScreenEndedRef.current;
      onScreenFrameRef.current = null;
      onScreenEndedRef.current = null;
      if (notify && wasActive) ended?.();
    },
    [stopFramePump],
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

    displaySourceRef.current?.disconnect();
    displayGainRef.current?.disconnect();
    displaySourceRef.current = null;
    displayGainRef.current = null;

    if (workletRef.current !== null) {
      workletRef.current.port.onmessage = null;
      workletRef.current.disconnect();
      workletRef.current = null;
    }
    micSourceRef.current?.disconnect();
    micSourceRef.current = null;
    mixerRef.current?.disconnect();
    mixerRef.current = null;
    streamRef.current?.getTracks().forEach((track) => track.stop());
    streamRef.current = null;
    void ctxRef.current?.close();
    ctxRef.current = null;

    isRecordingRef.current = false;
    screenActiveRef.current = false;
    setIsRecording(false);
    setScreenActive(false);
    setScreenAudioActive(false);
    setMicLevel(0);
  }, [stopFramePump]);

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
    ): Promise<void> => {
      const ctx = ctxRef.current;
      const mixer = mixerRef.current;
      if (ctx === null || mixer === null) {
        throw new Error("Audio capture is not ready.");
      }

      displayStreamRef.current = display;
      onScreenFrameRef.current = options.onFrame ?? null;
      onScreenEndedRef.current = options.onEnded ?? null;

      const audioTracks = display.getAudioTracks();
      if (audioTracks.length > 0) {
        const source = ctx.createMediaStreamSource(display);
        const gain = ctx.createGain();
        gain.gain.value = 0.9;
        source.connect(gain);
        gain.connect(mixer);
        displaySourceRef.current = source;
        displayGainRef.current = gain;
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
    },
    [stopDisplay],
  );

  const start = useCallback(
    async (
      onAudioChunk: (chunk: ArrayBuffer) => void,
      options: CaptureStartOptions = {},
    ): Promise<CaptureStartResult> => {
      if (isRecordingRef.current) {
        return {
          screenActive: screenActiveRef.current,
          screenAudioActive:
            (displayStreamRef.current?.getAudioTracks().length ?? 0) > 0,
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

        const source = ctx.createMediaStreamSource(mic);
        micSourceRef.current = source;

        // A mixer is always present so a screen share can be added later
        // without rebuilding the audio graph.
        const mixer = ctx.createGain();
        mixer.gain.value = 0.9;
        mixerRef.current = mixer;
        source.connect(mixer);

        const worklet = new AudioWorkletNode(ctx, "pcm-recorder", {
          numberOfInputs: 1,
          numberOfOutputs: 1,
          outputChannelCount: [1],
        });
        worklet.port.onmessage = handlePortMessage;
        workletRef.current = worklet;

        mixer.connect(worklet);
        // Route silent output to the destination so the graph keeps pulling.
        const sink = ctx.createGain();
        sink.gain.value = 0;
        worklet.connect(sink);
        sink.connect(ctx.destination);

        if (ctx.state === "suspended") {
          await ctx.resume();
        }

        isRecordingRef.current = true;
        setIsRecording(true);

        if (display !== null) {
          await attachDisplay(display, options);
        }

        return {
          screenActive: display !== null,
          screenAudioActive: (display?.getAudioTracks().length ?? 0) > 0,
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
        };
      }

      setError(null);
      let display: MediaStream | null = null;
      try {
        display = await requestDisplay();
        await attachDisplay(display, options);
        return {
          screenActive: true,
          screenAudioActive: display.getAudioTracks().length > 0,
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
    screenSupported,
    micLevel,
    error,
  };
}
