/**
 * Preload — runs in an isolated context before the web app loads.
 * Exposes only a capability flag; no privileged APIs are handed to the page.
 */

"use strict";

const { contextBridge } = require("electron");

contextBridge.exposeInMainWorld("pitchpilotDesktop", true);
