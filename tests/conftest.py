"""Test fixtures: fake YouTube videos made of real media files.

The test media is generated with ffmpeg and handed to yt-dlp as formats
with file:// URLs, so the real yt-dlp format selection, download, merge,
ffprobe checks and MP3 conversion all run - only YouTube itself is faked.
"""

import copy
import os
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("APP_PASSWORD", "test-password")

from app import config, jobs, tools, youtube  # noqa: E402
from app.errors import AppError  # noqa: E402
from app.urls import watch_url  # noqa: E402

tools.run_checks()

# Video IDs of the fake videos
NORMAL = "aaaaaaaaaaa"      # 16:9, 144p..2160p, H.264 + VP9, AAC + Opus audio
SHORT = "bbbbbbbbbbb"       # vertical 1080x1920 Short
SILENT = "ccccccccccc"      # video streams only, no audio at all
OPUS_ONLY = "ddddddddddd"   # only Opus audio -> must be re-encoded to AAC
COMBINED = "eeeeeeeeeee"    # only one combined 360p stream (like format 18)
LIVE = "fffffffffff"        # a live stream that is still running

TITLE = "Café déjà vu — 日本語のタイトル 🎉🚀: Part 1/2"


def _ffmpeg(*args):
    subprocess.run(["ffmpeg", "-hide_banner", "-v", "error", "-y", *args], check=True)


def _video(path, w, h, codec):
    src = ["-f", "lavfi", "-i", f"testsrc2=size={w}x{h}:rate=10:duration=2"]
    if codec == "h264":
        _ffmpeg(*src, "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", path)
    else:
        _ffmpeg(*src, "-c:v", "libvpx-vp9", "-deadline", "realtime", "-cpu-used", "8",
                "-b:v", "300k", path)


def build_media(d):
    """Generate the test media files in directory d (a pathlib.Path)."""
    files = {}

    def make(name, *args):
        files[name] = str(d / name)
        if args[0] == "video":
            _video(files[name], *args[1:])
        return files[name]

    for w, h in [(256, 144), (640, 360), (854, 480), (1280, 720), (1920, 1080)]:
        make(f"h264_{h}.mp4", "video", w, h, "h264")
    make("vp9_1080.webm", "video", 1920, 1080, "vp9")
    make("vp9_2160.webm", "video", 3840, 2160, "vp9")
    for w, h in [(360, 640), (480, 854), (720, 1280), (1080, 1920)]:
        make(f"short_{w}x{h}.mp4", "video", w, h, "h264")

    files["aac.m4a"] = str(d / "aac.m4a")
    _ffmpeg("-f", "lavfi", "-i", "sine=frequency=440:duration=2", "-c:a", "aac", "-b:a", "128k",
            files["aac.m4a"])
    files["opus.webm"] = str(d / "opus.webm")
    _ffmpeg("-f", "lavfi", "-i", "sine=frequency=660:duration=2", "-c:a", "libopus", "-b:a", "96k",
            files["opus.webm"])
    files["combined_360.mp4"] = str(d / "combined_360.mp4")
    _ffmpeg("-f", "lavfi", "-i", "testsrc2=size=640x360:rate=10:duration=2",
            "-f", "lavfi", "-i", "sine=duration=2", "-c:v", "libx264", "-preset", "ultrafast",
            "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", files["combined_360.mp4"])
    return files


def _fmt(fid, path, ext, w=None, h=None, vcodec="none", acodec="none", note=None):
    """A format dict shaped like the ones yt-dlp's YouTube extractor returns."""
    return {
        "format_id": fid, "url": "file://" + path, "ext": ext, "protocol": "https",
        "width": w, "height": h, "vcodec": vcodec, "acodec": acodec,
        "fps": 30 if vcodec != "none" else None, "format_note": note,
        "filesize": os.path.getsize(path),
        "dynamic_range": "SDR" if vcodec != "none" else None,
    }


def _audio(media):
    return [
        _fmt("140", media["aac.m4a"], "m4a", acodec="mp4a.40.2", note="medium"),
        _fmt("251", media["opus.webm"], "webm", acodec="opus", note="medium"),
    ]


