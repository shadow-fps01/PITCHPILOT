import { spawn, type ChildProcess } from "node:child_process";
import { existsSync } from "node:fs";
import net from "node:net";
import { join } from "node:path";
import { fileURLToPath, URL } from "node:url";
import { defineConfig, type Plugin, type ViteDevServer } from "vite";
import react from "@vitejs/plugin-react";

// ── PitchPilot backend auto-start ────────────────────────────────────────
// `npm run dev` boots the FastAPI API (backend/.venv) alongside Vite and
// stops it when the dev server exits. Set PITCHPILOT_NO_API=1 to opt out.

const backendDir = fileURLToPath(new URL("../backend", import.meta.url));
const backendHost = "127.0.0.1";
const backendPort = 8000;
const pythonExe =
  process.platform === "win32"
    ? join(backendDir, ".venv", "Scripts", "python.exe")
    : join(backendDir, ".venv", "bin", "python");

/** Resolve once the TCP port accepts connections. */
function probePort(host: string, port: number): Promise<boolean> {
  return new Promise((resolve) => {
    const socket = net.connect({ host, port });
    const finish = (up: boolean): void => {
      socket.destroy();
      resolve(up);
    };
    socket.once("connect", () => finish(true));
    socket.once("error", () => finish(false));
    socket.setTimeout(1500, () => finish(false));
  });
}

function startBackend(): ChildProcess | null {
  if (!existsSync(pythonExe)) {
    console.warn(
      `[api] backend venv not found at ${pythonExe} — run the backend setup first (see README), ` +
        "or create it with: cd backend && python -m venv .venv && .venv\\Scripts\\pip install -r requirements.txt",
    );
    return null;
  }
  const child = spawn(
    pythonExe,
    [
      "-m",
      "uvicorn",
      "app.main:app",
      "--host",
      backendHost,
      "--port",
      String(backendPort),
      // wsproto: uvicorn's default websockets impl stalls live sockets on
      // Python 3.14 + websockets 17 (see backend/requirements.txt).
      "--ws",
      "wsproto",
      "--log-level",
      "info",
    ],
    { cwd: backendDir, stdio: "inherit" },
  );
  child.on("error", (err) => console.error("[api] failed to start:", err));
  return child;
}

function stopBackend(child: ChildProcess): void {
  if (child.pid === undefined) return;
  if (process.platform === "win32") {
    // taskkill /T also reaps any grandchildren.
    spawn("taskkill", ["/pid", String(child.pid), "/T", "/F"], {
      stdio: "ignore",
    });
  } else {
    child.kill("SIGTERM");
  }
}

function pitchpilotApi(): Plugin {
  let child: ChildProcess | null = null;
  let cleanedUp = false;
  return {
    name: "pitchpilot-api",
    configureServer(_server: ViteDevServer) {
      void (async () => {
        if (process.env.PITCHPILOT_NO_API === "1") {
          console.log("[api] auto-start skipped (PITCHPILOT_NO_API=1)");
          return;
        }
        if (await probePort(backendHost, backendPort)) {
          console.log(
            `[api] backend already running on http://${backendHost}:${backendPort} — reusing it`,
          );
          return;
        }
        console.log(
          `[api] starting FastAPI backend on http://${backendHost}:${backendPort} ...`,
        );
        const spawned = startBackend();
        if (spawned === null) return;
        child = spawned;

        // Stop the backend whenever the Vite process goes away.
        // (Vite 5 has no closeServer hook; signal handlers cover Ctrl+C.)
        const cleanup = (): void => {
          if (cleanedUp) return;
          cleanedUp = true;
          if (child !== null) {
            console.log("[api] stopping backend");
            stopBackend(child);
            child = null;
          }
        };
        process.once("exit", cleanup);
        process.once("SIGINT", cleanup);
        process.once("SIGTERM", cleanup);

        spawned.on("exit", (code) => {
          if (!cleanedUp) {
            console.warn(`[api] backend exited early (code=${String(code)})`);
            child = null;
          }
        });
      })();
    },
  };
}

// https://vite.dev/config/
export default defineConfig({
  plugins: [react(), pitchpilotApi()],
  resolve: {
    alias: {
      "@": fileURLToPath(new URL("./src", import.meta.url)),
    },
  },
  server: {
    port: 5173,
    proxy: {
      // Same-origin REST + WebSocket access to the FastAPI backend —
      // avoids CORS entirely in development (ws proxy included).
      "/api": {
        target: `http://${backendHost}:${backendPort}`,
        changeOrigin: true,
        ws: true,
      },
    },
  },
  build: {
    sourcemap: false,
  },
});
