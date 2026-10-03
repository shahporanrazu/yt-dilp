"""Background download jobs, kept in memory.

* At most MAX_PARALLEL_DOWNLOADS run at once; the rest wait in a queue.
* Progress is ONE number for the whole job: the bytes of the video and
  audio streams are added together, so the bar never jumps back to 0.
  Downloading fills 0-95 %, merging/converting 96 %, final checks 98 %.
* Finished files are deleted FILE_LIFETIME_SECONDS after they finish.

Because jobs live in memory, the app must run as ONE process (with
several threads) - see main.py.
"""

import logging
import os
import re
import secrets
import shutil
import threading
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor

from . import config, media, youtube
from .errors import explain

log = logging.getLogger(__name__)


# Characters that aren't allowed in file names on Windows/macOS/Linux.
FILENAME_FIXES = str.maketrans({"/": "-", "\\": "-", "|": "-", ":": " -", '"': "'",
                                "<": "", ">": "", "?": "", "*": ""})


def make_download_name(title, tag, ext):
    """'Café 🎉: Live/Remix' -> 'Café 🎉 - Live-Remix [1080p].mp4'.

    Keeps non-English characters and emoji; only removes characters that
    file systems reject, and keeps the name under ~200 bytes.
    """
    name = unicodedata.normalize("NFC", title or "")
    name = "".join(ch for ch in name if unicodedata.category(ch) != "Cc")
    name = re.sub(r"\s+", " ", name.translate(FILENAME_FIXES)).strip(" .")
    while len(name.encode("utf-8")) > 180:
        name = name[:-1]
    name = name.rstrip(" .") or "video"
    return f"{name} [{tag}].{ext}"


class Job:
    def __init__(self, video_id, quality, video):
        self.id = secrets.token_urlsafe(9)
        self.video_id = video_id
        self.quality = quality                  # a youtube.Quality
        self.title = video["title"]
        self.thumbnail = video["thumbnail"]
        self.thumbnail_fallback = video["thumbnail_fallback"]
        self.created_at = time.time()
        self.finished_at = None

        self.state = "queued"                   # queued, running, done, error, expired
        self.stage = "Waiting in queue"
        self.progress = 0.0                     # 0-100, only ever goes up
        self.downloaded = 0
        self.total = 0
        self.speed = None                       # bytes per second
        self.eta = None                         # seconds
        self.parts = {}                         # format_id -> {kind, total, done}

        self.error = None                       # {message, hint, details}
        self.file_path = None
        self.download_name = None
        self.summary = None                     # what ffprobe found in the file

    @property
    def dir(self):
        return os.path.join(config.DOWNLOAD_DIR, self.id)

    # ----- progress (called from yt-dlp's hooks, on the worker thread)

    def add_part(self, fmt):
        """Register a stream (video or audio) we expect to download."""
        format_id = fmt.get("format_id")
        if format_id in self.parts:
            return
        kind = "video" if fmt.get("vcodec") not in (None, "none") else "audio"
        size = fmt.get("filesize") or fmt.get("filesize_approx")
        self.parts[format_id] = {"kind": kind, "total": size, "done": 0}

    def on_progress(self, d):
        info = d.get("info_dict") or {}
        self.add_part(info)
        part = self.parts[info.get("format_id")]

        if d.get("status") == "downloading":
            part["done"] = d.get("downloaded_bytes") or 0
            part["total"] = d.get("total_bytes") or d.get("total_bytes_estimate") or part["total"]
            self.stage = "Downloading video" if part["kind"] == "video" else "Downloading audio"
            self.speed = d.get("speed")
        elif d.get("status") == "finished":
            size = d.get("total_bytes") or d.get("downloaded_bytes")
            if size:
                part["done"] = part["total"] = size

        done = sum(p["done"] for p in self.parts.values())
        total = sum(max(p["total"] or 0, p["done"]) for p in self.parts.values())
        self.downloaded, self.total = done, total
        if total:
            self.progress = max(self.progress, 95 * done / total)
        self.eta = (total - done) / self.speed if self.speed and total > done else None

    def on_postprocess(self, d):
        if d.get("status") != "started":
            return
        name = d.get("postprocessor")
        if name == "Merger":
            self.set_stage("Merging", 96)
        elif name == "ExtractAudio":
            self.set_stage("Converting to MP3", 96)

    def set_stage(self, stage, progress):
        self.stage = stage
        self.progress = max(self.progress, progress)
        self.speed = self.eta = None

    # ----- the actual work

    def run(self):
        self.state = "running"
        self.stage = "Starting download"
        quality = self.quality
        try:
            os.makedirs(self.dir, exist_ok=True)
            raw, _ = youtube.fetch_info(self.video_id)
            youtube.check_downloadable(raw)
            picked = youtube.select_formats(raw, quality)
            for fmt in (picked or {}).get("parts", []):
                self.add_part(fmt)

            path = youtube.download(raw, quality, self.dir, self.on_progress, self.on_postprocess)

            self.set_stage("Finishing", 98)
            if quality.audio_only:
                self.summary = media.check_mp3(path)
                tag, ext = "MP3", "mp3"
            else:
                self.summary = media.finalize_mp4(path)   # ffprobe: video + AAC audio
                tag, ext = f"{self.summary['quality']}p", "mp4"

            self.file_path = path
            self.download_name = make_download_name(self.title, tag, ext)
            self.progress, self.stage, self.state = 100, "Ready", "done"
            log.info("Job %s done: %s (%s)", self.id, self.download_name, self.summary)
        except Exception as exc:
            error = explain(exc)
            if error is not exc:
                log.exception("Job %s failed", self.id)
            else:
                log.warning("Job %s failed: %s | %s", self.id, error.message, error.details)
            self.error = error.to_dict()
            self.state, self.stage = "error", "Failed"
            shutil.rmtree(self.dir, ignore_errors=True)
        finally:
            self.speed = self.eta = None
            self.finished_at = time.time()

    def to_dict(self, queue_position=None):
        expires_at = (self.finished_at + config.FILE_LIFETIME_SECONDS
                      if self.state == "done" else None)
        return {
            "id": self.id,
            "video_id": self.video_id,
            "title": self.title,
            "thumbnail": self.thumbnail,
            "thumbnail_fallback": self.thumbnail_fallback,
            "quality": self.quality.key,
            "quality_label": self.quality.label,
            "state": self.state,
            "stage": self.stage,
            "progress": round(self.progress, 1),
            "downloaded": self.downloaded,
            "total": self.total,
            "speed": self.speed,
            "eta": self.eta,
            "queue_position": queue_position,
            "error": self.error,
            "download_name": self.download_name,
            "summary": self.summary,
            "expires_at": expires_at,
        }


