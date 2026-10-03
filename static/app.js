// Video Saver - page logic. Plain JS, no build step.
//
//  1. Paste a link -> POST /api/info -> show the video card + quality choices
//  2. Download     -> POST /api/download -> a job appears in "Downloads"
//  3. While any job is queued/running, GET /api/jobs every second
//  4. Settings: status checks, cookies, "Update yt-dlp"
"use strict";

const $ = (sel, root = document) => root.querySelector(sel);
const CSRF = $('meta[name="csrf-token"]').content;

const state = {
  video: null,          // video shown on the card
  choices: [],
  infoRequest: 0,       // ignores stale responses if the user asks twice
  jobs: new Map(),      // job id -> <li> element
  pollTimer: null,
  statusTimer: null,
  status: null,
  bootId: null,
};

// ------------------------------------------------------------- helpers

/** Call the JSON API. Throws {message, hint, details} on any failure. */
async function api(path, { method = "GET", body, form } = {}) {
  const options = { method, headers: {}, credentials: "same-origin" };
  if (method !== "GET") options.headers["X-CSRF-Token"] = CSRF;
  if (form) {
    options.body = form;
  } else if (body !== undefined) {
    options.headers["Content-Type"] = "application/json";
    options.body = JSON.stringify(body);
  }
  let response;
  try {
    response = await fetch(path, options);
  } catch (err) {
    throw { message: "Can't reach the app.",
            hint: "Check your connection. If the app is restarting, wait a few seconds.",
            details: String(err), network: true };
  }
  if (response.status === 401) {
    location.href = "/login";
    throw { message: "Please log in again." };
  }
  let data = {};
  try { data = await response.json(); } catch (_) { /* not JSON */ }
  if (!response.ok) throw data.error || { message: `Request failed (HTTP ${response.status}).` };
  return data;
}

function formatBytes(bytes) {
  if (!bytes && bytes !== 0) return "";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let i = 0;
  while (bytes >= 1000 && i < units.length - 1) { bytes /= 1000; i++; }
  return `${bytes.toFixed(bytes >= 100 || i === 0 ? 0 : 1)} ${units[i]}`;
}

function formatDuration(seconds) {
  if (!seconds && seconds !== 0) return "";
  seconds = Math.round(seconds);
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = String(seconds % 60).padStart(2, "0");
  return h ? `${h}:${String(m).padStart(2, "0")}:${s}` : `${m}:${s}`;
}

function formatEta(seconds) {
  if (!seconds || !isFinite(seconds)) return "";
  if (seconds < 60) return `${Math.max(1, Math.round(seconds))} s left`;
  if (seconds < 3600) return `${Math.round(seconds / 60)} min left`;
  return `${formatDuration(seconds)} left`;
}

/** Fill a container with an error box: message, hint and collapsible details. */
function showError(container, err) {
  container.replaceChildren();
  if (!err) { container.hidden = true; return; }
  const box = document.createElement("div");
  box.className = "alert";
  box.setAttribute("role", "alert");
  const title = document.createElement("p");
  title.className = "alert-title";
  title.textContent = err.message || "Something went wrong.";
  box.append(title);
  if (err.hint) {
    const hint = document.createElement("p");
    hint.textContent = err.hint;
    box.append(hint);
  }
  if (err.details) {
    const details = document.createElement("details");
    const summary = document.createElement("summary");
    summary.textContent = "Details";
    const pre = document.createElement("pre");
    pre.textContent = err.details;
    details.append(summary, pre);
    box.append(details);
  }
  container.append(box);
  container.hidden = false;
}

/** Use the fallback thumbnail if the preferred one doesn't load. */
function setThumb(img, url, fallback) {
  img.onerror = () => {
    img.onerror = null;
    if (fallback && img.src !== fallback) img.src = fallback;
  };
  img.src = url || fallback || "";
}

// --------------------------------------------------------- video info

