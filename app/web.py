"""The Flask app: login, pages and the JSON API used by static/app.js."""

import hashlib
import hmac
import logging
import os
import secrets
import time
import uuid
from datetime import timedelta

from flask import (Flask, abort, jsonify, redirect, render_template, request,
                   send_file, session, url_for)
from werkzeug.middleware.proxy_fix import ProxyFix

from . import config, tools, updater, youtube
from .cookies import store as cookie_store
from .errors import AppError, explain
from .jobs import manager
from .urls import extract_video_id

log = logging.getLogger(__name__)

BOOT_ID = uuid.uuid4().hex          # changes on every (re)start; the page uses it
                                    # to notice that a restart has finished
MAX_LOGIN_FAILURES = 5              # per IP address ...
LOGIN_LOCK_SECONDS = 15 * 60        # ... then locked out for 15 minutes
_login_failures = {}                # ip -> [timestamps]


def _secret_key():
    """Key for signing the login cookie. Derived from APP_PASSWORD when no
    SESSION_SECRET is set, so changing the password logs everyone out."""
    if config.SESSION_SECRET:
        return config.SESSION_SECRET
    return hashlib.scrypt(config.APP_PASSWORD.encode(), salt=b"yt-dilp-session",
                          n=2 ** 14, r=8, p=1).hex()


