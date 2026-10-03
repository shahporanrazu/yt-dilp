"""Start Video Saver:  python main.py   (start.sh / start.bat do this for you)

main.py is a small launcher. It
  1. checks the Python version and asks for a password on the first start,
  2. runs the server (`main.py --serve`) as a child process, and starts it
     again whenever it exits with RESTART_EXIT_CODE (after "Update yt-dlp").

The server process:
  1. installs Flask/Waitress if missing and upgrades yt-dlp[default,deno]
     (YouTube breaks old yt-dlp versions quickly) - BEFORE importing yt_dlp,
  2. checks for ffmpeg, ffprobe and Deno; loads cookies from YT_COOKIES,
  3. serves with Waitress: ONE process, many threads. Download jobs live in
     memory, so never run it with several worker processes.
"""

import sys

if sys.version_info < (3, 11):
    sys.exit(f"Video Saver needs Python 3.11 or newer (this is {sys.version.split()[0]}). "
             "Get it from https://www.python.org/downloads/")

import getpass  # noqa: E402
import logging  # noqa: E402
import os  # noqa: E402
import socket  # noqa: E402
import subprocess  # noqa: E402
import threading  # noqa: E402
import webbrowser  # noqa: E402

from app import bootstrap, config  # noqa: E402

log = logging.getLogger("main")
OPEN_BROWSER_ENV = "VIDEO_SAVER_OPEN_BROWSER"


# ------------------------------------------------------------------ launcher

def launch():
    """Run the server; restart it after updates; stop on Ctrl+C."""
    ask_for_password()
    open_browser = True
    while True:
        env = dict(os.environ, **{OPEN_BROWSER_ENV: "1" if open_browser else ""})
        child = subprocess.Popen([sys.executable, os.path.abspath(__file__), "--serve"], env=env)
        try:
            code = child.wait()
        except KeyboardInterrupt:          # Ctrl+C reaches the server as well
            try:
                child.wait(timeout=10)
            except subprocess.TimeoutExpired:
                child.kill()
            return 0
        if code != config.RESTART_EXIT_CODE:
            return code
        print("\nRestarting with the updated yt-dlp ...\n", flush=True)
        open_browser = False


def ask_for_password():
    """On the first start, ask for a password and save it in settings.env."""
    if config.APP_PASSWORD or not sys.stdin.isatty():
        return
    print("\nWelcome! Choose a password for Video Saver.")
    print(f"It's saved in {config.SETTINGS_FILE} - edit that file to change it.\n")
    while True:
        password = getpass.getpass("Password: ").strip()
        if len(password) < 4:
            print("Please use at least 4 characters.")
        elif getpass.getpass("Same password again: ").strip() != password:
            print("The two passwords don't match. Try again.")
        else:
            config.save_setting("APP_PASSWORD", password)
            print()
            return


# -------------------------------------------------------------------- server

def lan_address():
    """This computer's address on the local network (for phones), or None."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))   # no packet is sent
            return s.getsockname()[0]
    except OSError:
        return None


def serve():
    logging.basicConfig(level=logging.INFO, stream=sys.stdout,
                        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    bootstrap.ensure_app_requirements()
    bootstrap.upgrade_at_startup()

    # Only now is it safe to import anything that imports yt_dlp.
    from waitress import create_server

    from app import jobs, tools
    from app.cookies import store as cookie_store
    from app.web import create_app

    tools.run_checks()
    jobs.reset_download_dir()
    jobs.start_cleanup_thread()

    if config.YT_COOKIES.strip():
        try:
            cookie_store.set(config.YT_COOKIES, "YT_COOKIES setting")
        except ValueError as exc:
            log.error("YT_COOKIES ignored: %s", exc)
    if not config.APP_PASSWORD:
        log.error("No APP_PASSWORD set - the app will refuse all requests. "
                  "Add APP_PASSWORD=... to %s and start again.", config.SETTINGS_FILE)

    try:
        server = create_server(create_app(), host=config.HOST, port=config.PORT,
                               threads=config.SERVER_THREADS, ident=None, channel_timeout=300)
    except OSError as exc:
        log.error("Can't use port %d (%s). Is Video Saver already running? To use another "
                  "port, add PORT=8766 to %s.", config.PORT, exc, config.SETTINGS_FILE)
        sys.exit(1)

    url = f"http://localhost:{config.PORT}"
    log.info("Video Saver is running: open %s", url)
    if config.HOST in ("0.0.0.0", "::") and (ip := lan_address()):
        log.info("From a phone on the same Wi-Fi: http://%s:%d", ip, config.PORT)
    log.info("Press Ctrl+C to stop.")
    if os.environ.get(OPEN_BROWSER_ENV):
        threading.Timer(1.0, webbrowser.open, [url]).start()

    try:
        server.run()
    except KeyboardInterrupt:
        pass
    log.info("Stopped.")
    logging.shutdown()
    os._exit(0)   # don't wait for unfinished downloads


if __name__ == "__main__":
    if "--serve" in sys.argv:
        serve()
    else:
        sys.exit(launch())
