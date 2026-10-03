"""Turning yt-dlp / ffmpeg errors into plain English.

Every error shown in the UI has three parts:
  message - what went wrong, in one sentence
  hint    - what to do about it
  details - the raw error text (shown in a collapsible "Details" box)
"""

import re

from . import config

ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
PROXY_HINT = object()   # placeholder: the proxy hint depends on PROXY_URL


class AppError(Exception):
    """An error we can explain to the user."""

    def __init__(self, message, hint="", details=""):
        super().__init__(message)
        self.message = message
        self.hint = hint
        self.details = details

    def to_dict(self):
        return {"message": self.message, "hint": self.hint, "details": self.details}


# (pattern, message, hint) - checked in order, first match wins.
# Patterns are matched case-insensitively against the raw error text.
RULES = [
    (r"not a bot",
     "YouTube is blocking this server (\"Sign in to confirm you're not a bot\").",
     "Paste fresh cookies in Settings. If it keeps happening, also set a PROXY_URL secret."),
    (r"confirm your age|age[- ]restricted|inappropriate for some users",
     "This video is age-restricted.",
     "It needs cookies (Settings) from a signed-in YouTube account that has confirmed its age."),
    (r"members[- ]only|join this channel|channel'?s? members|available to this channel",
     "This video is for channel members only.",
     "It needs cookies (Settings) from an account that is a member of this channel."),
    (r"private video|video is private",
     "This video is private.",
     "It needs cookies (Settings) from an account that has been given access to it."),
    (r"live event will begin|premieres? in|is_upcoming|scheduled to begin",
     "This stream or premiere hasn't started yet.",
     "It can be downloaded after it has finished."),
    (r"requested format is not available|no video formats found|only images are available",
     "YouTube didn't offer the quality you picked for this video.",
     "Try another quality, or update yt-dlp in Settings."),
    (r"not available in your country|geo.?restrict|from your location",
     "This video is blocked in the country this server is in.",
     "Set a PROXY_URL secret that points to a proxy in a country where it's available."),
    (r"video unavailable|has been removed|no longer available|account .* terminated|copyright",
     "This video is unavailable.",
     "It may have been deleted, made private or blocked."),
    (r"http error 429|too many requests",
     "YouTube is rate-limiting this server.",
     "Wait a while, then try again. Fresh cookies or a PROXY_URL secret also help."),
    (r"http error 403|403: forbidden",
     "YouTube refused the download (HTTP 403).",
     "Update yt-dlp in Settings and try again. Fresh cookies can also help."),
    (r"no space left on device",
     "The server ran out of disk space.",
     "Pick a lower quality, or wait for older downloads to be cleaned up (after 30 minutes)."),
    (r"unable to connect to proxy|tunnel connection failed|proxyerror",
     "The server couldn't connect through the proxy.",
     PROXY_HINT),
    (r"failed to resolve|name or service not known|temporary failure in name resolution|"
     r"timed out|connection refused|network is unreachable|connection reset|remote end closed",
     "The server couldn't reach YouTube.",
     "Check the server's internet connection, then try again."),
]


def clean(text):
    """Strip terminal colour codes and surrounding whitespace."""
    return ANSI_RE.sub("", str(text or "")).strip()


def explain(exc):
    """Return an AppError with a plain-English message for any exception."""
    if isinstance(exc, AppError):
        return exc
    raw = clean(exc) or exc.__class__.__name__
    for pattern, message, hint in RULES:
        if re.search(pattern, raw, re.IGNORECASE):
            if hint is PROXY_HINT:
                hint = ("Check the PROXY_URL secret, then try again." if config.PROXY_URL else
                        "The server's own network proxy refused the connection to YouTube.")
            return AppError(message, hint, raw)
    return AppError("The download failed.",
                    "Try again. If it keeps failing, update yt-dlp in Settings.", raw)


# Errors raised from our own checks (not from yt-dlp).

def live_status_error(live_status):
    """AppError for streams that can't be downloaded yet, or None."""
    if live_status == "is_live":
        return AppError("This live stream is still running.",
                        "It can be downloaded after it ends.")
    if live_status == "is_upcoming":
        return AppError("This stream or premiere hasn't started yet.",
                        "It can be downloaded after it has finished.")
    if live_status == "post_live":
        return AppError("This stream just ended and YouTube is still processing it.",
                        "Try again later, once YouTube has finished processing the recording.")
    return None


NO_AUDIO_ERROR = AppError(
    "YouTube doesn't offer any audio track for this video.",
    "Downloading it would give you a silent file, so it's been blocked. "
    "If the video does have sound on YouTube, update yt-dlp in Settings or add cookies.")