def create_app():
    app = Flask(__name__, template_folder="../templates", static_folder="../static")
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
    app.config.update(
        SECRET_KEY=_secret_key(),
        PERMANENT_SESSION_LIFETIME=timedelta(days=config.SESSION_DAYS),
        SESSION_COOKIE_HTTPONLY=True,
        # Replit shows the app in an iframe on another site; that needs
        # SameSite=None (+Secure). CSRF tokens protect the API instead.
        SESSION_COOKIE_SAMESITE="None" if config.ON_REPLIT else "Lax",
        SESSION_COOKIE_SECURE=config.ON_REPLIT,
        MAX_CONTENT_LENGTH=2 * 1024 * 1024,   # cookies.txt uploads
    )

    # ------------------------------------------------------------ security

    @app.before_request
    def require_login():
        if request.endpoint in ("login", "static"):
            return None
        if not config.APP_PASSWORD:
            return render_template("login.html", missing_password=True), 503
        if not session.get("logged_in"):
            if request.path.startswith("/api/"):
                return jsonify(error={"message": "Please log in again."}), 401
            return redirect(url_for("login"))
        # Every state-changing request must carry the page's CSRF token.
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            sent = request.headers.get("X-CSRF-Token", "")
            if not hmac.compare_digest(sent, session.get("csrf", "")):
                return jsonify(error={"message": "Your session expired. Reload the page."}), 403
        return None

    @app.after_request
    def security_headers(response):
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        return response

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if not config.APP_PASSWORD:
            return render_template("login.html", missing_password=True), 503
        if request.method == "GET" and session.get("logged_in"):
            return redirect(url_for("index"))
        error = None
        if request.method == "POST":
            ip = request.remote_addr or "?"
            now = time.time()
            recent = [t for t in _login_failures.get(ip, []) if now - t < LOGIN_LOCK_SECONDS]
            if len(recent) >= MAX_LOGIN_FAILURES:
                error = "Too many wrong passwords. Try again in 15 minutes."
            elif hmac.compare_digest(request.form.get("password", "").encode(),
                                     config.APP_PASSWORD.encode()):
                _login_failures.pop(ip, None)
                session.clear()
                session.permanent = True            # 30-day login
                session["logged_in"] = True
                session["csrf"] = secrets.token_urlsafe(32)
                return redirect(url_for("index"))
            else:
                recent.append(now)
                _login_failures[ip] = recent
                time.sleep(0.5)
                error = "Wrong password."
        return render_template("login.html", error=error), (401 if error else 200)

    @app.post("/logout")
    def logout():
        session.clear()
        return jsonify(ok=True)

    # --------------------------------------------------------------- pages

    @app.get("/")
    def index():
        if "csrf" not in session:
            session["csrf"] = secrets.token_urlsafe(32)
        return render_template("index.html", csrf=session["csrf"],
                               qualities=youtube.QUALITIES,
                               default_quality=youtube.DEFAULT_QUALITY)

    # ----------------------------------------------------------------- API

    @app.errorhandler(AppError)
    def app_error(err):
        return jsonify(error=err.to_dict()), 400

    @app.get("/api/status")
    def status():
        return jsonify(_status())

    @app.post("/api/info")
    def info():
        _check_can_download()
        video_id = extract_video_id((request.get_json(silent=True) or {}).get("url"))
        raw, warnings = youtube.fetch_info(video_id, use_cache=False)
        youtube.check_downloadable(raw)
        return jsonify(video=youtube.video_summary(raw),
                       choices=youtube.build_choices(raw),
                       default_quality=youtube.DEFAULT_QUALITY,
                       warnings=warnings)

    @app.post("/api/download")
    def download():
        _check_can_download()
        body = request.get_json(silent=True) or {}
        video_id = extract_video_id(body.get("url"))
        quality = youtube.QUALITY_BY_KEY.get(body.get("quality"))
        if not quality:
            raise AppError("Pick a quality first.")
        raw, _ = youtube.fetch_info(video_id)          # cached from /api/info
        youtube.check_downloadable(raw)
        if quality.key not in {c["key"] for c in youtube.build_choices(raw)}:
            raise AppError(f"{quality.label} isn't available for this video.",
                           "Pick one of the qualities shown.")
        job = manager.start(video_id, quality, youtube.video_summary(raw))
        return jsonify(job=job.to_dict())

    @app.get("/api/jobs")
    def jobs():
        return jsonify(jobs=manager.list())

    @app.post("/api/jobs/<job_id>/remove")
    def remove_job(job_id):
        return jsonify(ok=manager.remove(job_id))

    @app.get("/api/jobs/<job_id>/file")
    def job_file(job_id):
        job = manager.get(job_id)
        if not job or job.state != "done" or not job.file_path or not os.path.exists(job.file_path):
            abort(404)
        mimetype = "audio/mpeg" if job.download_name.endswith(".mp3") else "video/mp4"
        # Flask encodes non-ASCII names (filename*=UTF-8''...), so titles in
        # any language and emoji survive.
        return send_file(job.file_path, mimetype=mimetype, as_attachment=True,
                         download_name=job.download_name, max_age=0)

    @app.post("/api/cookies")
    def save_cookies():
        upload = request.files.get("file")
        if upload:
            text = upload.read().decode("utf-8", errors="replace")
        else:
            text = (request.get_json(silent=True) or {}).get("text", "")
        try:
            cookie_store.set(text, "Settings")
        except ValueError as exc:
            raise AppError(str(exc))
        return jsonify(cookies=cookie_store.status())

    @app.post("/api/cookies/clear")
    def clear_cookies():
        cookie_store.clear()
        return jsonify(cookies=cookie_store.status())

    @app.post("/api/update")
    def update():
        if not updater.start():
            raise AppError("An update is already running.")
        return jsonify(update=updater.state)

    @app.errorhandler(Exception)
    def unexpected(err):
        if hasattr(err, "code") and hasattr(err, "get_response"):  # HTTP errors (404 ...)
            return err
        log.exception("Unexpected error")
        return jsonify(error=explain(err).to_dict()), 500

    return app


def _check_can_download():
    if tools.PROBLEMS:
        raise AppError("Downloads are disabled: " + " ".join(tools.PROBLEMS))
    if not manager.accepting or updater.is_running():
        raise AppError("yt-dlp is being updated.",
                       "Downloads are paused until the update and restart finish.")


def _status():
    checks = list(tools.CHECKS)
    cookies = cookie_store.status()
    if cookies["loaded"]:
        detail = (f"{cookies['count']} cookies from {cookies['source']}, "
                  + ("signed in" if cookies["signed_in"] else "not signed in"))
    else:
        detail = "Not loaded (optional)"
    checks.append({"key": "cookies", "label": "Cookies", "ok": cookies["loaded"],
                   "detail": detail, "optional": True, "warning": cookies["warning"]})
    checks.append({"key": "proxy", "label": "Proxy", "ok": bool(config.PROXY_URL),
                   "detail": "Set (PROXY_URL)" if config.PROXY_URL else "Not set (optional)",
                   "optional": True})
    return {
        "boot_id": BOOT_ID,
        "can_download": not tools.PROBLEMS,
        "problems": tools.PROBLEMS,
        "checks": checks,
        "cookies": cookies,
        "update": updater.state,
        "busy": manager.busy(),
    }