const urlForm = $("#urlForm");
const urlInput = $("#urlInput");
const getBtn = $("#getBtn");
const infoError = $("#infoError");
const loadingCard = $("#loadingCard");
const videoCard = $("#videoCard");
const downloadBtn = $("#downloadBtn");
const downloadError = $("#downloadError");

urlForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const url = urlInput.value.trim();
  if (!url) {
    showError(infoError, { message: "Paste a YouTube link first." });
    urlInput.focus();
    return;
  }
  const request = ++state.infoRequest;
  showError(infoError, null);
  showError(downloadError, null);
  videoCard.hidden = true;
  loadingCard.hidden = false;
  getBtn.disabled = true;
  try {
    const data = await api("/api/info", { method: "POST", body: { url } });
    if (request !== state.infoRequest) return;
    renderVideo(data);
  } catch (err) {
    if (request !== state.infoRequest) return;
    showError(infoError, err);
  } finally {
    if (request === state.infoRequest) {
      loadingCard.hidden = true;
      getBtn.disabled = !canDownload();
    }
  }
});

$("#pasteBtn").addEventListener("click", async () => {
  try {
    const text = (await navigator.clipboard.readText()).trim();
    if (text) {
      urlInput.value = text;
      urlForm.requestSubmit();
      return;
    }
  } catch (_) { /* clipboard blocked: fall through to the hint */ }
  urlInput.focus();
  $("#pasteHint").hidden = false;
  setTimeout(() => { $("#pasteHint").hidden = true; }, 4000);
});

function renderVideo({ video, choices, default_quality, warnings }) {
  state.video = video;
  state.choices = choices;

  setThumb($("#thumb"), video.thumbnail, video.thumbnail_fallback);
  $("#durationBadge").textContent = formatDuration(video.duration);
  $("#videoTitle").textContent = video.title;
  $("#videoChannel").textContent = video.channel;
  $("#shortBadge").hidden = !video.is_short;

  // Default: 1080p, else the best named quality below it, else the first one.
  const keys = choices.map((c) => c.key);
  const order = [default_quality, "720", "480", "360", "best", "mp3"];
  const selected = order.find((k) => keys.includes(k)) || keys[0];

  const list = $("#choiceList");
  list.replaceChildren(...choices.map((choice) => choiceElement(choice, choice.key === selected)));

  const notes = warnings || [];
  $("#warningsBox").hidden = notes.length === 0;
  $("#warningsText").textContent = notes.join("\n\n");

  downloadBtn.disabled = choices.length === 0 || !canDownload();
  if (choices.length === 0) {
    showError(downloadError, { message: "YouTube didn't offer any downloadable quality for this video.",
                               hint: "Update yt-dlp in Settings, or add cookies, then try again." });
  }
  videoCard.hidden = false;
}

function choiceElement(choice, checked) {
  const label = document.createElement("label");
  label.className = "choice";
  const input = document.createElement("input");
  input.type = "radio";
  input.name = "quality";
  input.value = choice.key;
  input.checked = checked;

  const body = document.createElement("span");
  body.className = "choice-body";
  const radio = document.createElement("span");
  radio.className = "choice-radio";
  const name = document.createElement("span");
  name.className = "choice-label";
  name.textContent = choice.label;
  const size = document.createElement("span");
  size.className = "choice-size";
  size.textContent = choice.size ? (choice.size_is_estimate ? "~" : "") + formatBytes(choice.size) : "size unknown";
  body.append(radio, name, size);

  if (choice.detail) {
    const detail = document.createElement("span");
    detail.className = "choice-detail";
    detail.textContent = choice.detail;
    body.append(detail);
  }
  if (choice.note) {
    const note = document.createElement("span");
    note.className = "choice-note";
    note.textContent = choice.note;
    body.append(note);
  }
  label.append(input, body);
  return label;
}

