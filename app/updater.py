"""The "Update yt-dlp" button.

Steps: stop accepting new downloads -> wait until no download is queued or
running -> pip install --upgrade yt-dlp[default,deno] -> restart the app so
the new version is loaded. (pip replaces yt-dlp's files on disk, so it must
not run underneath an active download.)
"""

import logging
import os
import sys
import threading
import time

from . import bootstrap, config
from .jobs import manager

log = logging.getLogger(__name__)

_lock = threading.Lock()
state = {"state": "idle", "message": "", "log": ""}
# state["state"]: idle, waiting, installing, restarting, done, failed


def _set(**values):
    state.update(values)
    if values.get("message"):
        log.info("Update: %s", values["message"])


def is_running():
    return state["state"] in ("waiting", "installing", "restarting")


def start():
    """Start an update in the background. Returns False if one is running."""
    with _lock:
        if is_running():
            return False
        _set(state="waiting", message="Waiting for downloads to finish...", log="")
    threading.Thread(target=_run, name="updater", daemon=True).start()
    return True


def _run():
    manager.accepting = False
    try:
        while manager.busy():
            time.sleep(2)

        _set(state="installing", message="Installing the latest yt-dlp[default,deno]...")
        ok, before, after, output = bootstrap.upgrade_ytdlp()
        _set(log=output[-6000:])
        if not ok:
            _set(state="failed", message="pip couldn't install the update. See the log below.")
            return

        changes = [f"{name} {before[name] or 'none'} → {after[name] or 'none'}"
                   for name in bootstrap.TRACKED_PACKAGES if before[name] != after[name]]
        if not changes:
            _set(state="done", message=f"Already up to date (yt-dlp {after['yt-dlp']}).")
            return

        _set(state="restarting", message="Updated " + ", ".join(changes) + ". Restarting...")
        time.sleep(2)  # give the page a moment to show the message
        restart()
    except Exception as exc:
        log.exception("Update failed")
        _set(state="failed", message=f"Update failed: {exc}")
    finally:
        if state["state"] != "restarting":
            manager.accepting = True


def restart():
    """Exit with RESTART_EXIT_CODE; main.py's launcher then starts a fresh server."""
    log.info("Restarting the app...")
    logging.shutdown()
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(config.RESTART_EXIT_CODE)
