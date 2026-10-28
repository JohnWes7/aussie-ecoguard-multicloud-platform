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

// Create and inject the search bar UI
const searchContainer = el("div", "d-flex gap-2 mb-3");
searchContainer.innerHTML = `
  <input type="text" id="search-input" class="form-control" placeholder="Enter a tag (e.g., fox, feral pig)">
  <button id="search-btn" class="btn btn-primary">Search</button>
  <button id="clear-search-btn" class="btn btn-outline-secondary" hidden>Clear</button>
`;
grid.parentNode.insertBefore(searchContainer, grid);

document.getElementById("search-btn").addEventListener("click", handleSearch);
document.getElementById("clear-search-btn").addEventListener("click", () => {
  document.getElementById("search-input").value = "";
  document.getElementById("clear-search-btn").hidden = true;
  loadFiles(); // Reload the normal library
});

// Create and inject the Chat Widget UI
const chatContainer = el("div", "position-fixed bottom-0 end-0 m-3 shadow-lg");
chatContainer.style.zIndex = "1050";
chatContainer.innerHTML = `
  <div class="card" style="width: 350px; height: 450px; display: flex; flex-direction: column;">
    <div class="card-header bg-dark text-white d-flex justify-content-between align-items-center py-2">
      <span>Aussie EcoGuard AI</span>
      <button class="btn-close btn-close-white" id="chat-close-btn"></button>
    </div>
    <div class="card-body overflow-auto flex-grow-1" id="chat-messages" style="background-color: #f8f9fa;">
      <div class="text-muted small text-center mb-2">Ask me to find an animal!</div>
    </div>
    <div class="card-footer p-2">
      <div class="input-group">
        <input type="text" id="chat-input" class="form-control" placeholder="Find me a fox...">
        <button class="btn btn-primary" id="chat-send-btn">Send</button>
      </div>
    </div>
  </div>
`;
document.body.append(chatContainer);

// Add a button to reopen the chat if closed
const chatToggleBtn = el("button", "btn btn-primary rounded-circle position-fixed bottom-0 end-0 m-3 shadow");
chatToggleBtn.innerHTML = "Chat";
chatToggleBtn.style.display = "none";
chatToggleBtn.style.zIndex = "1050";
document.body.append(chatToggleBtn);

document.getElementById("chat-close-btn").addEventListener("click", () => {
  chatContainer.style.display = "none";
  chatToggleBtn.style.display = "block";
});
chatToggleBtn.addEventListener("click", () => {
  chatContainer.style.display = "block";
  chatToggleBtn.style.display = "none";
});

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
  
  // Add action buttons (Delete)
  const actions = el("div", "mt-2 d-flex gap-2");
  const deleteBtn = el("button", "btn btn-sm btn-outline-danger", "Delete");
  deleteBtn.addEventListener("click", () => handleDelete(file.file_id));
  actions.append(deleteBtn);
  body.append(actions);

  const editTagBtn = el("button", "btn btn-sm btn-outline-primary", "Edit Tags");
  editTagBtn.addEventListener("click", () => handleEditTags(file.file_id));
  actions.append(editTagBtn);

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
  // READY images show the small thumbnail (tens of KB instead of the whole
  // photo) and open the original in a new tab when clicked. If the thumbnail
  // is not there (not made yet, or the image could not be read), the card
  // falls back to the original. Other stages only have the original.
  const img = el("img", "card-img-top preview");
  img.alt = file.filename;
  img.loading = "lazy";
  if (!file.thumb_url) {
    img.src = file.preview_url;
    return img;
  }
  img.addEventListener("error", () => { img.src = file.preview_url; }, { once: true });
  img.src = file.thumb_url;
  const link = el("a", "d-block");
  link.href = file.preview_url;
  link.target = "_blank";
  link.rel = "noopener";
  link.title = "Open the original";
  link.append(img);
  return link;
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

async function handleDelete(fileId) {
  if (!confirm("Are you sure you want to delete this file?")) return;

  try {
    const response = await apiFetch("/delete", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ urls: [fileId] }) // We just send the file_id
    });

    if (!response.ok) throw new Error("Failed to delete file");

    // Remove the card from the DOM instantly without reloading the page
    const card = document.querySelector(`[data-file-id="${fileId}"]`);
    if (card) card.remove();
    
  } catch (err) {
    alert(`Error: ${err.message}`);
  }
}

