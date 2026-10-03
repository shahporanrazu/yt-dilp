# Video Saver: a private YouTube → MP4 downloader

A small password-protected web app. You paste a YouTube link, pick a quality,
and get an MP4 **with sound** (or an MP3). Everything YouTube-related goes
through [yt-dlp](https://github.com/yt-dlp/yt-dlp)'s Python API. Merging,
checking and converting are done with ffmpeg/ffprobe.

- Python 3.11+, Flask, plain HTML/CSS/JS (no build step)
- One process with many threads (Waitress), because download jobs live in memory
- `yt-dlp[default,deno]` is upgraded to the latest release **every time the app starts**

## Set up on Replit

1. Import this repository into Replit (Python).
2. Add **Secrets** (Tools → Secrets):

   | Secret | Required | What it is |
   |---|---|---|
   | `APP_PASSWORD` | yes | Password for the login page. Without it the app refuses every request. |
   | `YT_COOKIES` | no | The whole contents of a Netscape-format `cookies.txt` for youtube.com (see below). |
   | `PROXY_URL` | no | A proxy for yt-dlp, e.g. `http://user:pass@host:port` or `socks5://host:port`. |
   | `SESSION_SECRET` | no | Key for signing the login cookie. If unset, it's derived from `APP_PASSWORD`. |

3. Press **Run**. `.replit` already installs **ffmpeg** (with ffprobe) as a system
   dependency. That's the real binary, not the `ffmpeg` pip package. If the status panel says
   ffmpeg is missing, add it under Tools → System dependencies (search "ffmpeg").
4. Open the app and log in. The green **Ready** chip means yt-dlp, yt-dlp-ejs, Deno,
   ffmpeg and ffprobe were all found. Click it for details.

**Deploying:** use a **Reserved VM** deployment (already set in `.replit`). Autoscale
would run several instances and lose in-memory jobs.

**Logging in from the Replit preview pane:** the preview is an iframe, and some
browsers block its cookies. If the login page keeps coming back, open the app
in its own tab.

## Using it

1. Paste a link and press Enter or **Get video**. These all work: `youtube.com/watch?v=`,
   `youtu.be/`, `m.youtube.com`, `music.youtube.com`, `/shorts/` and `/live/` (finished
   streams only). Playlist and tracking parameters (`&list=`, `?si=`, `&t=`) are dropped.
2. Pick a quality. Only the qualities this video really has are shown, with estimated sizes.
3. **Download** starts a background job: one progress bar, the stage, speed and time left.
   At most 2 downloads run at once; the rest wait in a queue.
4. **Save file** gives you `<video title> [1080p].mp4`. Any language and emoji work.
   Files are deleted from the server 30 minutes after they finish.

### Quality rules

| Option | yt-dlp format | format_sort |
|---|---|---|
| Best available (up to 4K) | `bv+ba/b` | `res`, `hdr:sdr`, `acodec:aac` |
| 1080p / 720p / 480p / 360p | `bv+ba/b` | `res:<H>`, `vcodec:h264`, `acodec:aac` |
| Audio only | `ba/b` → MP3 192 kbps | (default) |

- `res` is the **shorter side** of the frame, so a vertical 1080×1920 Short counts as 1080p.
- The named qualities prefer H.264 + AAC, which plays everywhere. Above 1080p YouTube only
  has VP9/AV1. The app says so next to the option (older devices may need VLC).
- Video is always merged to MP4. Every file is then checked with ffprobe: it **must**
  have a video stream and an audio stream, otherwise the download counts as failed. Non-AAC
  audio is re-encoded to AAC 192 kbps (the video is copied untouched), and every MP4 gets
  `-movflags +faststart`.
- Videos where YouTube offers no audio track at all are refused before downloading.
  Live streams that are still running are refused too.

## When YouTube blocks the server

Replit runs on datacenter IPs, which YouTube distrusts. The app already forces IPv4 and
leaves yt-dlp's client choice at its defaults. If you see **"YouTube is blocking this
server"** ("Sign in to confirm you're not a bot"):

1. Open a private/incognito window and sign in to YouTube (a spare Google account is safest).
2. Export youtube.com cookies with a cookies.txt extension (e.g. "Get cookies.txt LOCALLY").
3. Close the private window right away, so YouTube doesn't replace those cookies.
4. Paste or upload the file in **Settings → YouTube cookies**. It works immediately; no restart.
   To keep it across restarts, also put it in the `YT_COOKIES` secret.

Cookie contents are never shown in the UI or written to the logs. They're kept in a
private (0600) temp file. Age-restricted, private and members-only videos also need
cookies, from an account that has access. If blocking continues, set `PROXY_URL`.

**Update yt-dlp** (Settings) installs the latest `yt-dlp[default,deno]` and restarts the
app once no download is running. Try that first when formats go missing or downloads fail
with HTTP 403.

## Testing

Offline tests. They need ffmpeg but not YouTube: real yt-dlp format selection, download,
merge, ffprobe checks and MP3 conversion run on generated media files.

```bash
pip install pytest
python -m pytest
```

Live test against real YouTube (run on the server; it uses the app's own code):

```bash
python selftest.py --short "https://youtube.com/shorts/<id>"
python selftest.py --video "<normal video link>" --short "<Short link>"
```

It downloads a normal video at 1080p, the same video through a `&list=` link, a Short at
1080p, and an MP3. Each file is checked with ffprobe (video + AAC audio, really 1080p,
MP3 decodes cleanly), and the yt-dlp version is printed.

## Files

```
main.py            start-up: upgrade yt-dlp, run checks, serve (one process, many threads)
selftest.py        live YouTube acceptance test
app/bootstrap.py   pip install/upgrade (never imports yt_dlp)
app/config.py      secrets and limits
app/tools.py       finds ffmpeg, ffprobe, Deno; status checks
app/urls.py        YouTube link → video ID → clean watch URL
app/youtube.py     all yt-dlp calls: info, quality choices, download
app/media.py       ffprobe checks, AAC + faststart fix-up
app/jobs.py        background jobs, queue, progress, cleanup
app/cookies.py     cookie storage
app/errors.py      yt-dlp errors → plain English
app/updater.py     "Update yt-dlp" + restart
app/web.py         Flask routes, login, CSRF
templates/, static/  the page (HTML/CSS/JS)
tests/             offline tests
```
