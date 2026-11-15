# HackUTA — ASL / English caption exchange

A minimal two-language codebase: Python translates short signing videos with Gemini and displays English captions; the Node.js agent transcribes English speech with ElevenLabs Scribe, cleans it using FreeLLM, and speaks each ASL translation into the call with an ElevenLabs voice. Both exchange JSON through one FastAPI server. A `voicemail.js` script turns signed messages into ElevenLabs voice clips. No training or avatar is included.

## 1. Open this folder in Visual Studio

Open three PowerShell terminals. Keep your existing working project as a backup. Copy your actual credentials from your old .env files into the matching new files; do not copy placeholders over actual secrets. Never commit credentials.

## 2. Start the shared server (one computer only)

From this folder:

```powershell
cd shared-backend
python -m pip install -r requirements.txt
Copy-Item .env.example .env
```

Edit `.env` and replace `SHARED_API_TOKEN` with a long random team token. Use the exact same token in all three environment files. Generate one with `python -c "import secrets; print(secrets.token_urlsafe(32))"`.

```powershell
python -m uvicorn shared_server:app --host 0.0.0.0 --port 8000
```

Open http://127.0.0.1:8000/health. Expected: `{"status":"ok"}`. Keep running. Use one server process; messages live in RAM and reset on restart. Restart both polling clients if you restart the server.

## 3. Python ASL agent (your side)

New PowerShell terminal from the project root:

```powershell
cd asl-agent
python -m pip install -r requirements.txt
Copy-Item .env.example .env
```

Fill in Gemini API key and existing LiveKit credentials. Set the shared token; `SHARED_API_URL=http://127.0.0.1:8000` works when the server is on this computer. The Gemini model is configurable; use a video-capable model available to your account. If dependencies do not install with Python 3.13, use Python 3.12 in a virtual environment (`py -3.12 -m venv .venv`, then `.venv\Scripts\python.exe -m pip install -r requirements.txt`).

```powershell
python sentence_agent.py
```

Space starts a clip; Space again stops it and requests translation. Recording stops automatically after eight seconds. Q quits. The translated caption is displayed and sent to the relay. Incoming English messages appear as English captions in the same window. The UI briefly waits during the synchronous Gemini request. Blank/uncertain model outputs are not sent.

For a test without camera or Gemini:

```powershell
python test_message.py "Can you help me?"
```

## 4. JavaScript English agent (teammate)

Use Node 24 and a new terminal from the project root:

```powershell
cd english-agent
npm ci
Copy-Item .env.local.example .env.local
```

Fill in the teammate's existing LiveKit and FreeLLM settings, shared URL and token. Speech to text now uses ElevenLabs Scribe, so add `ELEVENLABS_API_KEY` to `.env.local` (see the ElevenLabs section below). The original Deepgram/LiveKit setup is kept as a fallback (`STT_PROVIDER=deepgram`) and needs a working LiveKit inference configuration. Their FreeLLM model string is also unchanged.

Test the connection without Chert or FreeLLM:

```powershell
node receive_asl.js
```

This continuously reads ASL translations and displays them in the terminal. Leave it running. In another terminal in `english-agent`:

```powershell
node test_message.js "Yes, how can I help you?"
```

The Python webcam window should display the English reply. To start the original English speech agent with shared-API sending added:

```powershell
node agent.js dev
```

It registers as `english-speaker-agent`. Keep your teammate's existing Chert setup for dispatching it. The receiver is a separate process deliberately, to avoid changing the LiveKit voice lifecycle. It displays text only, not audio. The agent itself (`agent.js`) speaks each ASL translation aloud into the call.

## 5. Different laptops

If the server is on your laptop, run `ipconfig` on Windows and use its Wi-Fi IPv4 address in your teammate's `SHARED_API_URL`, e.g. `http://192.168.1.105:8000` (example only). Your own Python client can keep localhost. Both must be on a network that allows device-to-device access. Test the server health URL from the other laptop. Allow inbound TCP 8000 only on a trusted private network if necessary. Do not expose this prototype directly to the public internet; HTTP does not encrypt messages or the token. Use HTTPS, proper authentication and session isolation if hosting it publicly. The single token is a demo access check, not per-user authentication.

