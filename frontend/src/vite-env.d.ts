/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_API_BASE_URL?: string;
  /** Optional public legal pages shown in the recording-consent dialog. */
  readonly VITE_PRIVACY_URL?: string;
  readonly VITE_TERMS_URL?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}

interface DocumentPictureInPictureOptions {
  width?: number;
  height?: number;
  preferInitialWindowPlacement?: boolean;
  disallowReturnToOpener?: boolean;
}

/**
 * Document Picture-in-Picture API (Chrome / Edge 116+): an always-on-top
 * mini-window whose document we control. Used for the web "share-safe"
 * overlay — PiP windows are never included when sharing a tab or window.
 */
interface DocumentPictureInPicture {
  requestWindow(options?: DocumentPictureInPictureOptions): Promise<Window>;
  readonly window: Window | null;
}

/**
 * Injected by the Electron preload script (desktop/main.js) when the overlay
 * runs inside the protected desktop shell — enables the "screen-share
 * protected" badge in the UI. `pitchpilotPip` is set by the web PiP overlay
 * launcher (src/lib/pipOverlay.ts) for the same badge in browsers.
 */
interface Window {
  readonly pitchpilotDesktop?: boolean;
  documentPictureInPicture?: DocumentPictureInPicture;
  pitchpilotPip?: boolean;
}
