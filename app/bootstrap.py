"""Installing and upgrading Python packages with pip.

main.py runs this BEFORE anything imports yt_dlp, so this module must
never import yt_dlp itself. The "Update yt-dlp" button reuses
upgrade_ytdlp() and then restarts the app so the new version loads.
"""

import importlib
import importlib.metadata
import importlib.util
import logging
import os
import shutil
import subprocess
import sys

from . import config

log = logging.getLogger(__name__)

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Packages whose versions we report before/after an upgrade.
TRACKED_PACKAGES = ("yt-dlp", "yt-dlp-ejs", "deno")


def package_versions():
    """Return {package: version or None} for the tracked packages."""
    importlib.invalidate_caches()
    versions = {}
    for name in TRACKED_PACKAGES:
        try:
            versions[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            versions[name] = None
    return versions


def pip_install(args, timeout=600):
    """Run `pip install <args>` for the Python running this app.

    Returns (ok, output). Falls back to `uv pip` when this environment has
    no pip module (e.g. environments created by uv).
    """
    commands = [[sys.executable, "-m", "pip", "install",
                 "--disable-pip-version-check", "--no-input", *args]]
    uv = shutil.which("uv")
    if uv:
        commands.append([uv, "pip", "install", "--python", sys.executable, *args])

    output = ""
    for cmd in commands:
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                                    encoding="utf-8", errors="replace")
        except (OSError, subprocess.TimeoutExpired) as exc:
            output += f"{' '.join(cmd[:4])}: {exc}\n"
            continue
        output += result.stdout + result.stderr
        if result.returncode == 0:
            return True, output
        if "No module named pip" not in result.stderr:
            return False, output  # pip ran and failed; uv won't do better
    return False, output


def upgrade_ytdlp():
    """Upgrade yt-dlp[default,deno] to the latest release.

    Returns (ok, before_versions, after_versions, pip_output).
    """
    before = package_versions()
    ok, output = pip_install(["--upgrade", config.YTDLP_REQUIREMENT])
    after = package_versions()
    return ok, before, after, output


def ensure_app_requirements():
    """Install requirements.txt if Flask or Waitress is missing (first run)."""
    if all(importlib.util.find_spec(m) for m in ("flask", "waitress")):
        return
    log.info("Installing missing packages from requirements.txt ...")
    ok, output = pip_install(["-r", os.path.join(PROJECT_DIR, "requirements.txt")])
    if not ok:
        log.error("Could not install requirements:\n%s", output[-2000:])


def upgrade_at_startup():
    """Upgrade yt-dlp on every start. A failure is logged, not fatal."""
    log.info("Checking for a newer %s ...", config.YTDLP_REQUIREMENT)
    ok, before, after, output = upgrade_ytdlp()
    if not ok:
        log.warning("yt-dlp upgrade failed; continuing with the installed version.\n%s",
                    output[-2000:])
    for name in TRACKED_PACKAGES:
        old, new = before.get(name), after.get(name)
        if old != new:
            log.info("%s: %s -> %s", name, old or "not installed", new or "missing")
        else:
            log.info("%s: %s (up to date)", name, new or "MISSING")