class JobManager:
    def __init__(self):
        self._jobs = {}
        self._lock = threading.Lock()
        self._pool = ThreadPoolExecutor(max_workers=config.MAX_PARALLEL_DOWNLOADS,
                                        thread_name_prefix="download")
        self.accepting = True      # switched off while yt-dlp is being updated

    def start(self, video_id, quality, video):
        job = Job(video_id, quality, video)
        with self._lock:
            self._jobs[job.id] = job
        self._pool.submit(job.run)
        log.info("Job %s queued: %s [%s]", job.id, video_id, quality.key)
        return job

    def get(self, job_id):
        with self._lock:
            return self._jobs.get(job_id)

    def remove(self, job_id):
        """Forget a finished job and delete its file. Running jobs can't be removed."""
        with self._lock:
            job = self._jobs.get(job_id)
            if not job or job.state in ("queued", "running"):
                return False
            del self._jobs[job_id]
        shutil.rmtree(job.dir, ignore_errors=True)
        return True

    def busy(self):
        """True while any job is queued or running."""
        with self._lock:
            return any(j.state in ("queued", "running") for j in self._jobs.values())

    def list(self):
        """All jobs, newest first, with queue positions for waiting ones."""
        with self._lock:
            jobs = sorted(self._jobs.values(), key=lambda j: j.created_at)
        queued = [j for j in jobs if j.state == "queued"]
        return [j.to_dict(queued.index(j) + 1 if j in queued else None)
                for j in reversed(jobs)]

    def cleanup(self):
        """Delete files 30 min after they finished; forget very old jobs."""
        now = time.time()
        with self._lock:
            jobs = list(self._jobs.values())
        for job in jobs:
            if not job.finished_at:
                continue
            age = now - job.finished_at
            if job.state == "done" and age > config.FILE_LIFETIME_SECONDS:
                shutil.rmtree(job.dir, ignore_errors=True)
                job.file_path = None
                job.state, job.stage = "expired", "File deleted after 30 minutes"
                log.info("Job %s expired, file deleted", job.id)
            if age > config.JOB_RECORD_SECONDS:
                with self._lock:
                    self._jobs.pop(job.id, None)


def reset_download_dir():
    """Empty the download folder (called at startup)."""
    shutil.rmtree(config.DOWNLOAD_DIR, ignore_errors=True)
    os.makedirs(config.DOWNLOAD_DIR, exist_ok=True)


def _cleanup_loop():
    while True:
        time.sleep(30)
        try:
            manager.cleanup()
        except Exception:
            log.exception("Cleanup failed")


def start_cleanup_thread():
    threading.Thread(target=_cleanup_loop, name="cleanup", daemon=True).start()


manager = JobManager()
