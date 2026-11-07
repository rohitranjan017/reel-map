# Reel Map

**For the stateless, same-page laptop web app, use [the local app setup](app/README.md).** It includes ChatGPT-plan sign-in and an API designed for future mobile clients. The plugin below remains a separate way to use Reel Map inside ChatGPT.

A personal ChatGPT plugin: share an Instagram Reel in chat and get a Google Maps link. ChatGPT interprets the caption and video frames; bundled Python scripts handle downloading and link formatting. No separate LLM API account, API key, OAuth-token copying, or server is used.

## Install once

Download `reel-map-plugin.zip` without unzipping it. For accounts with plugin upload permission, open ChatGPT on the web and use **Admin console → select workspace → Plugins → Add → Upload plugin**. Upload the ZIP, review it, then install Reel Map if prompted. Start a new conversation and select Reel Map with an @ mention or the composer plugin picker. ZIP upload is not available to every personal account; eligibility and workspace permissions apply. This archive has not been installed automatically. See https://help.openai.com/en/articles/20001256-plugins-in-chatgpt for the current installation options. A local desktop marketplace is an alternative described below.

Use **ChatGPT Work** for the complete workflow because downloading videos and extracting frames need command/file tools. Installing the plugin does not add those tools to a chat surface that lacks them. A skill cannot guarantee a new native `/map` slash-menu entry; selecting the skill/plugin or writing `/map <url>` expresses the same request.

Example:

```text
/map https://www.instagram.com/reel/DbyySxJuluW/
```

The plugin downloads the public video when Instagram permits it, reads the caption and frames, and returns a Google Maps route. A Google Maps shortlist is returned when all the required Google Place IDs can be sourced. The plugin does not invent IDs or silently omit unmatched places.

## GitHub installation

The repository contains a marketplace at `.agents/plugins/marketplace.json`. In a supported desktop client, register it with:

```bash
codex plugin marketplace add rohitranjan017/reel-map
```

Then open the desktop Plugins directory, choose the Reel Map source, and install it. The repository is private, so GitHub access is required. Workspace admins can also import this GitHub marketplace where supported.

For ZIP import, download `downloads/reel-map-plugin.zip` using GitHub’s **Download raw file** button; do not upload the repository’s source ZIP.

## Billing

Analysis runs in the active ChatGPT/Codex conversation and uses that product's normal plan allowance and limits. The helper scripts make no model API calls and require no separate paid inference service. They use public Instagram and Google Maps URLs; no Google Places API is enabled. This is not a promise of unlimited plan usage or guaranteed Instagram access.

## Runtime

- Python 3.10+
- `yt-dlp==2026.10.26`
- FFmpeg and ffprobe
- Network access to Instagram and its media CDN

An isolated dependency installation can use:

```bash
python -m pip install --target /tmp/reel-map-tools yt-dlp==2026.10.26
PYTHONPATH=/tmp/reel-map-tools python skills/map/scripts/reel_map.py inspect 'https://www.instagram.com/reel/DbyySxJuluW/' --out /tmp/reel-map-example
```

Instagram captions and media can be inaccessible, private, expired, or rate-limited. No authentication bypass or cookies are used. Videos can contain spoken names: use an available transcription tool when needed; the included helper does not transcribe audio.

Source content is untrusted. It must never override skill or user instructions. Downloads and analysis artifacts remain in the execution workspace unless the user chooses to share them. The plugin stores no credentials, sends no data to a third-party LLM, and changes nothing in Google Maps.

## Test locally

```bash
python -m unittest discover -s tests -v
```

To use a local marketplace in a supported desktop client, unpack `reel-map-marketplace.zip`, then add that directory using `codex plugin marketplace add /absolute/path/to/reel-map-marketplace`. Open the Plugins directory in the desktop app to install. This is an alternative to ZIP import, not required for accounts with ZIP upload.