## 6. Chert 1 later

`asl-agent/agent.py` is a LiveKit registration and incoming-video smoke test, separate from the webcam app. Run `python agent.py dev`; in WSL, from the ASL folder, run `chert doctor --agent asl-agent` and (once the assigned line is available) `chert dev --agent asl-agent`. Confirm that your managed line forwards caller video. This file logs video frames but does not translate them yet. Integrating `sentence_agent.translate_video()` and a sentence-boundary mechanism into the LiveKit receiver is the next step. Native FaceTime does not automatically show these captions; an outgoing rendered video track or separate browser display would be needed.

## JSON contract

POST `/messages` with `X-API-Token`:

```json
{"source":"asl","original_text":"I need help","clean_text":"I need help.","intent":"statement"}
```

`source` is `asl` or `english`. `intent` is `statement`, `question`, `command`, `greeting`, or `other`. The server assigns an increasing `id`. GET `/messages/asl?after=0` or `/messages/english?after=0` returns messages for the requested source, after the supplied ID. Each receiver tracks the last ID. This is one shared demo conversation, not a production messaging system.

## Limits

The API relay supports polling approximately once per second. Messages disappear on server restart and failed sends are not retried automatically. Gemini is not a dedicated ASL translation model; validate its output with fluent ASL signers. The short-clip webcam pipeline is not continuous or guaranteed accurate. Video is sent to Google's API; obtain consent. No avatar or custom FaceTime caption rendering is included. The English voice agent requires its own credentials, including an ElevenLabs API key, and ElevenLabs usage spends that account's credits.

## ElevenLabs (voice, Scribe, voicemail)

The English agent uses ElevenLabs for both directions of audio:

- **Sign -> voice:** each new ASL translation on the shared server is spoken into the call (`eleven_flash_v2_5`, low latency).
- **Speech -> text:** the hearing person's speech is transcribed with Scribe realtime (`scribe_v2_realtime`). Set `STT_PROVIDER=deepgram` in `.env.local` to fall back to the original Deepgram setup.
- **Signed voicemail:** `node voicemail.js "text"` or `node voicemail.js --latest` saves an `.mp3` in `voicemails/` (`eleven_v4`, most expressive). Add `--play` to hear it. Send it by dragging the file into Messages.

Setup: `npm install`, then put `ELEVENLABS_API_KEY=` in `.env.local` (run `npx -y @elevenlabs/cli@latest onboard init` to create the file). Optional: `ELEVENLABS_VOICE_ID` (defaults to George, a pre-made voice every plan can use).

## Demo settings and tools

- **FreeLLM is optional.** Without `FREELLM_API_KEY`/`FREELLM_BASE_URL`, or if FreeLLM fails, the English agent sends the Scribe transcript to the signer as-is.
- **ASL agent settings** (environment variables, all optional):
  - `ASL_AGENT_NAME`: run the agent under a unique name (e.g. `asl-agent-yael`) so dispatches reach only your machine.
  - `MIN_MOTION` (default 3): how much movement starts a sign. Lower if signs are missed, raise if still moments trigger clips.
  - `SIGN_VOCAB` (default `YES,NO,PLEASE,THANK YOU,SORRY,HELP`): signs Gemini is told to expect.
  - `GEMINI_FALLBACK_MODELS`: models tried in order when one is overloaded or out of quota.
  - `GEMINI_MIN_INTERVAL` (default 13 s): spacing between Gemini requests for free-tier keys.
- **Signing detection:** a clip starts when the hands move and ends after 1 s of stillness. Sign one sign, drop your hands, and wait for the screen to show SIGN NOW again.
- **Caption screen status:** SIGN NOW (green), SIGNING (blue), TRANSLATING (amber), WAIT (grey countdown).
- **`node reply.js reply.m4a`** transcribes a recorded reply with ElevenLabs Scribe and sends it to the signer; `node reply.js --text "..."` sends typed text.
- **Browser demo (no FaceTime):** join rooms from meet.livekit.io with `lk token create`, then send agents in with `lk dispatch create --room <room> --agent-name <name>`. Join the room first, then dispatch.
