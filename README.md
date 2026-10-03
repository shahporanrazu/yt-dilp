# Video Saver: a private YouTube → MP4 downloader

A small password-protected web app that runs **on your own computer**. You paste a YouTube
link, pick a quality, and get an MP4 **with sound** (or an MP3). Everything YouTube-related
goes through [yt-dlp](https://github.com/yt-dlp/yt-dlp)'s Python API; merging, checking
and converting are done with ffmpeg/ffprobe.

- Python 3.11+, Flask, plain HTML/CSS/JS (no build step)
- One server process with many threads (Waitress), because download jobs live in memory
- `yt-dlp[default,deno]` is upgraded to the latest release **every time the app starts**
- Your home internet connection is rarely blocked by YouTube, so cookies are usually not needed

## Install (once)

You need two things on the computer: **Python 3.11 or newer** and **ffmpeg** (the real
program, which includes ffprobe; not the `ffmpeg` pip package). Deno, yt-dlp and everything
else are installed automatically on the first start.

**Mac** (with [Homebrew](https://brew.sh)):

```bash
brew install python ffmpeg
```

**Windows**: install Python from [python.org](https://www.python.org/downloads/) and tick
**"Add python.exe to PATH"** in the installer. Then, in a Command Prompt:

```bat
winget install Gyan.FFmpeg
```

**Linux** (Debian/Ubuntu): `sudo apt install python3 python3-venv ffmpeg`

Then get the app: on GitHub click **Code → Download ZIP** and unzip it, or
`git clone https://github.com/shahporanrazu/yt-dilp.git`.

## Start it

- **Mac / Linux**: open Terminal in the app folder and run `bash start.sh`
- **Windows**: double-click **`start.bat`**

On the first start it creates a private Python environment (`.venv`), installs everything
and asks you to **choose a password**. The password is saved in `settings.env` in the app
folder, which is readable only by you; edit that file to change it. Your browser then
opens at **http://localhost:8765**. Log in, and you stay logged in for 30 days.

To use it from your **phone**, connect the phone to the same Wi-Fi and open the
"From a phone on the same Wi-Fi" address shown in the terminal (e.g. `http://192.168.1.20:8765`).
Windows may ask whether to allow Python through the firewall: allow it on private networks.
If you only want it on this computer, add `HOST=127.0.0.1` to `settings.env`.

To **stop** it: press Ctrl+C in the terminal, or close the window.

### settings.env

One `KEY=value` per line. Everything except the password is optional:

| Setting | What it does |
|---|---|
| `APP_PASSWORD` | Password for the login page (set on the first start). |
| `PORT` | Port to use (default `8765`). |
| `HOST` | `0.0.0.0` (default, phones on your Wi-Fi can connect) or `127.0.0.1` (this computer only). |
| `PROXY_URL` | A proxy for yt-dlp, e.g. `socks5://127.0.0.1:1080` or `http://user:pass@host:port`. |
| `YT_COOKIES` | cookies.txt contents (single line with `\n` between lines). Easier: paste cookies in **Settings**. |
| `SESSION_SECRET` | Key for the login cookie. If unset, it's derived from the password. |

## Using it

1. Paste a link and press Enter or **Get video**. These all work: `youtube.com/watch?v=`,
   `youtu.be/`, `m.youtube.com`, `music.youtube.com`, `/shorts/` and `/live/` (finished
   streams only). Playlist and tracking parameters (`&list=`, `?si=`, `&t=`) are dropped.
2. Pick a quality. Only the qualities this video really has are shown, with estimated sizes.
3. **Download** starts a background job: one progress bar, the stage, speed and time left.
   At most 2 downloads run at once; the rest wait in a queue.
4. **Save file** gives you `<video title> [1080p].mp4`. Any language and emoji work.
   The app's own copy is deleted 30 minutes after the download finishes.

The green **Ready** chip at the top means yt-dlp, yt-dlp-ejs, Deno, ffmpeg and ffprobe were
all found. If something is missing, downloads are disabled and the page says exactly what
to install.

### Quality rules

| Option | yt-dlp format | format_sort |
|---|---|---|
| Best available (up to 4K) | `bv+ba/b` | `res`, `hdr:sdr`, `acodec:aac` |
| 1080p / 720p / 480p / 360p | `bv+ba/b` | `res:<H>`, `vcodec:h264`, `acodec:aac` |
| Audio only | `ba/b` → MP3 192 kbps | (default) |

- `res` is the **shorter side** of the frame, so a vertical 1080×1920 Short counts as 1080p.
- The named qualities prefer H.264 + AAC, which plays everywhere. Above 1080p YouTube only
  has VP9/AV1. The app says so next to the option (older devices may need VLC).
- Video is always merged to MP4. Every file is then checked with ffprobe: it **must** have
  a video stream and an audio stream, otherwise the download counts as failed. Non-AAC audio
  is re-encoded to AAC 192 kbps (the video is copied untouched), and every MP4 gets
  `-movflags +faststart`.
- Videos where YouTube offers no audio track at all are refused before downloading.
  Live streams that are still running are refused too.

## If YouTube blocks it, or a video needs sign-in

If you see **"YouTube is blocking this server"** ("Sign in to confirm you're not a bot"),
or for age-restricted, private and members-only videos:

1. Open a private/incognito window and sign in to YouTube (a spare Google account is safest).
2. Export youtube.com cookies with a cookies.txt extension (e.g. "Get cookies.txt LOCALLY").
3. Close the private window right away, so YouTube doesn't replace those cookies.
4. Paste or upload the file in **Settings → YouTube cookies**. It works immediately.

Cookies stay active until the app restarts. They're kept in a private temp file and are
never shown in the page or written to the logs.

**Update yt-dlp** (in Settings) installs the latest `yt-dlp[default,deno]` and restarts the
app once no download is running. Try it first when formats go missing or downloads fail
with HTTP 403. (yt-dlp is also upgraded on every normal start.)

## Testing

Offline tests. They need ffmpeg but not YouTube: real yt-dlp format selection, download,
merge, ffprobe checks and MP3 conversion run on generated media files.

```bash
.venv/bin/python -m pip install pytest
.venv/bin/python -m pytest
```

Live test against real YouTube. It uses the app's own code; the app doesn't need to be running:

```bash
.venv/bin/python selftest.py --short "https://youtube.com/shorts/<id>"
```

(On Windows use `.venv\Scripts\python`.) It downloads a normal video at 1080p, the same
video through a `&list=` link, a Short at 1080p, and an MP3. Each file is checked with
ffprobe (video + AAC audio, really 1080p, MP3 decodes cleanly), and the yt-dlp version is
printed. The default normal video is a 10-minute 4K film; add `--video "<link>"` to use
a shorter one.

## Files

```
start.sh / start.bat  start the app (creates .venv on the first run)
main.py            launcher: password on first start, restarts the server after updates
selftest.py        live YouTube acceptance test
app/bootstrap.py   pip install/upgrade (never imports yt_dlp)
app/config.py      settings (settings.env / environment) and limits
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
