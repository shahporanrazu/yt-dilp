"""Everything that talks to yt-dlp.

Flow:
  1. fetch_info(video_id)  - one extraction from YouTube (no download)
  2. build_choices(raw)    - which qualities this video really has, with sizes
  3. download(raw, ...)    - runs yt-dlp on the same info, then ffmpeg checks

The quality rules (the important part):
  * format_sort instead of height filters. "res" is the SHORTER side of the
    frame, so a vertical 1080x1920 Short counts as 1080p.
  * Named qualities prefer H.264 video + AAC audio (plays everywhere).
  * "bv+ba/b" = best video stream + best audio stream, merged by ffmpeg.
"""

import copy
import logging
import os
import threading
import time
from dataclasses import dataclass, field

import yt_dlp
from yt_dlp.utils import DownloadError, ExtractorError

from . import config, media, tools
from .cookies import store as cookie_store
from .errors import NO_AUDIO_ERROR, AppError, explain, live_status_error
from .urls import watch_url

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Quality:
    key: str
    label: str
    format: str
    format_sort: list = field(default_factory=list)
    audio_only: bool = False


QUALITIES = [
    Quality("best", "Best available", "bv+ba/b", ["res", "hdr:sdr", "acodec:aac"]),
    Quality("1080", "1080p", "bv+ba/b", ["res:1080", "vcodec:h264", "acodec:aac"]),
    Quality("720", "720p", "bv+ba/b", ["res:720", "vcodec:h264", "acodec:aac"]),
    Quality("480", "480p", "bv+ba/b", ["res:480", "vcodec:h264", "acodec:aac"]),
    Quality("360", "360p", "bv+ba/b", ["res:360", "vcodec:h264", "acodec:aac"]),
    Quality("mp3", "Audio only (MP3, 192 kbps)", "ba/b", [], audio_only=True),
]
QUALITY_BY_KEY = {q.key: q for q in QUALITIES}
DEFAULT_QUALITY = "1080"
MP3_KBPS = 192


# ---------------------------------------------------------------- yt-dlp setup

class YtdlpLogger:
    """Sends yt-dlp output to our log and remembers warnings for the UI."""

    def __init__(self):
        self.warnings = []

    def debug(self, msg):
        if not msg.startswith("[debug] "):
            log.debug(msg)

    def info(self, msg):
        log.debug(msg)

    def warning(self, msg):
        log.warning("yt-dlp: %s", msg)
        self.warnings.append(msg)
        if "cookies are no longer valid" in msg:
            cookie_store.warning = ("YouTube says these cookies are no longer valid. "
                                    "Export fresh ones and paste them in Settings.")

    def error(self, msg):
        log.error("yt-dlp: %s", msg)


def base_options(logger):
    """Options shared by every yt-dlp run."""
    opts = {
        "logger": logger,
        "quiet": True,
        "noprogress": True,
        "noplaylist": True,
        "source_address": "0.0.0.0",   # force IPv4 (same as --force-ipv4)
        "retries": 10,
        "fragment_retries": 10,
        "socket_timeout": 30,
        # Deno + yt-dlp-ejs solve YouTube's JavaScript challenges. Passing the
        # path explicitly also covers a Deno that isn't on PATH
        # (CLI equivalent: --js-runtimes deno:/path/to/deno).
        "js_runtimes": {"deno": {"path": tools.DENO} if tools.DENO else {}},
    }
    if tools.FFMPEG:
        opts["ffmpeg_location"] = tools.FFMPEG
    if config.PROXY_URL:
        opts["proxy"] = config.PROXY_URL
    # Deliberately no player_client / extractor_args: yt-dlp's defaults track
    # whatever currently works on YouTube.
    return opts


