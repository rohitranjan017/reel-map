"""Run from the repository with: python app/start.py"""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import venv
import webbrowser

ROOT = Path(__file__).resolve().parents[1]
ENV = ROOT / "app/.venv"
PYTHON = ENV / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def main():
    if sys.version_info < (3, 10):
        raise SystemExit("Install Python 3.10 or newer first.")
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        print("Install FFmpeg first. macOS: brew install ffmpeg | Windows: winget install Gyan.FFmpeg | Ubuntu: sudo apt install ffmpeg")
        return 1
    if not PYTHON.exists():
        print("Creating an isolated Python environment…")
        venv.create(ENV, with_pip=True)
    requirements = ROOT / "app/requirements.txt"
    stamp = ENV / ".requirements"
    expected = requirements.read_text()
    if not stamp.exists() or stamp.read_text() != expected:
        subprocess.run([str(PYTHON), "-m", "pip", "install", "-r", str(requirements)], check=True)
        stamp.write_text(expected)
    port = int(os.environ.get("REEL_MAP_PORT", "8765"))
    print(f"Open http://127.0.0.1:{port} and select Continue with ChatGPT.")
    subprocess.run([str(PYTHON), "-m", "app.server"], cwd=ROOT, check=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
