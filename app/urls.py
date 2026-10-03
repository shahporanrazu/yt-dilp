"""Turning whatever link was pasted into a clean YouTube watch URL."""

import re
from urllib.parse import parse_qs, urlsplit

from .errors import AppError

VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")

# Exact host names we accept (no suffix matching, so "youtube.com.evil.com"
# or "notyoutube.com" are rejected).
YOUTUBE_HOSTS = {
    "youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com",
    "youtube-nocookie.com", "www.youtube-nocookie.com",
}
SHORT_LINK_HOSTS = {"youtu.be", "www.youtu.be"}

# youtube.com/<prefix>/<VIDEO_ID>
PATH_PREFIXES = {"shorts", "live", "embed", "v", "watch"}


def extract_video_id(text):
    """Return the 11-character video ID in a YouTube link, or raise AppError.

    Accepts youtube.com/watch?v=, youtu.be/, m.youtube.com, music.youtube.com,
    /shorts/, /live/ and /embed/ links, with or without "https://".
    Playlist and tracking parameters (&list=, &t=, ?si=) are ignored.
    """
    text = (text or "").strip()
    if not text:
        raise AppError("Paste a YouTube link first.")
    if "://" not in text:
        text = "https://" + text

    try:
        parts = urlsplit(text)
        host = (parts.hostname or "").lower().rstrip(".")
    except ValueError:
        raise AppError("That doesn't look like a valid link.")

    if parts.scheme not in ("http", "https") or not (
            host in YOUTUBE_HOSTS or host in SHORT_LINK_HOSTS):
        raise AppError("That isn't a YouTube link.",
                       "Paste a link from youtube.com or youtu.be.")

    segments = [s for s in parts.path.split("/") if s]
    video_id = None
    if host in SHORT_LINK_HOSTS:                       # youtu.be/ID
        video_id = segments[0] if segments else None
    elif segments[:1] == ["watch"] and len(segments) == 1:   # /watch?v=ID
        video_id = (parse_qs(parts.query).get("v") or [None])[0]
    elif len(segments) >= 2 and segments[0] in PATH_PREFIXES:  # /shorts/ID etc.
        video_id = segments[1]
    elif segments[:1] == ["playlist"]:
        raise AppError("That's a playlist link.",
                       "Open a single video from the playlist and copy its link.")

    if not video_id or not VIDEO_ID_RE.match(video_id):
        raise AppError("This link doesn't point to a single YouTube video.",
                       "Copy the link of the video itself (it contains watch?v=, youtu.be/ or /shorts/).")
    return video_id


def watch_url(video_id):
    """The clean, canonical URL for a video ID (no playlist or tracking bits)."""
    return f"https://www.youtube.com/watch?v={video_id}"
