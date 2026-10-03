"""yt-dlp + ffmpeg end to end (only the YouTube extraction is faked).

Every MP4 is checked with ffprobe for a video stream AND an AAC audio
stream at the expected resolution.
"""

import shutil
import subprocess
import time

import pytest

from app import jobs, youtube
from app import media as media_tools
from app.errors import AppError
from conftest import COMBINED, LIVE, NORMAL, OPUS_ONLY, SHORT, SILENT, TITLE

Q = youtube.QUALITY_BY_KEY


def choices_for(video_id):
    raw, _ = youtube.fetch_info(video_id)
    return {c["key"]: c for c in youtube.build_choices(raw)}


def streams(path):
    info = media_tools.probe(path)
    video = [s for s in info["streams"] if s["codec_type"] == "video"]
    audio = [s for s in info["streams"] if s["codec_type"] == "audio"]
    return video, audio


def moov_before_mdat(path):
    """+faststart puts the 'moov' index before the media data."""
    data = open(path, "rb").read()
    return 0 < data.find(b"moov") < data.find(b"mdat")


# ------------------------------------------------------------ choices

def test_normal_video_offers_every_quality_with_sizes():
    choices = choices_for(NORMAL)
    assert list(choices) == ["best", "1080", "720", "480", "360", "mp3"]
    assert choices["1080"]["detail"] == "1920×1080 · H.264"
    assert choices["1080"]["note"] is None
    assert choices["best"]["detail"].startswith("2160p · 3840×2160 · VP9")
    assert "VLC" in choices["best"]["note"]
    assert all(c["size"] for c in choices.values())


def test_named_qualities_prefer_h264_and_aac():
    raw, _ = youtube.fetch_info(NORMAL)
    picked = youtube.select_formats(raw, Q["1080"])
    assert picked["video"]["format_id"] == "137"      # H.264, not the VP9 248
    assert picked["audio"]["format_id"] == "140"      # AAC, not Opus 251
    best = youtube.select_formats(raw, Q["best"])
    assert best["video"]["format_id"] == "313" and best["audio"]["format_id"] == "140"


def test_vertical_short_counts_as_1080p():
    choices = choices_for(SHORT)
    assert list(choices) == ["best", "1080", "720", "480", "360", "mp3"]
    assert choices["1080"]["detail"] == "1080×1920 · H.264"


def test_missing_qualities_are_not_offered():
    assert list(choices_for(COMBINED)) == ["best", "360", "mp3"]


def test_silent_and_live_videos_are_refused_before_downloading():
    for video_id, text in [(SILENT, "doesn't offer any audio"), (LIVE, "still running")]:
        raw, _ = youtube.fetch_info(video_id)
        with pytest.raises(AppError, match=text):
            youtube.check_downloadable(raw)


# ----------------------------------------------------------- downloads

def run_job(video_id, quality_key):
    """Run one job on this thread, recording (progress, stage) at every hook call."""
    raw, _ = youtube.fetch_info(video_id)
    job = jobs.Job(video_id, Q[quality_key], youtube.video_summary(raw))
    seen = []
    on_progress, on_postprocess = job.on_progress, job.on_postprocess

    def spy_progress(d):
        on_progress(d)
        seen.append((job.progress, job.stage))

    def spy_postprocess(d):
        on_postprocess(d)
        seen.append((job.progress, job.stage))

    job.on_progress, job.on_postprocess = spy_progress, spy_postprocess
    job.run()
    assert job.state == "done", job.error
    return job, seen