class Ytdlp:
    """`with Ytdlp(extra_options) as ydl:` - a YoutubeDL with our settings and
    a private copy of the cookies (refreshed cookies are saved afterwards)."""

    def __init__(self, extra=None):
        self.extra = extra or {}
        self.logger = YtdlpLogger()

    def __enter__(self):
        self._cookies = cookie_store.session_file()
        cookiefile = self._cookies.__enter__()
        opts = base_options(self.logger)
        if cookiefile:
            opts["cookiefile"] = cookiefile
        opts.update(self.extra)
        try:
            self._ydl = yt_dlp.YoutubeDL(opts)
        except BaseException:
            self._cookies.__exit__(None, None, None)
            raise
        return self._ydl

    def __exit__(self, *exc):
        try:
            self._ydl.close()  # writes refreshed cookies to our private copy
        finally:
            self._cookies.__exit__(*exc)
        return False


# ------------------------------------------------------------- video info

_info_cache = {}           # video_id -> (time fetched, raw info dict)
_info_cache_lock = threading.Lock()


def fetch_info(video_id, use_cache=True):
    """Ask YouTube about a video (no download). Returns (raw_info, warnings).

    The result is the unprocessed extractor output, so the same info can be
    re-used for every quality without contacting YouTube again.
    """
    if use_cache:
        with _info_cache_lock:
            cached = _info_cache.get(video_id)
        if cached and time.time() - cached[0] < config.INFO_CACHE_SECONDS:
            return cached[1], []

    tool = Ytdlp()
    try:
        with tool as ydl:
            raw = ydl.extract_info(watch_url(video_id), download=False, process=False)
    except (DownloadError, ExtractorError) as exc:
        raise explain(exc)
    if not raw or raw.get("_type", "video") != "video":
        raise AppError("This link didn't lead to a single video.",
                       "Copy the link of the video itself.")

    with _info_cache_lock:
        now = time.time()
        for key in [k for k, (t, _) in _info_cache.items()
                    if now - t > config.INFO_CACHE_SECONDS]:
            del _info_cache[key]
        _info_cache[video_id] = (now, raw)
    return raw, tool.logger.warnings


def has_audio(raw):
    """False only if YouTube explicitly marks every stream as having no audio.

    (Storyboard images don't count. Streams with an unknown audio codec count
    as maybe-audio; the ffprobe check after downloading is the real guarantee.)
    """
    return any(f.get("acodec") != "none" and f.get("ext") != "mhtml"
               for f in raw.get("formats") or [])


def check_downloadable(raw):
    """Raise AppError for live/upcoming streams and videos without any audio."""
    error = live_status_error(raw.get("live_status") or ("is_live" if raw.get("is_live") else None))
    if error:
        raise error
    if not has_audio(raw):
        raise NO_AUDIO_ERROR


# --------------------------------------------------------- quality choices

def select_formats(raw, quality):
    """Run yt-dlp's own format selection (no network) for one quality.

    Returns {"video": fmt or None, "audio": fmt or None, "parts": [fmt, ...]}
    or None if yt-dlp finds nothing for this quality.
    """
    opts = {"format": quality.format, "format_sort": quality.format_sort,
            "check_formats": False, "quiet": True, "logger": YtdlpLogger(),
            "js_runtimes": {}}
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            result = ydl.process_ie_result(copy.deepcopy(raw), download=False)
    except (DownloadError, ExtractorError):
        return None
    parts = result.get("requested_formats") or [result]
    video = next((f for f in parts if f.get("vcodec") not in (None, "none")), None)
    audio = next((f for f in parts if f.get("acodec") not in (None, "none")), None)
    return {"video": video, "audio": audio, "parts": parts}


def _size(fmt):
    """(bytes, is_estimate) for one format."""
    if fmt.get("filesize"):
        return fmt["filesize"], False
    if fmt.get("filesize_approx"):
        return fmt["filesize_approx"], True
    return None, True


def estimate_size(parts):
    """(total bytes or None, is_estimate) for the formats yt-dlp will download."""
    total, approx = 0, False
    for fmt in parts:
        size, is_approx = _size(fmt)
        if size is None:
            return None, True
        total += size
        approx = approx or is_approx
    return total, approx


