"""Settings plus fixed limits.

Settings come from environment variables, or from a `settings.env` file in
the app folder (one KEY=value per line; created on the first start):

  APP_PASSWORD    required - password for the login page
  YT_COOKIES      optional - contents of a Netscape-format cookies.txt
                  (easier: paste cookies in the app's Settings panel)
  PROXY_URL       optional - proxy for yt-dlp, e.g. http://user:pass@host:port
  SESSION_SECRET  optional - key for signing login cookies (derived from
                  APP_PASSWORD when not set)
  PORT            optional - default 8765
  HOST            optional - default 0.0.0.0 (phones on your Wi-Fi can connect);
                  use 127.0.0.1 to allow only this computer
"""

import os
import tempfile

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SETTINGS_FILE = os.path.join(PROJECT_DIR, "settings.env")


def read_settings_file(path=SETTINGS_FILE):
    """Parse KEY=value lines (# comments, optional quotes). Returns a dict."""
    values = {}
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                value = value.strip()
                if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
                    value = value[1:-1]
                values[key.strip()] = value
    except FileNotFoundError:
        pass
    return values


def save_setting(key, value, path=SETTINGS_FILE):
    """Add or replace one KEY=value line in settings.env (readable only by you)."""
    lines = []
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            lines = [ln for ln in f.read().splitlines() if not ln.startswith(f"{key}=")]
    lines.append(f"{key}={value}")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    os.chmod(path, 0o600)   # also tighten a file that already existed
    os.environ[key] = value


# Real environment variables win over settings.env.
for _key, _value in read_settings_file().items():
    os.environ.setdefault(_key, _value)

APP_PASSWORD = os.environ.get("APP_PASSWORD", "")
SESSION_SECRET = os.environ.get("SESSION_SECRET", "")
YT_COOKIES = os.environ.get("YT_COOKIES", "")
PROXY_URL = os.environ.get("PROXY_URL", "").strip()

HOST = os.environ.get("HOST", "0.0.0.0")
PORT = int(os.environ.get("PORT", "8765"))   # not 5000: macOS AirPlay uses it

# Limits
MAX_PARALLEL_DOWNLOADS = 2
FILE_LIFETIME_SECONDS = 30 * 60      # finished files are deleted after 30 min
JOB_RECORD_SECONDS = 6 * 60 * 60     # finished jobs vanish from the list after 6 h
INFO_CACHE_SECONDS = 20 * 60         # reuse fetched video info for 20 min
SESSION_DAYS = 30
SERVER_THREADS = 16

# Downloads live outside the project folder and are wiped on every start.
DOWNLOAD_DIR = os.path.join(tempfile.gettempdir(), "yt-dilp-downloads")

# What gets installed / upgraded on start and by the "Update yt-dlp" button.
YTDLP_REQUIREMENT = "yt-dlp[default,deno]"

# The server exits with this code to ask main.py to start it again
# (after "Update yt-dlp"), so the new version gets loaded.
RESTART_EXIT_CODE = 3