def _raw(video_id, formats, title=TITLE, **extra):
    url = watch_url(video_id)
    info = {
        "_type": "video", "id": video_id, "title": title, "channel": "Test Channel",
        "uploader": "Test Channel", "duration": 2, "extractor": "youtube",
        "extractor_key": "Youtube", "webpage_url": url, "original_url": url,
        "webpage_url_basename": "watch", "webpage_url_domain": "youtube.com",
        "live_status": "not_live", "media_type": "video", "formats": formats,
        "thumbnails": [{"url": f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg", "preference": 1}],
    }
    info.update(extra)
    return info


def build_fake_videos(m):
    """Fake extractor output for each test video ID, using the files from build_media()."""
    normal = [
        _fmt("18", m["combined_360.mp4"], "mp4", 640, 360, "avc1.42001E", "mp4a.40.2", "360p"),
        _fmt("160", m["h264_144.mp4"], "mp4", 256, 144, "avc1.4d400c", note="144p"),
        _fmt("134", m["h264_360.mp4"], "mp4", 640, 360, "avc1.4d401e", note="360p"),
        _fmt("135", m["h264_480.mp4"], "mp4", 854, 480, "avc1.4d401f", note="480p"),
        _fmt("136", m["h264_720.mp4"], "mp4", 1280, 720, "avc1.4d401f", note="720p"),
        _fmt("137", m["h264_1080.mp4"], "mp4", 1920, 1080, "avc1.640028", note="1080p"),
        _fmt("248", m["vp9_1080.webm"], "webm", 1920, 1080, "vp09.00.40.08", note="1080p"),
        _fmt("313", m["vp9_2160.webm"], "webm", 3840, 2160, "vp09.00.50.08", note="2160p"),
    ] + _audio(m)
    short = [
        _fmt("134", m["short_360x640.mp4"], "mp4", 360, 640, "avc1.4d401e", note="360p"),
        _fmt("135", m["short_480x854.mp4"], "mp4", 480, 854, "avc1.4d401f", note="480p"),
        _fmt("136", m["short_720x1280.mp4"], "mp4", 720, 1280, "avc1.4d401f", note="720p"),
        _fmt("137", m["short_1080x1920.mp4"], "mp4", 1080, 1920, "avc1.640028", note="1080p"),
    ] + _audio(m)
    return {
        NORMAL: _raw(NORMAL, normal),
        SHORT: _raw(SHORT, short, title="Vertical short #shorts", media_type="short"),
        SILENT: _raw(SILENT, [f for f in normal if f["acodec"] == "none"], title="Silent"),
        OPUS_ONLY: _raw(OPUS_ONLY, [f for f in normal if f["format_id"] in ("137", "251")],
                        title="Opus only"),
        COMBINED: _raw(COMBINED, [normal[0]], title="Old video"),
        LIVE: _raw(LIVE, normal, title="Live now", live_status="is_live", is_live=True),
    }


@pytest.fixture(scope="session")
def media(tmp_path_factory):
    return build_media(tmp_path_factory.mktemp("media"))


@pytest.fixture(scope="session")
def fake_videos(media):
    return build_fake_videos(media)


@pytest.fixture(autouse=True)
def fake_youtube(fake_videos, monkeypatch, tmp_path):
    """Replace the YouTube extraction step; everything after it is real."""

    def fetch_info(video_id, use_cache=True):
        if video_id not in fake_videos:
            raise AppError("This video is unavailable.", "", "ERROR: [youtube] Video unavailable")
        return copy.deepcopy(fake_videos[video_id]), []

    real_base_options = youtube.base_options

    def base_options(logger):
        opts = real_base_options(logger)
        opts["enable_file_urls"] = True     # test media is on disk
        return opts

    monkeypatch.setattr(youtube, "fetch_info", fetch_info)
    monkeypatch.setattr(youtube, "base_options", base_options)
    monkeypatch.setattr(config, "DOWNLOAD_DIR", str(tmp_path / "downloads"))
    os.makedirs(config.DOWNLOAD_DIR, exist_ok=True)


@pytest.fixture
def manager():
    return jobs.JobManager()
