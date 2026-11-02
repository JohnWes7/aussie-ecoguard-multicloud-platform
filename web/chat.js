// Chatbot: a floating chat box that talks to the Lex V2 bot through POST /chat
// (lambdas/chat). Lex reads the intent and slots, its fulfilment Lambda
// (lambdas/search) finds the files, and the reply comes back as
// {"message": "...", "results": [{filename, file_type, created_at, thumbnail_url, file_url}]}.
// Each result is a clickable thumbnail that opens the full-size file.
// This file adds its own elements, so index.html only has to load it.

(() => {
  const EXAMPLES = [
    "Show me all photos of foxes spotted yesterday",
    "Help me find a lost ginger cat",
    "Find feral pigs from last week",
  ];
  // One Lex conversation per tab; the server adds the user's ID.
  const sessionId = Math.random().toString(36).slice(2, 14);

  const box = document.createElement("section");
  box.className = "chat-box card shadow-lg";
  box.setAttribute("aria-label", "Chatbot");
  box.hidden = true;
  box.innerHTML = `
    <div class="card-header bg-dark text-white d-flex justify-content-between align-items-center py-2">
      <span class="fw-semibold">Aussie EcoGuard assistant</span>
      <button type="button" class="btn-close btn-close-white" aria-label="Close chat"></button>
    </div>
    <div class="chat-messages card-body overflow-auto" aria-live="polite"></div>
    <div class="chat-examples px-2 pb-1 d-flex flex-wrap gap-1"></div>
    <form class="card-footer p-2">
      <div class="input-group">
        <input type="text" class="form-control" maxlength="1024" autocomplete="off"
               placeholder="Ask me to find an animal..." aria-label="Message">
        <button class="btn btn-primary" type="submit">Send</button>
      </div>
    </form>`;

  const toggle = document.createElement("button");
  toggle.type = "button";
  toggle.className = "chat-toggle btn btn-primary rounded-pill shadow";
  toggle.textContent = "Chat";
  toggle.setAttribute("aria-label", "Open chatbot");

  document.body.append(box, toggle);

  const messages = box.querySelector(".chat-messages");
  const form = box.querySelector("form");
  const input = form.querySelector("input");
  const send = form.querySelector("button");
  const examples = box.querySelector(".chat-examples");

  bubble("bot", "Hi! Ask me to find animal sightings, or help you look for a lost pet.");
  for (const text of EXAMPLES) {
    const chip = el("button", "btn btn-sm btn-outline-secondary rounded-pill", text);
    chip.type = "button";
    chip.addEventListener("click", () => ask(text));
    examples.append(chip);
  }

  toggle.addEventListener("click", () => open(true));
  box.querySelector(".btn-close").addEventListener("click", () => open(false));
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    ask(input.value);
  });

  function open(on) {
    box.hidden = !on;
    toggle.hidden = on;
    if (on) input.focus();
  }

  async function ask(raw) {
    const text = raw.trim();
    if (!text || send.disabled) return;
    bubble("user", text);
    input.value = "";
    examples.classList.add("d-none"); // d-flex beats the hidden attribute
    send.disabled = true;
    const typing = bubble("bot", "…");
    typing.classList.add("chat-typing");
    try {
      const response = await apiFetch("/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text, sessionId }),
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(data.error || data.message || `the chat API returned ${response.status}`);
      typing.remove();
      bubble("bot", data.message || "Sorry, I didn't understand that.", data.results);
    } catch (err) {
      typing.remove();
      bubble("error", `Sorry, I couldn't answer that: ${err.message}`);
    } finally {
      send.disabled = false;
      input.focus();
    }
  }

  // Text always goes in with textContent, so a message can never inject HTML.
  function bubble(who, text, results = []) {
    const row = el("div", `chat-row chat-${who}`);
    const body = el("div", "chat-bubble", text);
    row.append(body);
    if (results && results.length) body.append(resultGrid(results));
    messages.append(row);
    messages.scrollTop = messages.scrollHeight;
    return row;
  }

  function resultGrid(results) {
    const grid = el("div", "chat-results");
    for (const file of results) {
      const link = el("a", "chat-result");
      link.href = file.file_url;
      link.target = "_blank";
      link.rel = "noopener";
      const name = file.filename || "file";
      const when = file.created_at ? new Date(file.created_at).toLocaleDateString() : "";
      link.title = `${name}${when ? ` · ${when}` : ""} (opens the full-size ${file.file_type || "file"})`;
      if (file.thumbnail_url) {
        const img = el("img");
        img.src = file.thumbnail_url;
        img.alt = name;
        img.loading = "lazy";
        img.addEventListener("error", () => img.replaceWith(el("span", "chat-result-name", name)), { once: true });
        link.append(img);
      } else {
        link.append(el("span", "chat-result-name", file.file_type === "video" ? `▶ ${name}` : name));
      }
      if (when) link.append(el("span", "chat-result-date", when));
      grid.append(link);
    }
    return grid;
  }

  function el(tag, className = "", text = "") {
    const node = document.createElement(tag);
    node.className = className;
    node.textContent = text;
    return node;
  }
})();