def build_choices(raw):
    """Quality options this video really has, each with an estimated size.

    A named quality (e.g. 720p) is only offered when yt-dlp would actually
    pick a stream of that quality; otherwise it would duplicate a lower one.
    """
    choices = []
    duration = raw.get("duration") or 0
    for quality in QUALITIES:
        if quality.audio_only:
            if not tools.MP3_ENCODER:
                continue
            picked = select_formats(raw, quality)
            if not picked or not picked["audio"]:
                continue
            size = int(duration * MP3_KBPS * 1000 / 8) if duration else None
            choices.append({"key": quality.key, "label": quality.label,
                            "size": size, "size_is_estimate": True,
                            "detail": "MP3 · 192 kbps", "note": None})
            continue

        picked = select_formats(raw, quality)
        if not picked or not picked["video"] or not picked["audio"]:
            continue
        video = picked["video"]
        step = media.quality_step(video.get("width"), video.get("height"))
        if quality.key != "best" and step != int(quality.key):
            continue
        size, approx = estimate_size(picked["parts"])
        vcodec = media.codec_name(video.get("vcodec")) or "video"
        width, height, fps = video.get("width"), video.get("height"), video.get("fps") or 0
        detail = " · ".join(filter(None, [
            f"{step}p" if quality.key == "best" and step else None,
            f"{width}×{height}" if width and height else None,
            f"{round(fps)} fps" if fps > 30 else None,
            vcodec,
            "HDR" if (video.get("dynamic_range") or "SDR") != "SDR" else None,
        ]))
        note = None
        if vcodec != "H.264":
            note = (f"{vcodec} video: plays in browsers, VLC and modern phones, "
                    "but older devices and some editing apps may need VLC.")
        choices.append({"key": quality.key, "label": quality.label, "size": size,
                        "size_is_estimate": approx, "detail": detail, "note": note,
                        "step": step})
    return choices


def video_summary(raw):
    """The bits of video info the page shows on the info card."""
    thumbnail = raw.get("thumbnail")
    if not thumbnail:
        thumbs = [t for t in raw.get("thumbnails") or [] if t.get("url")]
        # yt-dlp lists thumbnails worst-to-best; prefer jpg among the best.
        thumbs.sort(key=lambda t: (t.get("preference") or 0, ".jpg" in t["url"],
                                   t.get("width") or 0))
        thumbnail = thumbs[-1]["url"] if thumbs else None
    video_id = raw.get("id")
    return {
        "id": video_id,
        "title": raw.get("title") or "Untitled video",
        "channel": raw.get("channel") or raw.get("uploader") or "",
        "duration": raw.get("duration"),
        "thumbnail": thumbnail,
        # hqdefault always exists; the page falls back to it if the best one 404s.
        "thumbnail_fallback": f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg",
        "is_short": raw.get("media_type") == "short",
    }


# ------------------------------------------------------------- download

def download(raw, quality, out_dir, on_progress, on_postprocess):
    """Download one quality of an already-fetched video into out_dir.

    Returns the path of the finished file (media.mp4 or media.mp3).
    """
    opts = {
        "format": quality.format,
        "format_sort": quality.format_sort,
        "outtmpl": {"default": os.path.join(out_dir, "media.%(ext)s")},
        "progress_hooks": [on_progress],
        "postprocessor_hooks": [on_postprocess],
    }
    if quality.audio_only:
        opts["postprocessors"] = [
            {"key": "FFmpegExtractAudio", "preferredcodec": "mp3",
             "preferredquality": str(MP3_KBPS)},
            {"key": "FFmpegMetadata", "add_metadata": True},
        ]
    else:
        opts["merge_output_format"] = "mp4"

    try:
        with Ytdlp(opts) as ydl:
            result = ydl.process_ie_result(copy.deepcopy(raw), download=True)
    except (DownloadError, ExtractorError) as exc:
        raise explain(exc)

    downloads = result.get("requested_downloads") or []
    path = downloads[0].get("filepath") if downloads else None
    if not path:
        raise AppError("yt-dlp finished without producing a file.",
                       "Try again, or update yt-dlp in Settings.")
    return path
