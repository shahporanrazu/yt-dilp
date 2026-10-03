"""Live test against real YouTube, using the app's own download code.

Run it from the app folder after the app has been started once (so .venv
exists). The app itself doesn't need to be running:

    Mac/Linux:  .venv/bin/python selftest.py --short "https://youtube.com/shorts/<id>"
    Windows:    .venv\Scripts\python selftest.py --short "https://youtube.com/shorts/<id>"

Add --video "<link>" to test with a shorter normal video than the default.

It downloads:
  1. a normal video at 1080p
  2. the same video through a link with &list= (must still be ONE video)
  3. a Short at 1080p (if --short is given)
  4. the normal video as MP3
and checks every file with ffprobe: the MP4s must have video AND AAC audio
and really be 1080p; the MP3 must be ~192 kbps and decode cleanly.
Files are deleted afterwards.
"""

import argparse
import logging
import shutil
import subprocess
import sys
import tempfile

from app import bootstrap, config
from app.errors import AppError          # these two don't import yt_dlp,
from app.urls import extract_video_id    # so they're safe before the upgrade

# Blender's "Big Buck Bunny" (10 min, up to 4K). Pass --video for a shorter one.
DEFAULT_VIDEO = "https://www.youtube.com/watch?v=aqz-KE-bpKQ"


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--video", default=DEFAULT_VIDEO, help="a normal video that has 1080p")
    parser.add_argument("--short", help="a YouTube Short (e.g. https://youtube.com/shorts/...)")
    parser.add_argument("--skip-upgrade", action="store_true", help="don't upgrade yt-dlp first")
    args = parser.parse_args()

    logging.basicConfig(level=logging.WARNING, format="  %(levelname)s %(name)s: %(message)s")
    if not args.skip_upgrade:
        logging.getLogger("app.bootstrap").setLevel(logging.INFO)
        bootstrap.upgrade_at_startup()

    from app import jobs, media, tools, youtube
    from app.cookies import store as cookie_store

    tools.run_checks()
    print("\nInstalled:")
    for check in tools.CHECKS:
        print(f"  {'OK ' if check['ok'] else 'BAD'} {check['label']}: {check['detail']}")
    if tools.PROBLEMS:
        print("\nCan't test, fix these first:\n  " + "\n  ".join(tools.PROBLEMS))
        return 1
    if config.YT_COOKIES.strip():
        cookie_store.set(config.YT_COOKIES, "YT_COOKIES setting")
    print(f"  cookies: {'loaded' if cookie_store.loaded else 'none'}, "
          f"proxy: {'set' if config.PROXY_URL else 'none'}\n")

    config.DOWNLOAD_DIR = tempfile.mkdtemp(prefix="yt-selftest-")
    video_id = extract_video_id(args.video)
    cases = [
        ("Normal video, 1080p", args.video, "1080"),
        ("Link with &list=, 1080p", f"https://www.youtube.com/watch?v={video_id}"
                                    f"&list=RD{video_id}&start_radio=1&index=1", "1080"),
        ("Short, 1080p", args.short, "1080"),
        ("Audio only, MP3", args.video, "mp3"),
    ]

    results = []
    for name, url, quality_key in cases:
        if not url:
            results.append((name, "SKIP", "no --short link given"))
            continue
        print(f"{name}: {url}")
        try:
            detail = run_case(url, quality_key, youtube, jobs, media)
            results.append((name, "PASS", detail))
        except (AppError, AssertionError) as exc:
            message = exc.message if isinstance(exc, AppError) else str(exc)
            if isinstance(exc, AppError) and exc.hint:
                message += f" ({exc.hint})"
            results.append((name, "FAIL", message))
            if isinstance(exc, AppError) and exc.details:
                print(f"  details: {exc.details[:500]}")
        print(f"  -> {results[-1][1]}: {results[-1][2]}\n")

    shutil.rmtree(config.DOWNLOAD_DIR, ignore_errors=True)
    import yt_dlp.version
    print(f"yt-dlp {yt_dlp.version.__version__}")
    for name, verdict, detail in results:
        print(f"  {verdict:4}  {name}: {detail}")
    return 1 if any(v == "FAIL" for _, v, _ in results) else 0


def run_case(url, quality_key, youtube, jobs, media):
    video_id = extract_video_id(url)
    raw, warnings = youtube.fetch_info(video_id, use_cache=False)
    for w in warnings:
        print(f"  yt-dlp warning: {w}")
    youtube.check_downloadable(raw)
    offered = [c["key"] for c in youtube.build_choices(raw)]
    print(f"  title: {raw.get('title')!r}; offered: {', '.join(offered)}")
    assert quality_key in offered, f"{quality_key} isn't offered for this video"

    job = jobs.Job(video_id, youtube.QUALITY_BY_KEY[quality_key], youtube.video_summary(raw))
    job.run()
    if job.state != "done":
        raise AppError(job.error["message"], job.error["hint"], job.error["details"])

    info = media.probe(job.file_path)
    streams = info["streams"]
    video = [s for s in streams if s["codec_type"] == "video"]
    audio = [s for s in streams if s["codec_type"] == "audio"]
    size_mb = job.summary["size"] / 1e6
    try:
        if quality_key == "mp3":
            assert audio and audio[0]["codec_name"] == "mp3", "not an MP3"
            kbps = int(audio[0].get("bit_rate") or 0) // 1000
            assert 180 <= kbps <= 200, f"bitrate is {kbps} kbps, expected 192"
            decode = subprocess.run(["ffmpeg", "-v", "error", "-i", job.file_path, "-f", "null", "-"],
                                    capture_output=True, text=True)
            assert decode.returncode == 0 and not decode.stderr.strip(), \
                f"MP3 doesn't decode cleanly: {decode.stderr[:200]}"
            return f"MP3 {kbps} kbps, plays cleanly, {size_mb:.1f} MB, {job.download_name!r}"

        assert video, "no video stream"
        assert audio, "NO AUDIO STREAM"
        assert all(a["codec_name"] == "aac" for a in audio), f"audio is {audio[0]['codec_name']}, not AAC"
        w, h = video[0]["width"], video[0]["height"]
        assert min(w, h) == 1080, f"video is {w}x{h}, not 1080p"
        return (f"{w}x{h} {video[0]['codec_name']} + {audio[0]['codec_name']} audio, "
                f"{size_mb:.1f} MB, {job.download_name!r}")
    finally:
        shutil.rmtree(job.dir, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
