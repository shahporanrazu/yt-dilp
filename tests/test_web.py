"""The Flask app: login, CSRF, info, download, saving the file, cookies."""

import io
import re
import time
from urllib.parse import unquote

import pytest

from app import config, web
from app.cookies import store as cookie_store
from conftest import LIVE, NORMAL, SHORT, SILENT, TITLE

PASSWORD = "test-password"


@pytest.fixture
def app(monkeypatch):
    monkeypatch.setattr(config, "APP_PASSWORD", PASSWORD)
    web._login_failures.clear()
    return web.create_app()


@pytest.fixture
def client(app):
    c = app.test_client()
    c.post("/login", data={"password": PASSWORD})
    page = c.get("/").get_data(as_text=True)
    c.csrf = re.search(r'name="csrf-token" content="([^"]+)"', page).group(1)
    return c


def post(client, path, **kwargs):
    return client.post(path, headers={"X-CSRF-Token": client.csrf}, **kwargs)


def wait_done(client, job_id, timeout=120):
    end = time.time() + timeout
    while time.time() < end:
        job = next(j for j in client.get("/api/jobs").json["jobs"] if j["id"] == job_id)
        if job["state"] not in ("queued", "running"):
            return job
        time.sleep(0.2)
    raise AssertionError("job did not finish")


# --------------------------------------------------------------- login

def test_everything_needs_login(app):
    c = app.test_client()
    assert c.get("/").status_code == 302
    assert c.get("/api/status").status_code == 401
    assert c.post("/api/info", json={"url": "x"}).status_code == 401
    assert c.get("/static/app.css").status_code == 200       # login page needs it


def test_wrong_password_and_lockout(app):
    c = app.test_client()
    for _ in range(5):
        assert c.post("/login", data={"password": "nope"}).status_code == 401
    r = c.post("/login", data={"password": PASSWORD})
    assert "Too many" in r.get_data(as_text=True)


def test_login_lasts_30_days(app):
    c = app.test_client()
    r = c.post("/login", data={"password": PASSWORD})
    assert r.status_code == 302
    cookie = r.headers["Set-Cookie"]
    assert "HttpOnly" in cookie and "Expires=" in cookie
    assert app.permanent_session_lifetime.days == 30


def test_missing_app_password_locks_the_app(monkeypatch):
    monkeypatch.setattr(config, "APP_PASSWORD", "")
    monkeypatch.setattr(config, "SESSION_SECRET", "x")
    c = web.create_app().test_client()
    r = c.get("/")
    assert r.status_code == 503 and "APP_PASSWORD" in r.get_data(as_text=True)


def test_post_without_csrf_token_is_rejected(client):
    r = client.post("/api/info", json={"url": f"https://youtu.be/{NORMAL}"})
    assert r.status_code == 403


# ---------------------------------------------------------- info + jobs

def test_info_with_playlist_link(client):
    r = post(client, "/api/info",
             json={"url": f"https://www.youtube.com/watch?v={NORMAL}&list=PLxyz123&index=4"})
    assert r.status_code == 200, r.json
    assert r.json["video"]["id"] == NORMAL
    assert r.json["video"]["title"] == TITLE
    assert [c["key"] for c in r.json["choices"]] == ["best", "1080", "720", "480", "360", "mp3"]
    assert r.json["default_quality"] == "1080"


@pytest.mark.parametrize("url, message", [
    ("https://vimeo.com/1", "isn't a YouTube link"),
    (f"https://youtu.be/{LIVE}", "still running"),
    (f"https://youtu.be/{SILENT}", "doesn't offer any audio"),
    ("https://youtu.be/zzzzzzzzzzz", "unavailable"),
])
def test_info_errors_are_explained(client, url, message):
    r = post(client, "/api/info", json={"url": url})
    assert r.status_code == 400
    assert message in r.json["error"]["message"]


def test_download_and_save_with_unicode_filename(client):
    r = post(client, "/api/download",
             json={"url": f"https://www.youtube.com/watch?v={NORMAL}&list=PL1", "quality": "1080"})
    assert r.status_code == 200, r.json
    job = wait_done(client, r.json["job"]["id"])
    assert job["state"] == "done", job["error"]
    assert job["progress"] == 100
    assert job["summary"]["width"] == 1920 and job["summary"]["audio_codec"] == "AAC"

    r = client.get(f"/api/jobs/{job['id']}/file")
    assert r.status_code == 200
    assert r.mimetype == "video/mp4"
    disposition = r.headers["Content-Disposition"]
    encoded = re.search(r"filename\*=UTF-8''(\S+)", disposition).group(1)
    assert unquote(encoded) == "Café déjà vu — 日本語のタイトル 🎉🚀 - Part 1-2 [1080p].mp4"
    assert len(r.data) == job["summary"]["size"]
    r.close()


def test_short_download(client):
    r = post(client, "/api/download",
             json={"url": f"https://youtube.com/shorts/{SHORT}?feature=share", "quality": "1080"})
    job = wait_done(client, r.json["job"]["id"])
    assert (job["summary"]["width"], job["summary"]["height"]) == (1080, 1920)
    assert job["download_name"] == "Vertical short #shorts [1080p].mp4"


def test_unavailable_quality_is_refused(client):
    from conftest import COMBINED
    r = post(client, "/api/download", json={"url": f"https://youtu.be/{COMBINED}", "quality": "1080"})
    assert r.status_code == 400 and "isn't available" in r.json["error"]["message"]


def test_removed_job_file_is_gone(client):
    r = post(client, "/api/download", json={"url": f"https://youtu.be/{NORMAL}", "quality": "mp3"})
    job = wait_done(client, r.json["job"]["id"])
    assert post(client, f"/api/jobs/{job['id']}/remove").json["ok"]
    assert client.get(f"/api/jobs/{job['id']}/file").status_code == 404


# ---------------------------------------------------- status + cookies

def test_status_lists_all_checks(client):
    status = client.get("/api/status").json
    keys = [c["key"] for c in status["checks"]]
    assert keys == ["ytdlp", "ejs", "deno", "ffmpeg", "ffprobe", "cookies", "proxy"]
    assert status["can_download"] is True


COOKIES = ".youtube.com\tTRUE\t/\tTRUE\t1999999999\tSAPISID\ttop-secret-cookie-value\n"


def test_cookies_paste_upload_clear_never_echo_values(client):
    try:
        r = post(client, "/api/cookies", json={"text": COOKIES})
        assert r.status_code == 200 and r.json["cookies"]["signed_in"]
        r = post(client, "/api/cookies",
                 data={"file": (io.BytesIO(COOKIES.encode()), "cookies.txt")},
                 content_type="multipart/form-data")
        assert r.status_code == 200 and r.json["cookies"]["count"] == 1
        assert "top-secret" not in client.get("/api/status").get_data(as_text=True)

        r = post(client, "/api/cookies", json={"text": "not cookies"})
        assert r.status_code == 400

        assert post(client, "/api/cookies/clear").json["cookies"]["loaded"] is False
    finally:
        cookie_store.clear()
