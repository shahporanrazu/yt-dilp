"""Settings read from the environment (Replit "Secrets") plus fixed limits.

Secrets used:
  APP_PASSWORD    required - password for the login page
  YT_COOKIES      optional - contents of a Netscape-format cookies.txt
  PROXY_URL       optional - proxy for yt-dlp, e.g. http://user:pass@host:port
  SESSION_SECRET  optional - key for signing login cookies (derived from
                  APP_PASSWORD when not set)
"""

import os
import tempfile

APP_PASSWORD = os.environ.get("APP_PASSWORD", "")
SESSION_SECRET = os.environ.get("SESSION_SECRET", "")
YT_COOKIES = os.environ.get("YT_COOKIES", "")
PROXY_URL = os.environ.get("PROXY_URL", "").strip()

HOST = "0.0.0.0"
PORT = int(os.environ.get("PORT", "5000"))

# Replit serves the app over HTTPS and shows it inside an iframe in the
# editor, which needs SameSite=None + Secure cookies to stay logged in.
ON_REPLIT = bool(os.environ.get("REPL_ID"))

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
