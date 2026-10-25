/**
 * Web "share-safe" overlay via the Document Picture-in-Picture API.
 *
 * The Electron shell achieves invisibility with `setContentProtection`
 * (WDA_EXCLUDEFROMCAPTURE). Browsers have no such OS-level API, but Chrome /
 * Edge's Document PiP gives the closest equivalent for the common case:
 *
 *   - the PiP window FLOATS ABOVE all other windows (Cluely-style), and
 *   - it is NEVER included when you share a browser tab or an app window —
 *     conferencing software captures the shared surface, not the PiP float.
 *
 * Caveat (surfaced to the user in the overlay): sharing the ENTIRE screen
 * captures every window, PiP included — so share the call tab/window.
 *
 * The PiP window gets its own React root with fresh Auth/App providers. Auth
 * re-hydrates from localStorage (same origin), so no login is needed; the
 * overlay then behaves exactly like the detached window — it creates its own
 * live session.
 */

import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import { AppProvider } from "@/context/AppContext";
import { AuthProvider } from "@/context/AuthContext";
import { OverlayWidget } from "@/components/OverlayWidget";

const PIP_SIZE = { width: 460, height: 780 } as const;

/** True when this document IS a PitchPilot PiP overlay. */
export function isPipOverlay(): boolean {
  return window.pitchpilotPip === true;
}

/** Feature-detect Document Picture-in-Picture in this browser. */
export function pipSupported(): boolean {
  return typeof window.documentPictureInPicture === "object"
    && window.documentPictureInPicture !== null;
}

/** Copy every stylesheet (dev <style> tags + prod <link> tags) into the PiP document. */
function portStylesheets(target: Document): void {
  for (const node of Array.from(document.querySelectorAll('style, link[rel="stylesheet"]'))) {
    target.head.appendChild(node.cloneNode(true));
  }
  // Base page styling: the PiP document has no CSS of its own by default.
  const base = target.createElement("style");
  base.textContent =
    "html,body{margin:0;padding:0;height:100%;background:#020617;overflow:hidden;}";
  target.head.appendChild(base);
}

/**
 * Open the always-on-top PiP overlay.
 *
 * Returns `true` when the PiP window was opened and mounted, `false` when the
 * browser does not support Document PiP (the caller should fall back to a
 * regular popup). Must be called from a user gesture.
 */
export async function openPipOverlay(): Promise<boolean> {
  const api = window.documentPictureInPicture;
  if (api === undefined || api === null) {
    return false;
  }
  try {
    const pip = await api.requestWindow({
      ...PIP_SIZE,
      disallowReturnToOpener: false,
    });
    pip.pitchpilotPip = true;
    portStylesheets(pip.document);

    const container = pip.document.createElement("div");
    container.id = "root";
    container.style.minHeight = "100vh";
    pip.document.body.appendChild(container);

    createRoot(container).render(
      <StrictMode>
        <AuthProvider>
          <AppProvider initialView="live">
            <OverlayWidget />
          </AppProvider>
        </AuthProvider>
      </StrictMode>,
    );
    return true;
  } catch {
    // Request was denied or failed — caller falls back to the popup window.
    return false;
  }
}
