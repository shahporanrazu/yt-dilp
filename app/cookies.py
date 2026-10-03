"""YouTube cookies (Netscape cookies.txt format), kept in a private temp file.

Where cookies come from:
  * the YT_COOKIES secret, loaded once at startup
  * the Settings panel (paste or upload), which replaces them immediately

yt-dlp rewrites its cookie file when it finishes, to keep cookies that
YouTube refreshed. To stop parallel downloads from overwriting each other's
file, every yt-dlp run gets its own private copy (see session_file()), and
refreshed cookies are copied back to the master file afterwards.

Cookie values are never logged or sent to the browser; only counts.
"""

import contextlib
import logging
import os
import tempfile
import threading
import time

log = logging.getLogger(__name__)

HEADER = "# Netscape HTTP Cookie File\n"

# Cookie names that only exist when someone is signed in to Google/YouTube.
SIGNED_IN_NAMES = {"SID", "SAPISID", "__Secure-1PSID", "__Secure-3PSID",
                   "__Secure-3PAPISID", "LOGIN_INFO"}


def parse_cookies_txt(text):
    """Normalise cookies.txt text. Returns (clean_text, stats dict).

    Tolerates tabs that were turned into spaces by copy/paste and literal
    "\\n" sequences from single-line secrets.
    """
    text = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    if "\n" not in text.strip() and "\\n" in text:
        text = text.replace("\\n", "\n").replace("\\t", "\t")

    lines, youtube, names = [], 0, set()
    for raw in text.split("\n"):
        line = raw.strip()
        # "#HttpOnly_" lines are real cookies; other "#" lines are comments.
        if not line or (line.startswith("#") and not line.startswith("#HttpOnly_")):
            continue
        fields = line.split("\t") if "\t" in line else line.split(None, 6)
        if len(fields) == 6:          # cookie with an empty value
            fields.append("")
        if len(fields) != 7:
            continue
        domain = fields[0].removeprefix("#HttpOnly_").lower()
        if "youtube.com" in domain:
            youtube += 1
        if "youtube.com" in domain or "google.com" in domain:
            names.add(fields[5])
        lines.append("\t".join(fields))

    stats = {
        "count": len(lines),
        "youtube": youtube,
        "signed_in": bool(names & SIGNED_IN_NAMES),
    }
    return HEADER + "\n".join(lines) + "\n", stats


def _write_private(path, text):
    """Write a file only this user can read (0600)."""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)


class CookieStore:
    def __init__(self):
        self._lock = threading.Lock()
        self._dir = tempfile.mkdtemp(prefix="yt-dilp-cookies-")  # mode 0700
        self._path = os.path.join(self._dir, "cookies.txt")
        self._generation = 0      # bumped whenever the user replaces cookies
        self._stats = None
        self.source = None        # "YT_COOKIES secret" or "Settings"
        self.updated_at = None
        self.warning = None       # e.g. "YouTube says these cookies expired"

    @property
    def loaded(self):
        return self._stats is not None

    def set(self, text, source):
        """Replace the active cookies. Raises ValueError if text is unusable."""
        clean, stats = parse_cookies_txt(text)
        if stats["count"] == 0:
            raise ValueError("That doesn't look like a cookies.txt file (Netscape format).")
        if stats["youtube"] == 0:
            raise ValueError("No youtube.com cookies found. Export the cookies "
                             "while you're on youtube.com.")
        with self._lock:
            _write_private(self._path, clean)
            self._generation += 1
            self._stats = stats
            self.source = source
            self.updated_at = time.time()
            self.warning = None
        log.info("Cookies loaded from %s (%d cookies, signed in: %s)",
                 source, stats["count"], stats["signed_in"])
        return stats

    def clear(self):
        with self._lock:
            with contextlib.suppress(FileNotFoundError):
                os.remove(self._path)
            self._generation += 1
            self._stats = None
            self.source = None
            self.updated_at = None
            self.warning = None
        log.info("Cookies removed")

    @contextlib.contextmanager
    def session_file(self):
        """Yield a private copy of the cookie file for one yt-dlp run (or None).

        After the run, cookies refreshed by YouTube are copied back, unless
        the user replaced the cookies in the meantime.
        """
        with self._lock:
            if self._stats is None:
                snapshot = None
            else:
                with open(self._path, encoding="utf-8") as f:
                    snapshot = f.read()
            generation = self._generation

        if snapshot is None:
            yield None
            return

        fd, path = tempfile.mkstemp(dir=self._dir, suffix=".txt")  # mode 0600
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(snapshot)
        try:
            yield path
        finally:
            self._copy_back(path, snapshot, generation)
            with contextlib.suppress(FileNotFoundError):
                os.remove(path)

    def _copy_back(self, path, snapshot, generation):
        try:
            with open(path, encoding="utf-8") as f:
                updated = f.read()
        except OSError:
            return
        if updated == snapshot:
            return
        clean, stats = parse_cookies_txt(updated)
        if stats["youtube"] == 0:
            return  # never replace good cookies with an empty jar
        with self._lock:
            if generation == self._generation and self._stats is not None:
                _write_private(self._path, clean)
                self._stats = stats

    def status(self):
        """Safe-to-show summary (never includes cookie values)."""
        stats = self._stats or {}
        return {
            "loaded": self.loaded,
            "source": self.source,
            "count": stats.get("count", 0),
            "signed_in": stats.get("signed_in", False),
            "updated_at": self.updated_at,
            "warning": self.warning,
        }


store = CookieStore()
