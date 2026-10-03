"""Finding ffmpeg, ffprobe and Deno, and reporting versions.

run_checks() is called once at startup. Its results drive the status
panel and decide whether downloads are allowed at all.
"""

import importlib.metadata
import logging
import re
import shutil
import subprocess

log = logging.getLogger(__name__)

# yt-dlp ignores Deno versions older than this.
DENO_MIN_VERSION = (2, 3, 0)

FFMPEG = None          # path to the ffmpeg binary
FFPROBE = None         # path to the ffprobe binary
DENO = None            # path to the deno binary handed to yt-dlp
MP3_ENCODER = False    # ffmpeg was built with libmp3lame
CHECKS = []            # list of dicts shown in the status panel
PROBLEMS = []          # blocking problems; downloads are disabled if any


def _run(cmd, timeout=15):
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return result.stdout + result.stderr
    except (OSError, subprocess.TimeoutExpired):
        return ""


def _version(output, pattern):
    match = re.search(pattern, output, re.MULTILINE)
    return match.group(1) if match else None


def _version_tuple(version):
    return tuple(int(n) for n in re.findall(r"\d+", version or "")[:3])


def _deno_candidates():
    """Deno on PATH first, then the binary installed by the `deno` pip package."""
    on_path = shutil.which("deno")
    if on_path:
        yield on_path, "PATH"
    try:
        import deno  # the pip package (from yt-dlp[deno])
        yield deno.find_deno_bin(), "deno pip package"
    except Exception:  # not installed, or binary missing
        pass


def find_deno():
    """Return (path, version, where) for the newest usable Deno, else (None, None, None)."""
    fallback = (None, None, None)
    for path, where in _deno_candidates():
        version = _version(_run([path, "--version"]), r"^deno (\S+)")
        if not version:
            continue
        if _version_tuple(version) >= DENO_MIN_VERSION:
            return path, version, where
        fallback = fallback if fallback[0] else (path, version, where)
    return fallback


def _check(key, label, ok, detail, optional=False):
    return {"key": key, "label": label, "ok": bool(ok), "detail": detail, "optional": optional}


def run_checks():
    """Locate the external tools and record what is missing."""
    global FFMPEG, FFPROBE, DENO, MP3_ENCODER
    import yt_dlp.version

    checks, problems = [], []

    # yt-dlp itself (always present - we're importing it)
    checks.append(_check("ytdlp", "yt-dlp", True, yt_dlp.version.__version__))

    # yt-dlp-ejs: the scripts that solve YouTube's JavaScript challenges
    try:
        ejs_version = importlib.metadata.version("yt-dlp-ejs")
    except importlib.metadata.PackageNotFoundError:
        ejs_version = None
    checks.append(_check("ejs", "yt-dlp-ejs (challenge solver)", ejs_version,
                         ejs_version or "Not installed"))
    if not ejs_version:
        problems.append("yt-dlp-ejs is missing. Press \"Update yt-dlp\" in Settings "
                        "(it installs yt-dlp[default,deno]).")

    # Deno: the JavaScript runtime that runs those scripts
    DENO, deno_version, where = find_deno()
    deno_ok = bool(DENO) and _version_tuple(deno_version) >= DENO_MIN_VERSION
    if DENO:
        detail = f"{deno_version} ({where})"
        if not deno_ok:
            detail += " - too old, needs 2.3 or newer"
    else:
        detail = "Not found"
    checks.append(_check("deno", "Deno (JavaScript runtime)", deno_ok, detail))
    if not deno_ok:
        problems.append("Deno is missing or too old. Press \"Update yt-dlp\" in Settings "
                        "(it installs yt-dlp[default,deno], which includes Deno).")

    # ffmpeg + ffprobe: merging video and audio, converting, checking files
    FFMPEG, FFPROBE = shutil.which("ffmpeg"), shutil.which("ffprobe")
    if FFMPEG:
        ffmpeg_version = _version(_run([FFMPEG, "-version"]), r"^ffmpeg version (\S+)") or "unknown"
        encoders = _run([FFMPEG, "-hide_banner", "-encoders"])
        MP3_ENCODER = "libmp3lame" in encoders
        detail = ffmpeg_version + ("" if MP3_ENCODER else " - no MP3 encoder (libmp3lame)")
        checks.append(_check("ffmpeg", "ffmpeg", True, detail))
    else:
        checks.append(_check("ffmpeg", "ffmpeg", False, "Not found"))
        problems.append("ffmpeg is missing. Add ffmpeg as a Replit system dependency "
                        "(the real binary, not the pip package), then restart.")
    if FFPROBE:
        ffprobe_version = _version(_run([FFPROBE, "-version"]), r"^ffprobe version (\S+)") or "unknown"
        checks.append(_check("ffprobe", "ffprobe", True, ffprobe_version))
    else:
        checks.append(_check("ffprobe", "ffprobe", False, "Not found"))
        problems.append("ffprobe is missing. It comes with ffmpeg: add ffmpeg as a Replit "
                        "system dependency, then restart.")

    CHECKS[:] = checks
    PROBLEMS[:] = problems
    for c in checks:
        log.info("%-32s %s  %s", c["label"], "OK " if c["ok"] else "MISSING", c["detail"])
    for p in problems:
        log.error("Download disabled: %s", p)