downloadBtn.addEventListener("click", async () => {
  const picked = $('input[name="quality"]:checked');
  if (!state.video || !picked) return;
  showError(downloadError, null);
  downloadBtn.disabled = true;
  try {
    const data = await api("/api/download", {
      method: "POST",
      body: { url: `https://www.youtube.com/watch?v=${state.video.id}`, quality: picked.value },
    });
    upsertJob(data.job);
    $("#jobsSection").hidden = false;
    $(`[data-id="${data.job.id}"]`)?.scrollIntoView({ behavior: "smooth", block: "nearest" });
    schedulePoll(0);
  } catch (err) {
    showError(downloadError, err);
  } finally {
    // Short pause so a double click doesn't start two downloads.
    setTimeout(() => { downloadBtn.disabled = !canDownload() || state.choices.length === 0; }, 800);
  }
});

// -------------------------------------------------------------- jobs

const jobList = $("#jobList");
const jobTemplate = $("#jobTemplate");

function upsertJob(job) {
  let el = state.jobs.get(job.id);
  if (!el) {
    el = jobTemplate.content.firstElementChild.cloneNode(true);
    el.dataset.id = job.id;
    setThumb($(".job-thumb", el), job.thumbnail, job.thumbnail_fallback);
    $(".job-title", el).textContent = job.title;
    $(".job-remove", el).addEventListener("click", () => removeJob(job.id));
    state.jobs.set(job.id, el);
    jobList.prepend(el);
  }
  el.dataset.state = job.state;

  const active = job.state === "queued" || job.state === "running";
  $(".job-quality", el).textContent = job.quality_label;
  $(".job-remove", el).hidden = active;

  // One bar for the whole job.
  const bar = $(".progress-bar", el);
  bar.style.width = `${job.progress}%`;
  $(".progress", el).setAttribute("aria-valuenow", Math.round(job.progress));
  $(".job-progress", el).hidden = job.state === "error" || job.state === "expired";

  let stage = job.stage;
  if (job.state === "queued" && job.queue_position) stage = `Waiting in queue (#${job.queue_position})`;
  else if (job.state === "running") stage = `${job.stage} · ${Math.floor(job.progress)}%`;
  $(".job-stage", el).textContent = stage;

  const numbers = [];
  if (job.state === "running" && job.speed) numbers.push(`${formatBytes(job.speed)}/s`);
  if (job.state === "running" && job.eta) numbers.push(formatEta(job.eta));
  if (job.state === "running" && job.total) numbers.push(`${formatBytes(job.downloaded)} of ${formatBytes(job.total)}`);
  $(".job-numbers", el).textContent = numbers.join(" · ");

  // Finished: Save button + what's really in the file.
  const done = $(".job-done", el);
  done.hidden = job.state !== "done";
  if (job.state === "done") {
    const save = $(".job-save", el);
    save.href = `/api/jobs/${job.id}/file`;
    save.setAttribute("download", job.download_name || "");
    save.title = job.download_name || "";
    const s = job.summary || {};
    const parts = s.width
      ? [`${s.width}×${s.height}`, s.video_codec, s.audio_codec ? `${s.audio_codec} audio` : null]
      : [s.audio_codec, s.audio_bitrate ? `${s.audio_bitrate} kbps` : null];
    parts.push(formatBytes(s.size));
    if (job.expires_at) {
      const minutes = Math.max(0, Math.round((job.expires_at * 1000 - Date.now()) / 60000));
      parts.push(`deleted in ${minutes} min`);
    }
    $(".job-file", el).textContent = parts.filter(Boolean).join(" · ");
  }

  const expired = $(".job-expired", el);
  expired.hidden = job.state !== "expired";
  expired.textContent = "The file was deleted from the server after 30 minutes. Download it again if you still need it.";

  const errorBox = $(".job-error", el);
  if (job.state === "error") {
    if (!errorBox.dataset.shown) {
      showError(errorBox, job.error);
      errorBox.dataset.shown = "1";
    }
  } else {
    errorBox.hidden = true;
  }
}

