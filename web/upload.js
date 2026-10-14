// Upload panel. For each chosen file:
//   1. compute its SHA-256 in the browser
//   2. POST /upload-url to reserve it (409 means it is already stored)
//   3. PUT the file straight to S3 with the presigned URL
// It only uses /upload-url and S3. When an upload finishes it fires a
// "files-changed" event (with no data) so the library reloads its own list.

const MAX_SIZE = 500 * 1024 * 1024; // crypto.subtle reads the whole file into memory
const ALLOWED_EXTENSIONS = ["jpg", "jpeg", "png", "mp4", "mov"]; // same list as upload-url

const picker = document.getElementById("files");
const uploads = document.getElementById("uploads");

// Check that the page can work at all before the user picks anything.
const problem = !window.CONFIG
  ? "config.js is missing. Deploy with Terraform (infra/terraform), or create it for a local copy (see README)."
  : !window.crypto?.subtle
    ? "This page must be opened over HTTPS (or on localhost) to compute checksums."
    : null;
if (problem) {
  const warning = document.getElementById("warning");
  warning.textContent = problem;
  warning.hidden = false;
  picker.disabled = true;
}

picker.addEventListener("change", async () => {
  const files = [...picker.files];
  picker.value = ""; // so choosing the same file again fires "change" again
  picker.disabled = true;
  // One file at a time: each file is read into memory to hash it.
  for (const file of files) {
    const row = addRow(file);
    try {
      await uploadFile(file, row);
    } catch (err) {
      row.finish(err.message, "error");
    }
  }
  picker.disabled = false;
});

async function uploadFile(file, row) {
  const ext = file.name.includes(".") ? file.name.split(".").pop().toLowerCase() : "";
  if (!ALLOWED_EXTENSIONS.includes(ext)) {
    throw new Error("Unsupported file type. Allowed: " + ALLOWED_EXTENSIONS.join(", "));
  }
  if (file.size > MAX_SIZE) {
    throw new Error("File is larger than 500 MB");
  }

  row.status("Computing checksum...");
  const sha256 = await sha256Hex(file);
  row.showHash(sha256);

  row.status("Requesting upload URL...");
  const response = await fetch(`${window.CONFIG.apiUrl}/upload-url`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ sha256, filename: file.name }),
  });
  // API Gateway's own errors (e.g. 502) may not be our JSON shape.
  const body = await response.json().catch(() => ({}));
  if (response.status === 409) {
    row.finish("Already uploaded, see the library below", "duplicate");
    return;
  }
  if (!response.ok) {
    throw new Error(body.error || body.message || `upload-url returned ${response.status}`);
  }

  row.status("Uploading...");
  await putFile(body.url, body.headers, file, (fraction) => row.progress(fraction));
  row.finish("Uploaded", "ok");
  document.dispatchEvent(new Event("files-changed"));
}

// Hex SHA-256 of the whole file, the same value `sha256sum` prints.
async function sha256Hex(file) {
  const digest = await crypto.subtle.digest("SHA-256", await file.arrayBuffer());
  return Array.from(new Uint8Array(digest), (b) => b.toString(16).padStart(2, "0")).join("");
}

// fetch() cannot report upload progress, so the PUT uses XMLHttpRequest.
function putFile(url, headers, file, onProgress) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("PUT", url);
    // Exactly the headers upload-url signed (Content-Type and the checksum);
    // a different value breaks the signature.
    for (const [name, value] of Object.entries(headers)) {
      xhr.setRequestHeader(name, value);
    }
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable) onProgress(e.loaded / e.total);
    };
    xhr.onload = () => {
      if (xhr.status === 200) return resolve();
      // S3 explains errors in XML, e.g. <Code>BadDigest</Code>.
      const code = xhr.responseXML?.querySelector("Code")?.textContent;
      reject(new Error(`S3 returned ${xhr.status}${code ? ` (${code})` : ""}`));
    };
    xhr.onerror = () => reject(new Error("Network error while uploading"));
    xhr.send(file);
  });
}

// One list item per file, with methods to update it.
function addRow(file) {
  const item = document.createElement("li");
  item.className = "list-group-item";
  item.innerHTML = `
    <div><span class="fw-semibold name"></span> <span class="text-body-secondary small size"></span></div>
    <div class="hash text-body-secondary font-monospace small"></div>
    <div class="progress my-1" role="progressbar" hidden><div class="progress-bar"></div></div>
    <div class="status small"></div>`;
  // textContent, not innerHTML: file names come from the user.
  item.querySelector(".name").textContent = file.name;
  item.querySelector(".size").textContent = `${(file.size / 1024 / 1024).toFixed(1)} MB`;
  uploads.prepend(item);

  const progress = item.querySelector(".progress");
  const status = item.querySelector(".status");
  return {
    status(text) {
      status.textContent = text;
    },
    showHash(sha256) {
      item.querySelector(".hash").textContent = `SHA-256 ${sha256}`;
    },
    progress(fraction) {
      const percent = Math.round(fraction * 100);
      progress.hidden = false;
      progress.firstElementChild.style.width = `${percent}%`;
      status.textContent = `Uploading... ${percent}%`;
    },
    finish(text, kind) { // kind: "ok", "duplicate" or "error"
      const colour = { ok: "success", duplicate: "warning", error: "danger" }[kind];
      progress.hidden = true;
      status.textContent = text;
      item.classList.add(`list-group-item-${colour}`);
    },
  };
}
