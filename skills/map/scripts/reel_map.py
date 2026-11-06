#!/usr/bin/env python3
"""Download evidence for ChatGPT to inspect, or format model-extracted places.

No model calls, authentication tokens, paid APIs, or geocoding are used.
"""

import argparse
import json
import math
import re
import shutil
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlencode, urlsplit


def instagram_url(value):
    parsed = urlsplit(value.strip())
    if (parsed.scheme != "https" or parsed.hostname not in
            {"instagram.com", "www.instagram.com", "m.instagram.com"}
            or parsed.username or parsed.password or parsed.port not in (None, 443)):
        raise ValueError("Supply an HTTPS instagram.com Reel, post, or Story link.")
    if not re.fullmatch(r"/(?:reel|reels|p)/[A-Za-z0-9_-]+/?", parsed.path) and not re.fullmatch(
            r"/stories/[A-Za-z0-9_.]+/[0-9]+/?", parsed.path):
        raise ValueError("Expected an Instagram Reel, post, or individual Story URL.")
    return "https://www.instagram.com" + parsed.path.rstrip("/") + "/"


def write_json(path, value):
    path = Path(path).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def inspect_reel(url, out):
    url = instagram_url(url)
    try:
        from yt_dlp import YoutubeDL
    except ImportError as error:
        raise ValueError("yt-dlp is missing. Install yt-dlp==2026.10.26 into a task-local target.") from error
    out = Path(out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    options = {
        "outtmpl": str(out / "media_%(id)s.%(ext)s"),
        "noplaylist": True,
        "socket_timeout": 20,
        "retries": 0,
        "extractor_retries": 0,
        "max_filesize": 100 * 1024 * 1024,
        "quiet": True,
        "noprogress": True,
        "writeinfojson": False,
        "merge_output_format": "mp4",
        "format": "bestvideo+bestaudio/best" if ffmpeg else "best",
        "restrictfilenames": True,
    }
    with YoutubeDL(options) as downloader:
        info = downloader.extract_info(url, download=True)
    if not info:
        raise ValueError("Instagram returned no media information.")

    # Return only the evidence needed for interpretation, never signed media URLs
    # or the full extractor record. Video/CDN URLs can contain transient tokens.
    caption = str(info.get("description") or "")
    (out / "caption.txt").write_text(caption, encoding="utf-8")
    result = {
        "source_url": url,
        "title": str(info.get("title") or "Instagram post"),
        "uploader": info.get("uploader"),
        "caption": caption,
        "caption_file": str(out / "caption.txt"),
        "videos": [],
        "images": [],
        "contact_sheets": [],
        "warnings": [],
        "audio_transcribed": False,
        "sampling_interval_seconds": 1,
        "sampling_limit_seconds": 300,
    }
    videos = sorted(p for p in out.glob("media_*") if p.suffix.lower() in {".mp4", ".webm", ".mkv", ".mov"})
    images = sorted(p for p in out.glob("media_*") if p.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"})
    result["videos"] = [str(p) for p in videos]
    result["images"] = [str(p) for p in images]
    if videos and not (ffmpeg and ffprobe):
        result["warnings"].append("Video downloaded, but FFmpeg/ffprobe is unavailable. Frames have not been inspected.")
    for index, video in enumerate(videos):
        if not (ffmpeg and ffprobe):
            continue
        probe = subprocess.run(
            [ffprobe, "-v", "error", "-show_entries", "format=duration", "-of", "json", str(video)],
            check=True, capture_output=True, text=True, timeout=30,
        )
        duration = float(json.loads(probe.stdout)["format"]["duration"])
        limit = min(duration, 300)
        result.setdefault("durations_seconds", []).append(duration)
        if duration > 300:
            result["warnings"].append("Only the first 300 seconds were sampled. Inspect the remainder for a complete result.")
        # Use source fps before scaling so every source second remains represented.
        frame_count = max(1, math.ceil(limit))
        sheet_count = math.ceil(frame_count / 16)
        pattern = str(out / ("video_%02d_sheet_%%03d.jpg" % index))
        subprocess.run(
            [ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", str(video), "-t", str(limit),
             "-vf", "fps=1,scale=360:-2,tile=4x4", "-frames:v", str(sheet_count), "-q:v", "3", pattern],
            check=True, capture_output=True, text=True, timeout=180,
        )
        sheets = sorted(out.glob("video_%02d_sheet_*.jpg" % index))
        for number, sheet in enumerate(sheets):
            result["contact_sheets"].append({
                "path": str(sheet),
                "first_approximate_second": number * 16,
                "reading_order": "left to right, then top to bottom; one second per tile",
            })
    if not videos and not images:
        result["warnings"].append("No downloaded video or post images were found. Do not claim the media was inspected.")
    write_json(out / "inspection.json", result)
    return result


def chunks_with_overlap(places, maximum):
    if not places:
        return []
    chunks = []
    start = 0
    while True:
        part = places[start:start + maximum]
        chunks.append(part)
        if start + maximum >= len(places):
            break
        start += maximum - 1
    return chunks


def location(place):
    return ", ".join(x for x in [place["name"], place.get("address", "")] if x)


def route_link(places):
    if len(places) == 1:
        params = {"api": "1", "query": location(places[0])}
        if places[0].get("google_place_id"):
            params["query_place_id"] = places[0]["google_place_id"]
        return "https://www.google.com/maps/search/?" + urlencode(params)
    params = {"api": "1", "origin": location(places[0]), "destination": location(places[-1]), "travelmode": "walking"}
    if len(places) > 2:
        params["waypoints"] = "|".join(location(p) for p in places[1:-1])
    if places[0].get("google_place_id"):
        params["origin_place_id"] = places[0]["google_place_id"]
    if places[-1].get("google_place_id"):
        params["destination_place_id"] = places[-1]["google_place_id"]
    if len(places) > 2 and all(p.get("google_place_id") for p in places[1:-1]):
        params["waypoint_place_ids"] = "|".join(p["google_place_id"] for p in places[1:-1])
    return "https://www.google.com/maps/dir/?" + urlencode(params)


def make_routes(places, maximum):
    # Respect Google's 2048-character limit, including very long addresses/IDs.
    pending = chunks_with_overlap(places, maximum)
    complete = []
    while pending:
        part = pending.pop(0)
        url = route_link(part)
        if len(url) <= 2048:
            complete.append({"places": [p["name"] for p in part], "url": url})
        elif len(part) > 2:
            half = max(2, (len(part) + 1) // 2)
            pending = [part[:half], part[half - 1:]] + pending
        else:
            raise ValueError("A location is too long for a Google Maps URL. Shorten its name/address.")
    return complete


def build_links(data):
    if not isinstance(data, dict) or not isinstance(data.get("places"), list):
        raise ValueError("Expected an object containing a places array.")
    raw = data["places"]
    if len(raw) > 100:
        raise ValueError("At most 100 places are supported per result.")
    places, seen = [], set()
    for item in raw:
        if not isinstance(item, dict) or not isinstance(item.get("name"), str) or not item["name"].strip():
            raise ValueError("Every place needs a nonempty name.")
        clean = {"name": item["name"].strip()}
        for field in ["address", "evidence", "google_place_id", "place_id_source"]:
            value = item.get(field, "")
            if not isinstance(value, str):
                raise ValueError("Place fields must be text.")
            clean[field] = value.strip()
        if len(clean["name"]) > 300 or len(clean["address"]) > 600:
            raise ValueError("Place name/address is too long.")
        if clean["google_place_id"]:
            if not re.fullmatch(r"[A-Za-z0-9_-]{8,500}", clean["google_place_id"]):
                raise ValueError("Google Place IDs must be URL-safe identifiers.")
            source = urlsplit(clean["place_id_source"])
            if source.scheme != "https" or not source.hostname:
                raise ValueError("Every Google Place ID needs an HTTPS place_id_source.")
        key = (clean["name"].casefold(), clean["address"].casefold())
        if key not in seen:
            seen.add(key)
            places.append(clean)
    result = {
        "title": str(data.get("title") or "Places from Instagram"),
        "source_url": str(data.get("source_url") or ""),
        "place_count": len(places),
        "places": places,
        "routes": make_routes(places, 10),
        "mobile_browser_routes": make_routes(places, 5),
        "shortlists": [],
        "unmatched_places": [p["name"] for p in places if not p["google_place_id"]],
        "order": "input order; not optimized",
    }
    if places and not result["unmatched_places"]:
        for start in range(0, len(places), 20):
            batch = places[start:start + 20]
            params = {"api": "1", "map_action": "shortlist", "place_ids": ",".join(p["google_place_id"] for p in batch)}
            url = "https://www.google.com/maps/@?" + urlencode(params)
            if len(url) > 2048:
                raise ValueError("Place IDs exceed Google's URL length limit. Use smaller groups.")
            result["shortlists"].append({"places": [p["name"] for p in batch], "url": url})
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    inspect = commands.add_parser("inspect", help="Download media, caption, and contact sheets for ChatGPT to inspect")
    inspect.add_argument("url")
    inspect.add_argument("--out", required=True)
    links = commands.add_parser("links", help="Build Maps URLs from places already extracted by ChatGPT")
    links.add_argument("input", help="Path to a UTF-8 JSON result")
    links.add_argument("--out")
    args = parser.parse_args()
    try:
        if args.command == "inspect":
            result = inspect_reel(args.url, args.out)
        else:
            result = build_links(json.loads(Path(args.input).read_text(encoding="utf-8")))
            if args.out:
                write_json(args.out, result)
        print(json.dumps(result, indent=2, ensure_ascii=False))
    except Exception as error:
        print(json.dumps({"error": str(error), "kind": type(error).__name__}, ensure_ascii=False), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