async function removeJob(id) {
  try {
    await api(`/api/jobs/${id}/remove`, { method: "POST" });
  } catch (_) { /* gone already */ }
  state.jobs.get(id)?.remove();
  state.jobs.delete(id);
  $("#jobsSection").hidden = state.jobs.size === 0;
}

async function pollJobs() {
  let jobs = [];
  try {
    jobs = (await api("/api/jobs")).jobs;
  } catch (err) {
    schedulePoll(3000);
    return;
  }
  const seen = new Set();
  // The server lists newest first; insert oldest first so prepend keeps order.
  for (const job of [...jobs].reverse()) {
    seen.add(job.id);
    upsertJob(job);
  }
  for (const [id, el] of state.jobs) {
    if (!seen.has(id)) { el.remove(); state.jobs.delete(id); }
  }
  $("#jobsSection").hidden = state.jobs.size === 0;
  const active = jobs.some((j) => j.state === "queued" || j.state === "running");
  schedulePoll(active ? 1000 : 20000);   // every second while downloading
}

function schedulePoll(delay) {
  clearTimeout(state.pollTimer);
  state.pollTimer = setTimeout(pollJobs, delay);
}

// ------------------------------------------------------- status/settings

const settingsDialog = $("#settingsDialog");

function isUpdating() {
  return ["waiting", "installing", "restarting"].includes(state.status?.update.state);
}

function canDownload() {
  return !state.status || (state.status.can_download && !isUpdating());
}

async function refreshStatus() {
  clearTimeout(state.statusTimer);
  let status;
  try {
    status = await api("/api/status");
  } catch (err) {
    // While the app restarts after an update it is briefly unreachable.
    if (isUpdating()) state.statusTimer = setTimeout(refreshStatus, 2000);
    return null;
  }
  // A new boot id means the app restarted (e.g. after an update).
  if (state.bootId && status.boot_id !== state.bootId) {
    location.reload();
    return status;
  }
  state.bootId = status.boot_id;
  state.status = status;
  renderStatus(status);
  return status;
}

function renderStatus(status) {
  // Header chip
  const chip = $("#statusChip");
  const dot = $(".dot", chip);
  const cookieWarning = status.cookies.warning;
  if (!status.can_download) {
    dot.className = "dot dot-bad";
    $(".chip-text", chip).textContent = "Not ready";
  } else if (cookieWarning) {
    dot.className = "dot dot-warn";
    $(".chip-text", chip).textContent = "Check cookies";
  } else {
    dot.className = "dot dot-ok";
    $(".chip-text", chip).textContent = "Ready";
  }

  // Banner when downloads are disabled
  const banner = $("#problemBanner");
  banner.hidden = status.can_download;
  if (!status.can_download) {
    banner.replaceChildren();
    const title = document.createElement("p");
    title.className = "alert-title";
    title.textContent = "Downloads are disabled until this is fixed:";
    const list = document.createElement("ul");
    for (const problem of status.problems) {
      const li = document.createElement("li");
      li.textContent = problem;
      list.append(li);
    }
    banner.append(title, list);
  }

  // Status list in Settings
  const checkList = $("#checkList");
  checkList.replaceChildren(...status.checks.map((check) => {
    const li = document.createElement("li");
    const dot = document.createElement("span");
    dot.className = "dot " + (check.ok ? (check.warning ? "dot-warn" : "dot-ok") : (check.optional ? "dot-off" : "dot-bad"));
    const row = document.createElement("div");
    row.className = "check-row";
    const label = document.createElement("span");
    label.className = "check-label";
    label.textContent = check.label;
    const detail = document.createElement("span");
    detail.className = "check-detail";
    detail.textContent = check.detail;
    row.append(label, detail);
    li.append(dot, row);
    if (check.warning) {
      const warn = document.createElement("span");
      warn.className = "check-warning";
      warn.textContent = check.warning;
      li.append(warn);
    }
    return li;
  }));

  // Cookies
  const c = status.cookies;
  $("#cookieState").textContent = c.loaded
    ? `Active: ${c.count} cookies from ${c.source} (${c.signed_in ? "signed in" : "not signed in"}).`
    : "No cookies loaded.";
  $("#cookieClear").disabled = !c.loaded;

  // Update
  const u = status.update;
  const updating = isUpdating();
  $("#updateBtn").disabled = updating;
  $("#updateBtn").textContent = updating ? "Updating…" : "Update yt-dlp";
  const msg = $("#updateMsg");
  msg.textContent = u.message || "";
  msg.className = "small " + (u.state === "failed" ? "msg-bad" : u.state === "done" ? "msg-ok" : "");
  $("#updateLogBox").hidden = !u.log;
  $("#updateLog").textContent = u.log || "";

  getBtn.disabled = !canDownload();
  downloadBtn.disabled = !canDownload() || state.choices.length === 0;
  if (updating) state.statusTimer = setTimeout(refreshStatus, 2000);
}

