// Notifications tab. The user ticks the species they want to hear about and
// subscribes with POST /subscriptions {"tags": ["fox", "cane toad"]}. The
// subscribe Lambda takes their email from the sign-in token and subscribes it
// to SNS with a filter on those species. SNS first sends a confirmation email;
// no notification arrives until the user clicks the link in it.
// This file adds its own tab and panel, so index.html only has to load it.

// Wrapped in a function: the other scripts share the page's global names
// (spinner, problem, ...), and this file must not clash with them.
(() => {
  // The tags the model can give (infra/model/config.json "species" and
  // "unknown_tag"), so every tag on a file can be subscribed to.
  const SPECIES = [
    "fox", "rabbit", "feral pig", "cane toad", "cat", "dog",
    "deer", "goat", "horse", "camel", "cattle",
    "kangaroo", "possum", "wombat", "koala", "bandicoot", "echidna", "emu",
    "bird", "reptile", "frog", "rodent", "other animal",
  ];

  const tab = document.createElement("li");
  tab.className = "nav-item";
  tab.setAttribute("role", "presentation");
  tab.innerHTML = `
    <button class="nav-link" data-bs-toggle="tab" data-bs-target="#notifications-tab" type="button" role="tab">Notifications</button>`;
  document.querySelector(".nav-tabs").append(tab);

  const panel = document.createElement("section");
  panel.id = "notifications-tab";
  panel.className = "tab-pane fade";
  panel.setAttribute("role", "tabpanel");
  panel.innerHTML = `
    <div class="card">
      <div class="card-header fw-semibold">Email notifications</div>
      <div class="card-body">
        <p>Choose the species you want to hear about. When a new photo or video is tagged with one of them, you get an email with a link to it.</p>
        <form novalidate>
          <fieldset>
            <legend class="h6">Species</legend>
            <div class="species row row-cols-2 row-cols-sm-3 row-cols-md-4 g-2"></div>
            <div class="d-flex flex-wrap align-items-center gap-2 mt-3">
              <button class="btn btn-primary subscribe" type="submit" disabled>Subscribe</button>
              <button class="btn btn-outline-secondary btn-sm select-all" type="button">Select all</button>
              <button class="btn btn-outline-secondary btn-sm clear" type="button">Clear</button>
              <span class="selected-count text-body-secondary small"></span>
            </div>
          </fieldset>
        </form>
        <div class="result alert mt-3 mb-0" role="status" hidden></div>
      </div>
    </div>`;
  document.querySelector(".tab-content").append(panel);

  const form = panel.querySelector("form");
  const fieldset = panel.querySelector("fieldset");
  const subscribeButton = panel.querySelector(".subscribe");
  const selectedCount = panel.querySelector(".selected-count");
  const result = panel.querySelector(".result");

  const boxes = SPECIES.map((species, i) => {
    const cell = document.createElement("div");
    cell.className = "col";
    cell.innerHTML = `
      <div class="form-check">
        <input class="form-check-input" type="checkbox" id="species-${i}">
        <label class="form-check-label" for="species-${i}"></label>
      </div>`;
    const box = cell.querySelector("input");
    box.value = species;
    cell.querySelector("label").textContent = species[0].toUpperCase() + species.slice(1);
    panel.querySelector(".species").append(cell);
    return box;
  });

  const chosen = () => boxes.filter((box) => box.checked).map((box) => box.value);

  // Subscribe only works with at least one species and a config.js
  // (upload.js already shows the warning when config.js is missing).
  function update() {
    const count = chosen().length;
    selectedCount.textContent = count ? `${count} selected` : "Choose at least one species";
    subscribeButton.disabled = count === 0 || !window.CONFIG;
  }

  form.addEventListener("change", update);
  panel.querySelector(".select-all").addEventListener("click", () => {
    boxes.forEach((box) => (box.checked = true));
    update();
  });
  panel.querySelector(".clear").addEventListener("click", () => {
    boxes.forEach((box) => (box.checked = false));
    update();
  });
  update();

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const tags = chosen();
    if (!tags.length || !window.CONFIG) return;

    // Disabling the fieldset also disables every checkbox and button in it,
    // so the choice cannot change and Subscribe cannot be sent twice.
    fieldset.disabled = true;
    subscribeButton.innerHTML =
      '<span class="spinner-border spinner-border-sm me-1" aria-hidden="true"></span>Subscribing...';
    result.hidden = true;
    try {
      const response = await apiFetch("/subscriptions", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ tags }),
      });
      // API Gateway's own errors (e.g. 502) may not be our JSON shape.
      const body = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(body.error || body.message || `POST /subscriptions returned ${response.status}`);
      }
      showResult(
        "success",
        `Subscribed to ${tags.join(", ")}.`,
        `Check ${body.email || "your email"} for a message from AWS Notifications and click ` +
          `"Confirm subscription". Notifications only start after you confirm.`,
      );
      if (window.showToast) showToast("Check your email to confirm the subscription", "success");
    } catch (err) {
      // fetch() itself throws a TypeError when the server cannot be reached.
      const message = err instanceof TypeError ? "Could not reach the server" : err.message;
      showResult("danger", "Could not subscribe.", message);
      if (window.showToast) showToast(`Could not subscribe: ${message}`, "error");
    } finally {
      subscribeButton.textContent = "Subscribe";
      fieldset.disabled = false;
      update();
    }
  });

  // textContent, not innerHTML: the email and error text come from the server.
  function showResult(colour, title, text) {
    result.className = `result alert alert-${colour} mt-3 mb-0`;
    result.replaceChildren(document.createElement("strong"), ` ${text}`);
    result.firstChild.textContent = title;
    result.hidden = false;
  }
})();
