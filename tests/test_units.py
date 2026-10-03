"""Unit tests: link parsing, error messages, file names, cookies."""

import os

import pytest

from app import cookies as cookies_module
from app.errors import AppError, explain
from app.jobs import make_download_name
from app.media import codec_name, quality_step
from app.urls import extract_video_id, watch_url

ID = "dQw4w9WgXcQ"


@pytest.mark.parametrize("link", [
    f"https://www.youtube.com/watch?v={ID}",
    f"https://www.youtube.com/watch?v={ID}&list=PLabcdefghijklmnop&index=3",
    f"https://www.youtube.com/watch?list=RD{ID}&v={ID}&start_radio=1",
    f"https://youtube.com/watch?v={ID}&t=42s&si=trackingjunk",
    f"youtube.com/watch?v={ID}",
    f"www.youtube.com/watch?v={ID}",
    f"http://m.youtube.com/watch?v={ID}&feature=share",
    f"https://music.youtube.com/watch?v={ID}&list=OLAK5uy_abc",
    f"https://youtu.be/{ID}",
    f"https://youtu.be/{ID}?si=abc&t=10",
    f"youtu.be/{ID}",
    f"https://www.youtube.com/shorts/{ID}",
    f"https://youtube.com/shorts/{ID}?feature=share",
    f"https://www.youtube.com/live/{ID}?si=xyz",
    f"https://www.youtube.com/embed/{ID}",
    f"  https://www.youtube.com/watch?v={ID}  ",
])
def test_accepts_youtube_links(link):
    assert extract_video_id(link) == ID
    assert watch_url(extract_video_id(link)) == f"https://www.youtube.com/watch?v={ID}"


@pytest.mark.parametrize("link", [
    "",
    "https://vimeo.com/123456",
    f"https://youtube.com.evil.com/watch?v={ID}",
    f"https://notyoutube.com/watch?v={ID}",
    f"https://youtube.com@evil.com/watch?v={ID}",
    f"ftp://youtube.com/watch?v={ID}",
    "javascript:alert(1)",
    "https://www.youtube.com/playlist?list=PLabcdefghijklmnop",
    "https://www.youtube.com/@somechannel",
    "https://www.youtube.com/watch?v=tooShort",
    "https://www.youtube.com/watch?v=has spaces!",
    "https://youtu.be/",
])
def test_rejects_everything_else(link):
    with pytest.raises(AppError):
        extract_video_id(link)


@pytest.mark.parametrize("raw, expected", [
    ("ERROR: [youtube] abc: Sign in to confirm you’re not a bot. Use --cookies-from-browser",
     "blocking this server"),
    ("ERROR: [youtube] abc: Sign in to confirm your age. This video may be inappropriate for some users.",
     "age-restricted"),
    ("ERROR: [youtube] abc: Join this channel to get access to members-only content like this video",
     "members only"),
    ("ERROR: [youtube] abc: Private video. Sign in if you've been granted access to this video",
     "private"),
    ("ERROR: [youtube] abc: Requested format is not available. Use --list-formats",
     "didn't offer the quality"),
    ("ERROR: [youtube] abc: This live event will begin in 3 hours.", "hasn't started"),
    ("ERROR: [youtube] abc: Video unavailable. This video has been removed by the uploader",
     "unavailable"),
    ("ERROR: unable to download video data: HTTP Error 403: Forbidden", "HTTP 403"),
    ("ERROR: [youtube] abc: Unable to download API page: ('Unable to connect to proxy', "
     "OSError('Tunnel connection failed: 403 Forbidden'))", "proxy"),
    ("something nobody expected", "download failed"),
])
def test_errors_are_plain_english(raw, expected):
    error = explain(Exception("\x1b[0;31m" + raw + "\x1b[0m"))
    assert expected.lower() in error.message.lower()
    assert error.hint
    assert error.details == raw          # raw text kept for the Details box, colours removed


def test_live_stream_message():
    from app.errors import live_status_error
    assert "still running" in live_status_error("is_live").message
    assert "after it ends" in live_status_error("is_live").hint
    assert live_status_error("was_live") is None
    assert live_status_error("not_live") is None