$("#statusChip").addEventListener("click", openSettings);
$("#settingsBtn").addEventListener("click", openSettings);
$("#settingsClose").addEventListener("click", () => settingsDialog.close());
settingsDialog.addEventListener("click", (e) => { if (e.target === settingsDialog) settingsDialog.close(); });

function openSettings() {
  $("#cookieMsg").textContent = "";
  settingsDialog.showModal();
  refreshStatus();
}

function cookieMessage(text, ok) {
  const msg = $("#cookieMsg");
  msg.textContent = text;
  msg.className = "small " + (ok ? "msg-ok" : "msg-bad");
}

async function saveCookies(request) {
  try {
    const data = await api("/api/cookies", request);
    const c = data.cookies;
    cookieMessage(`Saved: ${c.count} cookies (${c.signed_in ? "signed in" : "not signed in"}). They are used from now on.`, true);
    $("#cookieText").value = "";
    refreshStatus();
  } catch (err) {
    cookieMessage(err.message || "Couldn't save the cookies.", false);
  }
}

$("#cookieSave").addEventListener("click", () => {
  const text = $("#cookieText").value;
  if (!text.trim()) { cookieMessage("Paste the contents of cookies.txt first, or upload the file.", false); return; }
  saveCookies({ method: "POST", body: { text } });
});

$("#cookieFile").addEventListener("change", (e) => {
  const file = e.target.files[0];
  if (!file) return;
  const form = new FormData();
  form.append("file", file);
  e.target.value = "";
  saveCookies({ method: "POST", form });
});

$("#cookieClear").addEventListener("click", async () => {
  if (!confirm("Remove the active cookies? Downloads will run without them.")) return;
  try {
    await api("/api/cookies/clear", { method: "POST" });
    cookieMessage("Cookies removed.", true);
    refreshStatus();
  } catch (err) {
    cookieMessage(err.message, false);
  }
});

$("#updateBtn").addEventListener("click", async () => {
  try {
    const data = await api("/api/update", { method: "POST" });
    if (state.status) state.status.update = data.update;
    refreshStatus();
  } catch (err) {
    $("#updateMsg").textContent = err.message;
  }
});

// --------------------------------------------------------- theme/logout

$("#themeBtn").addEventListener("click", () => {
  const root = document.documentElement;
  const dark = root.dataset.theme
    ? root.dataset.theme === "dark"
    : matchMedia("(prefers-color-scheme: dark)").matches;
  root.dataset.theme = dark ? "light" : "dark";
  try { localStorage.setItem("theme", root.dataset.theme); } catch (_) { /* private mode */ }
});

$("#logoutBtn").addEventListener("click", async () => {
  try { await api("/logout", { method: "POST" }); } catch (_) { /* ignore */ }
  location.href = "/login";
});

// --------------------------------------------------------------- start

refreshStatus();
pollJobs();
// Re-check status now and then (cookie warnings, finished updates).
setInterval(refreshStatus, 30000);