@pytest.mark.parametrize("video_id, quality, size", [
    (NORMAL, "1080", (1920, 1080)),
    (NORMAL, "720", (1280, 720)),
    (NORMAL, "360", (640, 360)),
    (SHORT, "1080", (1080, 1920)),
    (SHORT, "720", (720, 1280)),
])
def test_mp4_has_video_and_aac_audio_at_the_right_resolution(video_id, quality, size):
    job, _ = run_job(video_id, quality)
    video, audio = streams(job.file_path)
    assert len(video) == 1 and len(audio) == 1
    assert video[0]["codec_name"] == "h264"
    assert (video[0]["width"], video[0]["height"]) == size
    assert audio[0]["codec_name"] == "aac"
    assert moov_before_mdat(job.file_path)
    assert job.download_name.endswith(f"[{quality}p].mp4")
    assert job.summary["quality"] == int(quality)


def test_best_quality_is_4k_vp9_with_aac():
    job, _ = run_job(NORMAL, "best")
    video, audio = streams(job.file_path)
    assert (video[0]["codec_name"], video[0]["width"], video[0]["height"]) == ("vp9", 3840, 2160)
    assert audio[0]["codec_name"] == "aac"
    assert job.download_name == "Café déjà vu — 日本語のタイトル 🎉🚀 - Part 1-2 [2160p].mp4"


def test_opus_audio_is_reencoded_to_aac_and_video_copied(media):
    job, _ = run_job(OPUS_ONLY, "1080")
    video, audio = streams(job.file_path)
    assert audio[0]["codec_name"] == "aac"
    assert video[0]["codec_name"] == "h264"
    # Video copied untouched: same packets as the source stream.
    src = media["h264_1080.mp4"]
    def packets(p):
        return subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-count_packets",
                               "-show_entries", "stream=nb_read_packets", "-of", "csv=p=0", p],
                              capture_output=True, text=True).stdout.strip()
    assert packets(job.file_path) == packets(src)


def test_combined_only_video_falls_back_to_single_stream():
    job, _ = run_job(COMBINED, "360")
    video, audio = streams(job.file_path)
    assert video and audio[0]["codec_name"] == "aac"


def test_mp3_is_192kbps_and_plays():
    job, _ = run_job(NORMAL, "mp3")
    assert job.download_name == f"{TITLE.replace(':', ' -').replace('/', '-')} [MP3].mp3"
    _, audio = streams(job.file_path)
    assert audio[0]["codec_name"] == "mp3"
    assert 180 <= int(audio[0]["bit_rate"]) // 1000 <= 200
    decode = subprocess.run(["ffmpeg", "-v", "error", "-i", job.file_path, "-f", "null", "-"],
                            capture_output=True, text=True)
    assert decode.returncode == 0 and decode.stderr == ""


def test_progress_is_one_bar_that_never_goes_back():
    job, seen = run_job(NORMAL, "1080")
    values = [p for p, _ in seen]
    assert values == sorted(values)                     # never jumps back
    stages = [s for _, s in seen]
    assert stages.index("Downloading video") < stages.index("Downloading audio") < stages.index("Merging")
    assert job.progress == 100 and job.stage == "Ready"


def test_mp3_stages():
    _, seen = run_job(NORMAL, "mp3")
    stages = [s for _, s in seen]
    assert "Downloading audio" in stages and "Converting to MP3" in stages


def test_file_without_sound_is_a_failed_download(media, tmp_path):
    silent = str(tmp_path / "silent.mp4")
    shutil.copy(media["h264_1080.mp4"], silent)
    with pytest.raises(AppError, match="no sound"):
        media_tools.finalize_mp4(silent)


def test_queue_runs_at_most_two_downloads_at_once(manager):
    raw, _ = youtube.fetch_info(NORMAL)
    video = youtube.video_summary(raw)
    started = [manager.start(NORMAL, Q["360"], video) for _ in range(4)]
    max_running = 0
    while any(j.state in ("queued", "running") for j in started):
        max_running = max(max_running, sum(j.state == "running" for j in started))
        listed = manager.list()
        positions = [j["queue_position"] for j in listed if j["state"] == "queued"]
        assert sorted(positions) == list(range(1, len(positions) + 1))
        time.sleep(0.02)
    assert max_running <= 2
    assert all(j.state == "done" for j in started)
