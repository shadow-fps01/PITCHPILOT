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

  if (url.startsWith("http")) {
    win.loadURL(url);
  } else {
    win.loadFile(url, { query: { view: "live" } });
  }

  // Same-origin popups (e.g. "Detached overlay window") open as additional
  // protected windows; anything external goes to the system browser.
  win.webContents.setWindowOpenHandler(({ url: target }) => {
    try {
      const parsed = new URL(target);
      if (
        parsed.hostname === "localhost" ||
        parsed.hostname === "127.0.0.1"
      ) {
        createProtectedWindow(`${parsed.origin}/`);
        return { action: "deny" };
      }
    } catch {
      /* fall through to external */
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
