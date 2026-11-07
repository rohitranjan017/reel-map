import asyncio
import contextlib
import importlib.util
import json
import os
import re
import shutil
import signal
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlencode

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, StreamingResponse
from pydantic import BaseModel, Field

from .auth import PlanAccount, AuthError
from . import inference

ROOT = Path(__file__).resolve().parents[1]
STATIC = Path(__file__).parent / "static"
spec = importlib.util.spec_from_file_location("map_helpers", ROOT / "skills/map/scripts/reel_map.py")
helpers = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helpers)


class MapInput(BaseModel):
    url: str = Field(max_length=2000)
    model: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,100}$")
    include_audio: bool = False


async def media_process(url, directory, audio):
    args = [sys.executable, "-m", "app.media", url, directory]
    if audio:
        args.append("--audio")
    process = await asyncio.create_subprocess_exec(*args, cwd=ROOT, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE, start_new_session=os.name != "nt")
    try:
        stdout, _ = await asyncio.wait_for(process.communicate(), 240 if audio else 180)
    except (asyncio.CancelledError, TimeoutError):
        if process.returncode is None:
            if os.name != "nt":
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(process.pid, signal.SIGKILL)
            else:
                killer = await asyncio.create_subprocess_exec("taskkill", "/PID", str(process.pid), "/T", "/F",
                    stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
                await killer.wait()
            await process.wait()
        raise
    try:
        data = json.loads(stdout)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ValueError("The media downloader did not return a usable result.") from exc
    if process.returncode or data.get("error"):
        raise ValueError(data.get("error") or "Instagram download failed.")
    return data


def create_app(account=None, port=None):
    port = port or int(os.environ.get("REEL_MAP_PORT", "8765"))
    origin = f"http://127.0.0.1:{port}"
    account = account or PlanAccount()
    app = FastAPI(title="Reel Map", version="1.0.0", description="Stateless personal Reel-to-Maps API; no job/history database.")
    app.state.account = account
    app.state.busy = False

    @app.middleware("http")
    async def local_boundary(request, next_handler):
        if request.headers.get("host") != f"127.0.0.1:{port}":
            return JSONResponse({"error": "Open this app at " + origin}, 403)
        if request.method not in ("GET", "HEAD", "OPTIONS") and request.headers.get("origin") != origin:
            return JSONResponse({"error": "This action must come from the local Reel Map page."}, 403)
        try:
            size = int(request.headers.get("content-length", "0") or 0)
        except ValueError:
            return JSONResponse({"error": "Invalid request length."}, 400)
        if size < 0 or size > 10000:
            return JSONResponse({"error": "Request too large."}, 413)
        response = await next_handler(request)
        response.headers.update({"Cache-Control": "no-store", "Referrer-Policy": "no-referrer", "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"})
        return response

    @app.get("/")
    async def index():
        return FileResponse(STATIC / "index.html")

    @app.get("/app.js")
    async def client_script():
        return FileResponse(STATIC / "app.js", media_type="text/javascript")

    @app.get("/api/status")
    async def status():
        return {**account.status(), "ffmpeg_available": bool(shutil.which("ffmpeg") and shutil.which("ffprobe")),
                "audio_available": importlib.util.find_spec("faster_whisper") is not None, "busy": app.state.busy,
                "stateless": True, "billing": "chatgpt_plan_only"}

    @app.post("/api/auth/start")
    async def auth_start():
        try:
            attempt, url = account.begin(origin + "/auth/callback")
        except AuthError as exc:
            return JSONResponse({"error": str(exc)}, 400)
        response = JSONResponse({"authorize_url": url})
        response.set_cookie("reel_map_auth_attempt", attempt, max_age=600, httponly=True, samesite="lax", path="/auth/callback")
        return response

    @app.get("/auth/callback")
    async def callback(request: Request):
        try:
            await account.finish(request.cookies.get("reel_map_auth_attempt"), dict(request.query_params))
            target = "/"
        except AuthError as exc:
            target = "/?" + urlencode({"auth_error": str(exc)})
        except Exception:
            target = "/?auth_error=Sign-in+could+not+be+verified.+Please+try+again."
        response = RedirectResponse(target, status_code=303)
        response.delete_cookie("reel_map_auth_attempt", path="/auth/callback")
        return response

    @app.post("/api/auth/disconnect")
    async def disconnect():
        if app.state.busy:
            return JSONResponse({"error": "Wait for processing to finish before disconnecting."}, 409)
        confirmed = await account.disconnect()
        return {"disconnected": True, "remote_revocation_confirmed": confirmed}

    @app.get("/api/models")
    async def model_list():
        try:
            return {"models": await inference.models(account)}
        except AuthError as exc:
            return JSONResponse({"error": str(exc)}, 401)
        except Exception:
            return JSONResponse({"error": "Could not reach ChatGPT’s model catalog. Please retry."}, 502)

    @app.post("/api/v1/map", responses={200: {"content": {"text/event-stream": {}}}}, summary="Analyze a Reel without saving it")
    async def map_reel(body: MapInput):
        try:
            url = helpers.instagram_url(body.url)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, 400)
        if not account.status()["connected"]:
            return JSONResponse({"error": "Connect your ChatGPT plan first."}, 401)
        if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
            return JSONResponse({"error": "Install FFmpeg and ffprobe before analyzing videos."}, 503)
        if app.state.busy:
            return JSONResponse({"error": "Another Reel is being processed. Try again when it finishes."}, 429)
        app.state.busy = True

        async def events():
            def event(kind, payload):
                return "event: " + kind + "\ndata: " + json.dumps(payload, ensure_ascii=False) + "\n\n"
            try:
                yield event("progress", {"stage": "checking", "message": "Checking ChatGPT plan access…"})
                catalog = await inference.models(account)
                if body.model not in {m["id"] for m in catalog}:
                    raise ValueError("Choose a model available to your connected ChatGPT account.")
                with tempfile.TemporaryDirectory(prefix="reel-map-") as directory:
                    yield event("progress", {"stage": "download", "message": "Downloading the public Reel and sampling video frames…"})
                    evidence = await media_process(url, directory, body.include_audio)
                    yield event("progress", {"stage": "analysis", "message": "ChatGPT is identifying the places…"})
                    data = await inference.extract(account, body.model, evidence, url)
                    links = helpers.build_links({"title": data.get("title"), "source_url": url, "places": data["places"]})
                    methods = sorted({item["kind"] for item in evidence["images"]} | ({"caption"} if evidence["caption"] else set()) | ({"local_speech_transcript"} if evidence.get("transcript") else set()))
                    final = {"api_version": "1", "status": "ready" if links["place_count"] else "no_places",
                             **links, "notes": data.get("notes", ""), "sampling_notes": evidence["notes"], "analysis_methods": methods}
                # TemporaryDirectory has already removed every downloaded file.
                yield event("result", final)
            except (AuthError, inference.InferenceError, ValueError) as exc:
                yield event("error", {"error": str(exc)})
            except TimeoutError:
                yield event("error", {"error": "Media processing timed out. Try a shorter Reel or retry later."})
            except asyncio.CancelledError:
                raise
            except Exception:
                yield event("error", {"error": "Analysis could not complete. No result or media has been saved. Please retry."})
            finally:
                app.state.busy = False
        return StreamingResponse(events(), media_type="text/event-stream", headers={"X-Accel-Buffering": "no"})

    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.server:app", host="127.0.0.1", port=int(os.environ.get("REEL_MAP_PORT", "8765")), access_log=False)
