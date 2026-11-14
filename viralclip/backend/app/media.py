"""FFmpeg/ffprobe wrappers. Always argv lists, never a shell, so filenames can't inject commands."""
import json
import subprocess
from pathlib import Path

from . import config

ALLOWED_EXTS = {".mp4", ".mov", ".webm", ".m4v", ".mkv"}
# ffprobe format_name values we accept (mp4/mov/m4v share the first; webm/mkv share the second)
ALLOWED_FORMATS = {"mov,mp4,m4a,3gp,3g2,mj2", "matroska,webm"}


class MediaError(Exception):
    """Message is safe to show to the user."""


def probe(path: Path) -> dict:
    r = subprocess.run(
        [config.FFPROBE, "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)],
        capture_output=True, text=True, timeout=120,
    )
    if r.returncode != 0:
        raise MediaError("This file could not be read as a video. It may be corrupted or incomplete.")
    info = json.loads(r.stdout or "{}")
    fmt = info.get("format", {})
    if fmt.get("format_name") not in ALLOWED_FORMATS:
        raise MediaError(f"Unsupported container ({fmt.get('format_name', 'unknown')}). Use MP4, MOV or WEBM.")
    streams = info.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"
                  and not s.get("disposition", {}).get("attached_pic")), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    if not video:
        raise MediaError("No video stream found in this file.")
    duration = float(fmt.get("duration") or video.get("duration") or 0)
    if duration <= 0:
        raise MediaError("Could not determine the video duration; the file may be corrupted.")
    num, _, den = (video.get("avg_frame_rate") or "0/1").partition("/")
    fps = float(num) / float(den) if den and float(den) else None
    # Rotation metadata (phone footage) swaps displayed width/height.
    rot = abs(int(float(video.get("tags", {}).get("rotate", 0)))) or abs(int(float(next(
        (sd.get("rotation", 0) for sd in video.get("side_data_list", []) if "rotation" in sd), 0))))
    w, h = video.get("width"), video.get("height")
    if rot in (90, 270):
        w, h = h, w
    return {
        "duration": duration, "width": w, "height": h, "fps": round(fps, 3) if fps else None,
        "video_codec": video.get("codec_name"), "audio_codec": audio.get("codec_name") if audio else None,
        "has_audio": audio is not None,
    }


def thumbnail(src: Path, dst: Path, at: float, width: int = 640) -> None:
    """Grab one frame. Also proves the video stream actually decodes."""
    r = subprocess.run(
        [config.FFMPEG, "-v", "error", "-y", "-ss", f"{at:.3f}", "-i", str(src), "-frames:v", "1",
         "-vf", f"scale={width}:-2", "-q:v", "3", str(dst)],
        capture_output=True, text=True, timeout=120,
    )
    if r.returncode != 0 or not dst.exists():
        raise MediaError("The video stream could not be decoded (unsupported codec or corrupted file).")
