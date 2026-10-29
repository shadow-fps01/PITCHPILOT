/**
 * PitchPilot desktop overlay shell (copilot upgrade F2).
 *
 * Wraps the existing web overlay (frontend/) in an Electron BrowserWindow
 * with OS-level media capture protection: `setContentProtection(true)` maps
 * to WDA_EXCLUDEFROMCAPTURE on Windows (the window is entirely excluded from
 * screen recording / conferencing capture) and the macOS ScreenCaptureGuard
 * equivalent. The overlay stays fully visible to the person physically at
 * the monitor, while external screen-share hooks receive nothing at all.
 *
 * Usage (with the web app already running via `npm run dev`):
 *     npm start                → loads http://localhost:5173/?view=live
 *     PITCHPILOT_DEV_URL=... npm start   → custom URL
 *     PITCHPILOT_APP_URL=<file-or-url> npm start  → production build
 */

"use strict";

const { app, BrowserWindow, shell } = require("electron");
const path = require("path");

const DEV_URL =
  process.env.PITCHPILOT_DEV_URL || "http://localhost:5173/?view=live";
const APP_URL = process.env.PITCHPILOT_APP_URL || ""; // e.g. built index.html

function httpOrigin(value) {
  try {
    const parsed = new URL(value);
    return parsed.protocol === "http:" || parsed.protocol === "https:"
      ? parsed.origin
      : null;
  } catch {
    return null;
  }
}

/**
 * Only http(s) URLs may reach the OS browser or a child window. In particular
 * this blocks `file:`, `javascript:`, `data:` and custom protocol handlers,
 * which could otherwise be used to launch local programs from remote content.
 */
function isSafeWebUrl(value) {
  try {
    const parsed = new URL(value);
    if (parsed.protocol !== "http:" && parsed.protocol !== "https:") {
      return false;
    }
    if (parsed.username.length > 0 || parsed.password.length > 0) {
      return false;
    }
    return parsed.hostname.length > 0;
  } catch {
    return false;
  }
}

function isPaystackUrl(value) {
  try {
    const hostname = new URL(value).hostname.toLowerCase();
    return hostname === "paystack.com" || hostname.endsWith(".paystack.com");
  } catch {
    return false;
  }
}

/**
 * Hosted checkout must never inherit the overlay's 460x780 transparent,
 * always-on-top canvas. Open it in a normal, full-sized window that shares
 * the Electron session (so the callback still has the signed-in token).
 */
function createCheckoutWindow(url, parent) {
  const checkout = new BrowserWindow({
    width: 1100,
    height: 900,
    minWidth: 720,
    minHeight: 600,
    title: "PitchPilot Checkout",
    frame: true,
    transparent: false,
    backgroundColor: "#ffffff",
    alwaysOnTop: false,
    skipTaskbar: false,
    autoHideMenuBar: true,
    webPreferences: {
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
    },
  });

  // Payment pages contain sensitive data; keep them out of screen shares too.
  checkout.setContentProtection(true);
  checkout.loadURL(url);

  // Bank / 3-D Secure flows sometimes open a popup from the checkout page.
  // Let it open in a normal window (sharing this session) rather than a tiny
  // default Electron popup, and preserve the opener relationship. Only web
  // URLs are allowed; remote checkout content must never open file:// or a
  // custom protocol.
  checkout.webContents.setWindowOpenHandler(({ url: target }) => {
    if (!isSafeWebUrl(target)) {
      return { action: "deny" };
    }
    return {
      action: "allow",
      overrideBrowserWindowOptions: {
        width: 900,
        height: 800,
        frame: true,
        transparent: false,
        backgroundColor: "#ffffff",
        alwaysOnTop: false,
        webPreferences: {
          contextIsolation: true,
          nodeIntegration: false,
          sandbox: true,
        },
      },
    };
  });

  checkout.webContents.on("will-navigate", (event, target) => {
    if (!isSafeWebUrl(target)) {
      event.preventDefault();
    }
  });

  if (parent !== undefined && parent !== null && !parent.isDestroyed()) {
    // The protected overlay is always-on-top; hide it so it cannot cover the
    // checkout form. Restore it when the checkout window is closed.
    parent.hide();
    checkout.on("closed", () => {
      if (!parent.isDestroyed()) {
        parent.show();
        parent.focus();
      }
    });
  }

  return checkout;
}

function createProtectedWindow(url) {
  const win = new BrowserWindow({
    width: 460,
    height: 780,
    minWidth: 340,
    minHeight: 480,
    frame: false,
    transparent: true,
    alwaysOnTop: true,
    skipTaskbar: true,
    resizable: true,
    hasShadow: false,
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
      contextIsolation: true,
      nodeIntegration: false,
    },
  });

  // F2 — the core of this shell: the window is invisible to screen
  // recording and video-conferencing software (WDA_EXCLUDEFROMCAPTURE /
  // macOS screen-capture guard) while remaining visible on the monitor.
  win.setContentProtection(true);
  win.setAlwaysOnTop(true, "screen-saver");

  if (url.startsWith("http://") || url.startsWith("https://")) {
    win.loadURL(url);
  } else if (url.startsWith("file://")) {
    win.loadURL(url);
  } else {
    win.loadFile(url, { query: { view: "live" } });
  }

  // `window.location.assign(paystackUrl)` in the renderer must not replace
  // the protected overlay with the checkout page. Intercept any navigation
  // that leaves the app origin and hand it to a normal checkout window.
  const appOrigin = httpOrigin(url);
  win.webContents.on("will-navigate", (event, target) => {
    // Never let remote content navigate this privileged window to file://,
    // javascript:, data: or a custom protocol.
    if (!isSafeWebUrl(target)) {
      event.preventDefault();
      return;
    }
    const targetOrigin = httpOrigin(target);
    if (appOrigin !== null && targetOrigin === appOrigin) {
      return; // in-app navigation (e.g. ?view=live)
    }
    event.preventDefault();
    if (isPaystackUrl(target)) {
      createCheckoutWindow(target, win);
    } else {
      void shell.openExternal(target);
    }
  });

  // Same-origin popups (e.g. "Detached overlay window") open as additional
  // protected windows; anything external goes to the system browser. A
  // Paystack popup gets the full-sized checkout window instead. Unsafe
  // protocols are dropped entirely.
  win.webContents.setWindowOpenHandler(({ url: target }) => {
    if (!isSafeWebUrl(target)) {
      return { action: "deny" };
    }
    const parsed = new URL(target);
    if (appOrigin !== null && parsed.origin === appOrigin) {
      createProtectedWindow(target);
      return { action: "deny" };
    }
    if (isPaystackUrl(target)) {
      createCheckoutWindow(target, win);
      return { action: "deny" };
    }
    void shell.openExternal(target);
    return { action: "deny" };
  });

  return win;
}

app.whenReady().then(() => {
  createProtectedWindow(APP_URL.length > 0 ? APP_URL : DEV_URL);
  app.on("activate", () => {
    if (BrowserWindow.getAllWindows().length === 0) {
      createProtectedWindow(APP_URL.length > 0 ? APP_URL : DEV_URL);
    }
  });
});

app.on("window-all-closed", () => {
  if (process.platform !== "darwin") {
    app.quit();
  }
});
