"""Start the app:  python main.py

1. Install Flask/Waitress if missing, and upgrade yt-dlp[default,deno]
   (YouTube breaks old yt-dlp versions quickly) - BEFORE importing yt_dlp.
2. Check for ffmpeg, ffprobe and Deno; load cookies from YT_COOKIES.
3. Serve with Waitress: ONE process, many threads. Download jobs are kept
   in memory, so never run this with several worker processes.
"""

import logging
import sys

from app import bootstrap, config

logging.basicConfig(level=logging.INFO, stream=sys.stdout,
                    format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")
log = logging.getLogger("main")


def main():
    bootstrap.ensure_app_requirements()
    bootstrap.upgrade_at_startup()

    # Only now is it safe to import anything that imports yt_dlp.
    from waitress import serve

    from app import jobs, tools
    from app.cookies import store as cookie_store
    from app.web import create_app

    tools.run_checks()
    jobs.reset_download_dir()
    jobs.start_cleanup_thread()

    if config.YT_COOKIES.strip():
        try:
            cookie_store.set(config.YT_COOKIES, "YT_COOKIES secret")
        except ValueError as exc:
            log.error("YT_COOKIES secret ignored: %s", exc)
    if not config.APP_PASSWORD:
        log.error("APP_PASSWORD secret is not set - the app will refuse all requests.")

    app = create_app()
    log.info("Serving on http://%s:%d", config.HOST, config.PORT)
    serve(app, host=config.HOST, port=config.PORT, threads=config.SERVER_THREADS,
          ident=None, channel_timeout=300)


if __name__ == "__main__":
    main()
