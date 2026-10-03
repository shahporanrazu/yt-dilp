"""Checking finished files with ffprobe and fixing them up with ffmpeg.

Every MP4 must contain a video stream AND an audio stream. A file without
sound is treated as a failed download, never as a result.
"""

import json
import os
import subprocess

from . import tools
from .errors import AppError

# Standard YouTube quality steps. A file's label is the smallest step that
# fits its SHORTER side, so a vertical 1080x1920 Short counts as 1080p.
QUALITY_STEPS = (144, 240, 360, 480, 720, 1080, 1440, 2160, 4320)


def quality_step(width, height):
    """1920x1080 -> 1080, 1080x1920 -> 1080, 1920x800 -> 1080, 854x480 -> 480."""
    sides = [s for s in (width, height) if s]
    if not sides:
        return None
    short = min(sides)
    for step in QUALITY_STEPS:
        if short <= step + 16:   # small tolerance for e.g. 1088-pixel encodes
            return step
    return short


CODEC_NAMES = {
    "h264": "H.264", "avc1": "H.264", "hevc": "H.265", "hev1": "H.265", "hvc1": "H.265",
    "vp9": "VP9", "vp09": "VP9", "vp8": "VP8", "av1": "AV1", "av01": "AV1",
    "aac": "AAC", "mp4a": "AAC", "opus": "Opus", "vorbis": "Vorbis",
    "mp3": "MP3", "ac3": "AC-3", "eac3": "E-AC-3", "flac": "FLAC",
}


def codec_name(codec):
    """'avc1.640028' -> 'H.264', 'mp4a.40.2' -> 'AAC', 'vp09.00.40.08' -> 'VP9'."""
    if not codec or codec == "none":
        return None
    base = codec.split(".")[0].lower()
    return CODEC_NAMES.get(base, base.upper())


def probe(path):
    """Run ffprobe and return its JSON (streams + format)."""
    result = subprocess.run(
        [tools.FFPROBE, "-v", "error", "-print_format", "json",
         "-show_streams", "-show_format", path],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
    if result.returncode != 0:
        raise AppError("The downloaded file couldn't be read.",
                       "Try the download again.", result.stderr.strip())
    return json.loads(result.stdout or "{}")


def _streams(info, kind):
    return [s for s in info.get("streams", [])
            if s.get("codec_type") == kind
            and not (s.get("disposition") or {}).get("attached_pic")]


def summarize(path, info):
    """What the file really contains, for the UI and the file name."""
    video = (_streams(info, "video") or [None])[0]
    audio = (_streams(info, "audio") or [None])[0]
    summary = {
        "size": os.path.getsize(path),
        "duration": float((info.get("format") or {}).get("duration") or 0),
        "video_codec": codec_name(video["codec_name"]) if video else None,
        "audio_codec": codec_name(audio["codec_name"]) if audio else None,
        "audio_bitrate": int(audio.get("bit_rate") or 0) // 1000 if audio else None,
        "width": video.get("width") if video else None,
        "height": video.get("height") if video else None,
    }
    summary["quality"] = quality_step(summary["width"], summary["height"])
    return summary


def finalize_mp4(path):
    """Verify video + audio, make the audio AAC, add +faststart. Returns a summary.

    The video stream is always copied untouched. The audio is copied when it
    is already AAC, otherwise re-encoded to AAC 192 kbps.
    """
    info = probe(path)
    if not _streams(info, "video"):
        raise AppError("The downloaded file has no picture, so it was discarded.",
                       "Try again or pick another quality.", f"ffprobe streams: {_stream_list(info)}")
    audio = _streams(info, "audio")
    if not audio:
        raise AppError("The downloaded file has no sound, so it was discarded.",
                       "Try again or pick another quality. Updating yt-dlp in Settings can help.",
                       f"ffprobe streams: {_stream_list(info)}")

    audio_is_aac = all(s.get("codec_name") == "aac" for s in audio)
    fixed = path + ".final.mp4"
    cmd = [tools.FFMPEG, "-hide_banner", "-nostdin", "-v", "error", "-y", "-i", path,
           "-map", "0:v:0", "-map", "0:a", "-c:v", "copy"]
    cmd += ["-c:a", "copy"] if audio_is_aac else ["-c:a", "aac", "-b:a", "192k"]
    cmd += ["-movflags", "+faststart", fixed]
    result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if result.returncode != 0:
        raise AppError("Finishing the MP4 file failed.", "Try the download again.",
                       result.stderr.strip())
    os.replace(fixed, path)

    # Check the final file again, exactly as the user will get it.
    info = probe(path)
    audio = _streams(info, "audio")
    if not _streams(info, "video") or not audio or any(s.get("codec_name") != "aac" for s in audio):
        raise AppError("The finished MP4 failed its final check.", "Try the download again.",
                       f"ffprobe streams: {_stream_list(info)}")
    return summarize(path, info)


def check_mp3(path):
    """Verify the audio-only download is a real MP3. Returns a summary."""
    info = probe(path)
    audio = _streams(info, "audio")
    if not audio or audio[0].get("codec_name") != "mp3":
        raise AppError("The MP3 conversion failed.", "Try the download again.",
                       f"ffprobe streams: {_stream_list(info)}")
    return summarize(path, info)


def _stream_list(info):
    return ", ".join(f"{s.get('codec_type')}:{s.get('codec_name')}"
                     for s in info.get("streams", [])) or "none"