@pytest.mark.parametrize("w, h, step", [
    (1920, 1080, 1080), (1080, 1920, 1080), (3840, 2160, 2160), (2160, 3840, 2160),
    (1280, 720, 720), (854, 480, 480), (640, 360, 360), (256, 144, 144),
    (1920, 800, 1080), (2560, 1080, 1080), (1920, 1088, 1080), (2560, 1440, 1440),
])
def test_quality_step_uses_shorter_side(w, h, step):
    assert quality_step(w, h) == step


def test_codec_names():
    assert codec_name("avc1.640028") == "H.264"
    assert codec_name("vp09.00.40.08") == "VP9"
    assert codec_name("av01.0.08M.08") == "AV1"
    assert codec_name("mp4a.40.2") == "AAC"
    assert codec_name("opus") == "Opus"
    assert codec_name("none") is None


def test_download_names_keep_unicode_and_emoji():
    name = make_download_name("Café déjà vu — 日本語 🎉: Part 1/2", "1080p", "mp4")
    assert name == "Café déjà vu — 日本語 🎉 - Part 1-2 [1080p].mp4"
    assert make_download_name('a<b>c"d?e*f|g\\h\x07', "720p", "mp4") == "abc'def-g-h [720p].mp4"
    assert make_download_name("", "MP3", "mp3") == "video [MP3].mp3"
    assert make_download_name("...", "360p", "mp4") == "video [360p].mp4"
    long_name = make_download_name("🎉" * 300, "1080p", "mp4")
    assert len(long_name.encode()) < 255 and long_name.endswith(" [1080p].mp4")


# ----------------------------------------------------------------- cookies

COOKIES = (
    "# Netscape HTTP Cookie File\n"
    ".youtube.com\tTRUE\t/\tTRUE\t1999999999\tSID\tsecret-value-1\n"
    "#HttpOnly_.youtube.com\tTRUE\t/\tTRUE\t1999999999\tLOGIN_INFO\tsecret-value-2\n"
    ".youtube.com\tTRUE\t/\tTRUE\t1999999999\tVISITOR_INFO1_LIVE\tsecret-value-3\n"
    "this line is junk\n"
)


def test_cookie_parsing_tolerates_spaces_instead_of_tabs():
    spaced = COOKIES.replace("\t", "    ")
    clean, stats = cookies_module.parse_cookies_txt(spaced)
    assert stats == {"count": 3, "youtube": 3, "signed_in": True}
    assert clean.count("\t") == 3 * 6
    _, stats = cookies_module.parse_cookies_txt(COOKIES.replace("\n", "\\n").replace("\t", "\\t"))
    assert stats["count"] == 3


def test_cookie_store_rejects_bad_input():
    store = cookies_module.CookieStore()
    with pytest.raises(ValueError):
        store.set("hello world", "Settings")
    with pytest.raises(ValueError):
        store.set(".example.com\tTRUE\t/\tFALSE\t0\tname\tvalue\n", "Settings")
    assert not store.loaded


def test_cookie_store_is_private_and_never_leaks_values():
    store = cookies_module.CookieStore()
    store.set(COOKIES, "Settings")
    assert oct(os.stat(store._path).st_mode & 0o777) == "0o600"
    assert oct(os.stat(store._dir).st_mode & 0o777) == "0o700"
    assert "secret-value" not in repr(store.status())
    with store.session_file() as path:
        assert path != store._path
        assert "secret-value-1" in open(path).read()
    store.clear()
    assert not store.loaded
    with store.session_file() as path:
        assert path is None


def test_refreshed_cookies_are_kept_unless_user_replaced_them():
    store = cookies_module.CookieStore()
    store.set(COOKIES, "Settings")
    refreshed = ".youtube.com\tTRUE\t/\tTRUE\t1999999999\tSID\trefreshed\n"

    with store.session_file() as path:          # yt-dlp rewrites its copy
        with open(path, "w") as f:
            f.write(refreshed)
    assert "refreshed" in open(store._path).read()

    with store.session_file() as path:          # user pastes new cookies meanwhile
        store.set(COOKIES, "Settings")
        with open(path, "w") as f:
            f.write(refreshed.replace("refreshed", "stale"))
    assert "stale" not in open(store._path).read()
