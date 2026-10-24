// Library: every file with its status and tags. It only uses the files API:
//   GET /files           when the page opens, on Refresh, and on "files-changed"
//                        (fired by upload.js after an upload)
//   GET /files/{sha256}  to poll each unfinished card until READY or FAILED

const POLL_INTERVAL = 3000; // ms between status checks of one file
const POLL_LIMIT = 15 * 60 * 1000; // give up after 15 min; a failed attempt is retried after 7.5 min
const FINISHED = ["READY", "FAILED"];

const grid = document.getElementById("library");
const fileCount = document.getElementById("file-count");
const libraryError = document.getElementById("library-error");
const refreshButton = document.getElementById("refresh");
const polling = new Set(); // file_ids with a poll loop running
let loading = false; // a GET /files is running
let reloadWanted = false; // "files-changed" arrived during it: load once more afterwards

refreshButton.addEventListener("click", loadFiles);
document.addEventListener("files-changed", loadFiles);
if (window.CONFIG) loadFiles();

async function loadFiles() {
  if (loading) { // one request at a time; an upload that finished meanwhile still shows up
    reloadWanted = true;
    return;
  }
  loading = true;
  setLoading(true);
  try {
    const response = await apiFetch("/files");
    if (!response.ok) throw new Error(`GET /files returned ${response.status}`);
    const { files } = await response.json();
    libraryError.hidden = true;
    grid.replaceChildren(...(files.length ? files.map(renderCard) : [emptyState()]));
    fileCount.textContent = files.length;
    files.filter((f) => !FINISHED.includes(f.status)).forEach((f) => poll(f.file_id));
  } catch (err) {
    libraryError.textContent = `Could not load the library: ${err.message}`;
    libraryError.hidden = false;
    grid.querySelector(".library-loading")?.remove(); // keep old cards, drop the spinner
  } finally {
    loading = false;
    setLoading(false);
    if (reloadWanted) {
      reloadWanted = false;
      loadFiles();
    }
  }
}

// While GET /files runs: Refresh is disabled with a small spinner, and the
// grid shows a big spinner if it has no cards yet. On Refresh the old cards
// stay until the new list arrives, so the page does not jump.
function setLoading(on) {
  refreshButton.disabled = on;
  refreshButton.replaceChildren(...(on ? [spinner("spinner-border-sm me-1")] : []), "Refresh");
  grid.setAttribute("aria-busy", on);
  if (on && !grid.querySelector("[data-file-id]")) {
    const box = el("div", "library-state library-loading text-center py-5");
    box.setAttribute("role", "status");
    box.append(spinner("text-secondary"), el("p", "small text-body-secondary mt-2 mb-0", "Loading files..."));
    grid.replaceChildren(box);
  }
}

function spinner(className) {
  const node = el("span", `spinner-border ${className}`);
  node.setAttribute("aria-hidden", "true");
  return node;
}

function emptyState() {
  const box = el("div", "library-state text-center text-body-secondary py-5");
  box.append(
    el("p", "h6 mb-1", "No files yet"),
    el("p", "small mb-0", "Upload a photo or video above and it will appear here."),
  );
  return box;
}

async function poll(fileId) {
  if (polling.has(fileId)) return; // the list was reloaded while this file is polled
  polling.add(fileId);
  const start = Date.now();
  try {
    while (Date.now() - start < POLL_LIMIT) {
      await new Promise((resolve) => setTimeout(resolve, POLL_INTERVAL));
      const response = await apiFetch(`/files/${fileId}`);
      const card = grid.querySelector(`[data-file-id="${fileId}"]`);
      if (response.status === 404) { // e.g. an abandoned reservation that TTL removed
        card?.remove();
        const left = grid.querySelectorAll("[data-file-id]").length;
        fileCount.textContent = left;
        if (left === 0) grid.replaceChildren(emptyState());
        return;
      }
      if (!response.ok) continue; // try again on the next round
      const file = await response.json();
      // Redraw only when the stage changes: each response has a freshly
      // signed preview URL, so redrawing would download the image again.
      if (card && card.dataset.stage !== stage(file)) card.replaceWith(renderCard(file));
      if (FINISHED.includes(file.status)) return;
    }
  } catch {
    // Network error: stop quietly; Refresh starts polling again.
  } finally {
    polling.delete(fileId);
  }
}

// One Bootstrap card per file. Everything from the API is set with
// textContent, never innerHTML, because file names come from users.
function renderCard(file) {
  const col = el("div", "col");
  col.dataset.fileId = file.file_id;
  col.dataset.stage = stage(file);
  const card = el("div", "card h-100 shadow-sm");
  col.append(card);

  card.append(renderPreview(file));
  const body = el("div", "card-body");
  card.append(body);

  const title = el("h3", "card-title h6 text-truncate", file.filename || file.file_id.slice(0, 12));
  title.title = file.filename;
  const when = new Date(file.created_at).toLocaleString();
  body.append(title, el("p", "card-text small text-body-secondary mb-2", `${file.file_type} · ${when}`));
  body.append(renderStatus(file));
  return col;
}

// The status, with PENDING split in two: the PUT to S3 is still running, or
// the file is in S3 and waits in the tagging queue (2 files are tagged at a time).
function stage(file) {
  if (file.status !== "PENDING") return file.status;
  return file.uploaded ? "QUEUED" : "UPLOADING";
}

function renderPreview(file) {
  if (!file.preview_url) {
    return el("div", "card-img-top preview preview-empty", "Waiting for upload");
  }
  if (file.file_type === "video") {
    const video = el("video", "card-img-top preview");
    video.src = file.preview_url;
    video.controls = true;
    video.preload = "metadata"; // only the first bytes until play is pressed
    return video;
  }
  const img = el("img", "card-img-top preview");
  img.src = file.preview_url;
  img.alt = file.filename;
  img.loading = "lazy";
  return img;
}

function renderStatus(file) {
  const box = el("div");
  if (stage(file) === "UPLOADING") {
    box.append(badge("Uploading...", "secondary"));
  } else if (stage(file) === "QUEUED") {
    box.append(badge("Queued for tagging", "secondary"));
  } else if (file.status === "PROCESSING") {
    box.append(badge("Detecting species...", "info"));
  } else if (file.status === "FAILED") {
    box.append(badge("Failed", "danger"), el("div", "small text-danger mt-1", file.error || ""));
  } else {
    const tags = Object.entries(file.tags);
    if (tags.length === 0) box.append(el("span", "small text-body-secondary", "No animals detected"));
    for (const [species, count] of tags) box.append(badge(`${species} × ${count}`, "success"));
    for (const attribute of file.attributes) box.append(badge(attribute, "secondary"));
  }
  return box;
}

function badge(text, colour) {
  return el("span", `badge text-bg-${colour} me-1`, text);
}

function el(tag, className = "", text = "") {
  const node = document.createElement(tag);
  node.className = className;
  node.textContent = text;
  return node;
}
