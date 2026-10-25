You are an expert systems architect and desktop application engineer. We need to upgrade our existing application codebase by adding advanced, ultra-low-latency real-time AI copilot capabilities. 

Do not generate a brand-new app architecture from scratch. Instead, integrate the following feature sets directly into our existing application flow:

### 1. Low-Latency Dual Audio Capture & Streaming Pipeline
- Add a background audio capture service utilizing native system bindings (e.g., node-core-audio / PyAudio).
- Configure it to simultaneously loopback and mix two channels: System Audio (incoming remote voice) and Microphone Audio (outgoing user voice).
- Pipe this raw audio stream directly into a persistent WebSocket client. 
- Route the stream to a high-speed STT provider endpoint (Deepgram Nova-2 / Groq Whisper-Large-V3) to receive sub-200ms real-time chunked text transcription updates.

### 2. Invisible Screen-Share OS Overlay Component
- Add a translucent window layer to the desktop application client using Electron/Tauri window settings.
- Implement system-level media capture protection to render the window completely invisible to screen-recording and video-conferencing software (e.g., using Electron's `win.setContentProtection(true)` or equivalent OS window flags).
- Ensure the overlay remains fully visible to the user physically looking at their monitor while returning an empty/black frame to external screen-sharing hooks.

### 3. Automated "Next-Topic" Anticipation (Zero-Prompting)
- Integrate a continuous semantic evaluation system over the incoming live transcript stream.
- When key intents or questions are flagged in the text, trigger an asynchronous background query to our existing vector database (RAG/Knowledge Base).
- Pre-fetch relevant context cards, answers, or resume details automatically BEFORE the speaker finishes their thought, completely bypassing the need for manual clicking or prompting.

### 4. Advanced Conversational Pacing (Teleprompter Mode)
- Modify the UI text rendering to act as a dynamic, voice-responsive teleprompter rather than raw static text bullets.
- Sync text-highlighting and auto-scrolling speeds directly to the user's active microphone amplitude and speaking rhythm.
- Build a smooth visual layout that subtly cues the user on conversational pacing, reducing mechanical pauses.

### 5. Multi-Modal Vision Integration
- Add a silent background worker thread that takes localized desktop screenshots or captures active code-editor bounds at fixed intervals (e.g., every 2 seconds).
- Feed these display frames to a high-throughput multi-modal vision model (like Gemini 1.5 Flash).
- Extract and parse relevant visual items—such as technical architecture diagrams, slide decks, or algorithmic coding windows—and feed that context back into the core prompt engine for unified audio-visual assistance.

Ensure all new operations run asynchronously to protect thread safety and prevent lagging in the UI overlay. Incorporate these features seamlessly into the existing system loops.
