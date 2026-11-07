# Reel Map on your laptop

**Paste an Instagram Reel → AI identifies the places → open Google Maps.** The entire interaction stays on one page. There is no ChatGPT conversation handoff, database, queue, or saved-map dashboard.

The app uses the supported **ChatGPT plan usage** OAuth flow for personal open-source clients. It does not read `OPENAI_API_KEY`, copy Codex credentials, or fall back to separately billed API-key requests. Access remains subject to your account’s eligibility, consent, model permissions, and usage limits. A completed inference request, not login alone, confirms access.

## Start

1. Install **Python 3.10 or newer** and **FFmpeg** (including `ffprobe`).
   - macOS: `brew install ffmpeg`
   - Windows: `winget install Gyan.FFmpeg`, then open a new terminal
   - Ubuntu/Debian: `sudo apt install ffmpeg python3-venv`
2. Download this repository or the laptop ZIP and open a terminal in its top-level folder.
3. Start the app:

   ```bash
   # macOS / Linux
   python3 app/start.py
   ```

   ```powershell
   # Windows
   py app/start.py
   ```

   The launcher creates an isolated `app/.venv` and installs dependencies once. Open **http://127.0.0.1:8765**. Keep this terminal running; press Ctrl+C to stop.
4. Select **Continue with ChatGPT**. Review the requested plan access. The browser returns directly to the local app after authorization.
5. Select an available model, paste a public Instagram URL, and choose **Find the places**. Review the results and open Google Maps.

Use `127.0.0.1`, not `localhost`: the OAuth callback and local request checks use the former. No paid service is enabled automatically. If plan access is declined or unavailable, the app shows an error; it does not switch billing paths.

## What persists

- **No Reel URLs, captions, videos, results, or job history are saved by the application.** Temporary media is removed after success, failure, or cancellation. Results remain in page memory until cleared or reloaded.
- OAuth credentials are stored with owner-only file permissions outside the repository: `~/.config/reel-map` on Linux, `~/Library/Application Support/ReelMap` on macOS, or `%APPDATA%/ReelMap` on Windows. An opaque host ID is retained for the installation. You can override the location with `REEL_MAP_CONFIG_DIR`.
- Disconnect revokes the renewable session when reachable, clears local credentials, and retains the account/client registration for later sign-in. If revocation cannot be confirmed, the app tells you. You can also revoke access in ChatGPT Settings.
- Provider-side retention and ordinary browser/network behavior are separate from application history. Responses requests use `store: false`. Access logging is disabled so OAuth callback codes and user URLs do not enter server access logs.

## Media and speech

The downloader accesses public Instagram media without account cookies. Instagram may refuse, rate-limit, expire, or restrict a post. Videos must be under ten minutes and fit the download limits.

Frames are sampled across the video and analyzed with the caption. Very brief labels and speech-only places can be missed. To include speech, install the optional local transcription dependency:

```bash
# macOS / Linux
app/.venv/bin/python -m pip install -r app/requirements-audio.txt
```

```powershell
# Windows
app\.venv\Scripts\python.exe -m pip install -r app\requirements-audio.txt
```

Restart the app, then enable **Include speech with local transcription**. This downloads a small Whisper model on first use and runs it on your CPU. Only model weights are cached, not recordings or transcripts. No transcription API is called. Proper names in the local transcript may be inaccurate.

The result is a Google Maps route; it does not modify your Google account. Sourced Google Place-ID lookup is not configured, so this version does not invent a native Google Maps saved list. Longer routes split without dropping destinations.

## API for future mobile clients

The interface and processing API are separate:

- `GET /api/status` — connection/dependency status, no credentials
- `POST /api/auth/start` — begin local ChatGPT OAuth
- `GET /auth/callback` — loopback OAuth return
- `POST /api/auth/disconnect` — clear the authorized session
- `GET /api/models` — available models for the connected plan
- `POST /api/v1/map` — stateless mapping; streams `progress`, `result`, or `error` as server-sent events
- `GET /openapi.json` — machine-readable API schema

Example request body:

```json
{
  "url": "https://www.instagram.com/reel/DbyySxJuluW/",
  "model": "<an id returned by /api/models>",
  "include_audio": false
}
```

Android and iOS clients can reuse this request/result contract later. They will need their own share integration plus an appropriate deployment/authentication setup. The laptop prototype deliberately binds only to loopback and rejects browser writes from other origins; it is not exposed to your Wi-Fi network or the internet.

## Developer checks

```bash
app/.venv/bin/python -m unittest discover -s app/tests -v
python3 -m unittest discover -s tests -v
```

Tests exercise OAuth signature/nonce/state checks, local request boundaries, completion-only result acceptance, and cleanup on success/failure. Provider calls are mocked; live plan inference requires the owner’s one-time sign-in.

Protocol references: [plan registration](https://developers.openai.com/siwc/token-sharing-open-source/sign-in), [inference](https://developers.openai.com/siwc/token-sharing-open-source/models-and-inference), [limitations](https://developers.openai.com/siwc/token-sharing-open-source/preview-limitations).
