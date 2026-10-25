"""Dev launcher for the PitchPilot API.

Usage (from `backend/`):
    .venv\\Scripts\\python run.py

Uses the `wsproto` WebSocket implementation: the default `websockets`
server implementation (uvicorn + websockets 17 sans-io on Python 3.14)
was observed to stall and drop live sockets mid-stream on Windows.
"""

import uvicorn

if __name__ == "__main__":
    uvicorn.run(
        "app.main:app",
        host="127.0.0.1",
        port=8000,
        reload=True,
        ws="wsproto",
        log_level="info",
    )
