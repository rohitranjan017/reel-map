"""Isolated media subprocess. All user-content files live in a temporary folder."""
import argparse
import contextlib
import io
import json
import math
import os
import shutil
import subprocess
from pathlib import Path

from yt_dlp import YoutubeDL


def inspect(url, directory, audio=False):
    root = Path(directory)
    root.mkdir(parents=True, exist_ok=True)
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        raise ValueError("FFmpeg and ffprobe must be installed on this laptop.")
    options = {"outtmpl": str(root / "media_%(id)s.%(ext)s"), "noplaylist": True, "socket_timeout": 20,
               "retries": 0, "extractor_retries": 0, "quiet": True, "noprogress": True,
               "restrictfilenames": True, "max_filesize": 100 * 1024 * 1024,
               "format": "best[height<=720]/bestvideo[height<=720]+bestaudio/best", "merge_output_format": "mp4",
               "compat_opts": {"no-certifi"} if os.environ.get("SSL_CERT_FILE") else set()}
    # yt-dlp progress cannot corrupt the JSON protocol or enter server logs.
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        with YoutubeDL(options) as downloader:
            info = downloader.extract_info(url, download=True)
    if not info:
        raise ValueError("Instagram did not return accessible media.")
    if sum(p.stat().st_size for p in root.iterdir() if p.is_file()) > 150 * 1024 * 1024:
        raise ValueError("The media is too large for this prototype.")
    caption = str(info.get("description") or "")
    result = {"caption": caption[:20000], "images": [], "notes": [], "transcript": "", "duration": None}
    if len(caption) > 20000:
        result["notes"].append("The caption was truncated at 20,000 characters.")
    images = sorted(p for p in root.glob("media_*") if p.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"})
    for image in images[:20]:
        result["images"].append({"path": str(image), "label": "Post image", "kind": "post_images"})
    videos = sorted(p for p in root.glob("media_*") if p.suffix.lower() in {".mp4", ".webm", ".mkv", ".mov"})
    if not videos and not images:
        raise ValueError("The media download did not complete. The post may be private, expired, rate-limited, or too large.")
    for vi, video in enumerate(videos[:1]):
        probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", str(video)],
                               capture_output=True, text=True, check=True, timeout=20)
        duration = float(json.loads(probe.stdout)["format"]["duration"])
        if not math.isfinite(duration) or duration <= 0 or duration > 600:
            raise ValueError("Use a video shorter than ten minutes.")
        result["duration"] = duration
        step = max(.5, duration / 80)
        count = max(1, math.ceil(duration / step))
        pattern = str(root / f"sheet_{vi}_%03d.jpg")
        subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(video), "-an",
                        "-vf", f"fps=1/{step},scale=360:-2,tile=4x4", "-frames:v", str(math.ceil(count / 16)),
                        "-q:v", "3", pattern], check=True, capture_output=True, timeout=100)
        for i, sheet in enumerate(sorted(root.glob(f"sheet_{vi}_*.jpg"))):
            result["images"].append({"path": str(sheet), "kind": "video_frames",
                "label": f"Video contact sheet {i + 1}. Read left-to-right then top-to-bottom. First tile approximately {i * 16 * step:.1f}s; each tile advances {step:.2f}s."})
        result["notes"].append(f"Video sampled approximately every {step:.2f} seconds; brief or speech-only mentions may be missed.")
        if audio:
            try:
                from faster_whisper import WhisperModel
            except ImportError as exc:
                raise ValueError("Local speech support is not installed. Install app/requirements-audio.txt or switch speech off.") from exc
            from .auth import config_directory
            cache = config_directory() / "speech-models"
            model = WhisperModel("tiny", device="cpu", compute_type="int8", download_root=str(cache))
            segments, _ = model.transcribe(str(video), vad_filter=True)
            result["transcript"] = "\n".join(f"{s.start:.1f}s: {s.text.strip()}" for s in segments)[:20000]
            result["notes"].append("Speech was transcribed locally; proper names may be inaccurate.")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("url")
    parser.add_argument("directory")
    parser.add_argument("--audio", action="store_true")
    args = parser.parse_args()
    try:
        print(json.dumps(inspect(args.url, args.directory, args.audio), ensure_ascii=False))
    except Exception as exc:
        # Do not expose extractor internals, signed media links, or remote HTML.
        message = str(exc) if isinstance(exc, ValueError) else "Instagram media could not be downloaded or decoded. The post may be private, expired, or rate-limited."
        print(json.dumps({"error": message}))
        raise SystemExit(1)