async function handleEditTags(fileId) {
  const tag = prompt("Enter the tag name (e.g., fox, cat, cane toad):");
  if (!tag) return; // User cancelled

  const opString = prompt("Type 1 to ADD the tag, or 0 to REMOVE the tag:");
  if (opString !== "0" && opString !== "1") {
    alert("Invalid operation. Must be 0 or 1.");
    return;
  }
  
  const operation = parseInt(opString);
  
  // The backend expects a dictionary of tags, e.g., {"fox": 1}
  const tagsDict = {};
  tagsDict[tag.toLowerCase()] = 1; 

  try {
    const response = await apiFetch("/tags", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        urls: [fileId],
        operation: operation,
        tags: tagsDict
      })
    });

    if (!response.ok) throw new Error("Failed to edit tags");

    alert("Tags updated successfully!");
    
    // Reload the page data to reflect the new tags
    loadFiles(); 
    
  } catch (err) {
    alert(`Error: ${err.message}`);
  }
}

async function handleSearch() {
  const input = document.getElementById("search-input");
  const tag = input.value.trim().toLowerCase();
  if (!tag) return;

  try {
    // Send {"tags": {"fox": 1}} to your search Lambda
    const response = await apiFetch("/search", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ tags: { [tag]: 1 } })
    });

    if (!response.ok) throw new Error("Search failed");
    
    const data = await response.json();
    const results = data.results || [];

    // Clear the current grid
    grid.replaceChildren();
    document.getElementById("clear-search-btn").hidden = false;

    if (results.length === 0) {
      grid.append(el("div", "text-center py-5 text-body-secondary", "No files found for this tag."));
      return;
    }

    // Render the results
    for (const item of results) {
      const col = el("div", "col");
      const card = el("div", "card h-100 shadow-sm");
      
      // Create the preview using the presigned URL from the search Lambda
      const img = el("img", "card-img-top preview");
      img.src = item.thumbnail_url || item.file_url;
      img.style.objectFit = "cover";
      img.style.height = "200px";
      
      const body = el("div", "card-body");
      const title = el("h3", "card-title h6 text-truncate", item.file_id.slice(0, 12));
      
      card.append(img, body);
      body.append(title);
      col.append(card);
      grid.append(col);
    }
    
  } catch (err) {
    alert(`Error: ${err.message}`);
  }
}

// Generate a unique session ID for this browser session
const CHAT_SESSION_ID = Math.random().toString(36).substring(2, 15);

async function sendChatMessage() {
  const input = document.getElementById("chat-input");
  const messagesDiv = document.getElementById("chat-messages");
  const text = input.value.trim();
  if (!text) return;

  // 1. Append the user's message to the chat window
  const userMsg = el("div", "mb-2 text-end");
  userMsg.innerHTML = `<span class="badge bg-primary text-wrap text-start" style="max-width: 80%;">${text}</span>`;
  messagesDiv.append(userMsg);
  input.value = ""; // Clear input
  messagesDiv.scrollTop = messagesDiv.scrollHeight; // Scroll to bottom

  // 2. Call the /chat Lambda
  try {
    const response = await apiFetch("/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text: text, sessionId: CHAT_SESSION_ID })
    });

    if (!response.ok) throw new Error("Failed to reach chat API");
    const data = await response.json();

    // 3. Append Lex's reply to the chat window
    const botMsg = el("div", "mb-2 text-start");
    botMsg.innerHTML = `<span class="badge bg-secondary text-wrap text-start" style="max-width: 80%;">${data.message}</span>`;
    messagesDiv.append(botMsg);
    messagesDiv.scrollTop = messagesDiv.scrollHeight; // Scroll to bottom

  } catch (err) {
    const errorMsg = el("div", "mb-2 text-start");
    errorMsg.innerHTML = `<span class="badge bg-danger text-wrap text-start">Error: ${err.message}</span>`;
    messagesDiv.append(errorMsg);
  }
}

// Add event listeners for clicking "Send" and pressing "Enter"
document.getElementById("chat-send-btn").addEventListener("click", sendChatMessage);
document.getElementById("chat-input").addEventListener("keypress", (e) => {
  if (e.key === "Enter") sendChatMessage();
});