// Lost pet tab, with two searches that both use POST /search (lambdas/search):
//   Lost pet:     {"lost_pet": {"species": "cat", "colours": ["orange"], "days": 30}}
//                 sightings of that species in everyone's files, newest first.
//                 "Use a photo" fills the form from detectFile(file) (nothing is stored).
//   Find original from a thumbnail URL: {"thumbnail_url": "..."} -> the full-size file.
// This file adds its own tab and panel, so index.html only has to load it.

(() => {
  // The model only gives colours for cats and dogs (infra/model/config.json).
  const PETS = ["cat", "dog"];
  const COLOURS = ["orange", "black", "white", "grey", "brown"];

  const tab = document.createElement("li");
  tab.className = "nav-item";
  tab.setAttribute("role", "presentation");
  tab.innerHTML = `
    <button class="nav-link" data-bs-toggle="tab" data-bs-target="#lost-pet-tab" type="button" role="tab">Lost pet</button>`;
  document.querySelector(".nav-tabs").append(tab);

  const panel = document.createElement("section");
  panel.id = "lost-pet-tab";
  panel.className = "tab-pane fade";
  panel.setAttribute("role", "tabpanel");
  panel.innerHTML = `
    <div class="card mb-4">
      <div class="card-header fw-semibold">Find a lost pet</div>
      <div class="card-body">
        <p>Search every sighting uploaded to Aussie EcoGuard for your pet. Choose its species and colours, or use a photo of it.</p>
        <form class="pet-form" novalidate>
          <fieldset>
            <div class="row g-3 align-items-end">
              <div class="col-sm-4 col-md-3">
                <label class="form-label" for="pet-species">Species</label>
                <select id="pet-species" class="form-select"></select>
              </div>
              <div class="col-sm-4 col-md-3">
                <label class="form-label" for="pet-days">Seen in the last</label>
                <select id="pet-days" class="form-select">
                  <option value="7">7 days</option>
                  <option value="30" selected>30 days</option>
                  <option value="">Any time</option>
                </select>
              </div>
              <div class="col-12">
                <span class="form-label d-block">Colours (any of them, none = all)</span>
                <div class="pet-colours d-flex flex-wrap gap-3"></div>
              </div>
              <div class="col-12 d-flex flex-wrap gap-2">
                <button class="btn btn-primary pet-search" type="submit">Search sightings</button>
                <label class="btn btn-outline-primary mb-0" for="pet-photo">Use a photo</label>
                <input id="pet-photo" type="file" accept="image/jpeg,image/png" hidden>
              </div>
            </div>
          </fieldset>
        </form>
        <div class="pet-status small text-body-secondary mt-3" role="status"></div>
      </div>
    </div>

    <div class="card mb-4">
      <div class="card-header fw-semibold">Find the original from a thumbnail URL</div>
      <div class="card-body">
        <form class="thumb-form" novalidate>
          <label class="form-label" for="thumb-url">Thumbnail URL (right-click a thumbnail in the Library, then Copy Image Address)</label>
          <div class="input-group">
            <input id="thumb-url" class="form-control" type="url" placeholder="https://.../thumbnails/<sha256>.jpg?..." required>
            <button class="btn btn-primary" type="submit">Find original</button>
          </div>
        </form>
        <div class="thumb-result mt-3" role="status"></div>
      </div>
    </div>

    <h2 class="h5 pet-results-title" hidden>Sightings <span class="badge text-bg-secondary pet-count"></span></h2>
    <div class="pet-results row row-cols-1 row-cols-sm-2 row-cols-md-3 row-cols-lg-4 g-3"></div>`;
  document.querySelector(".tab-content").append(panel);

  const $ = (selector) => panel.querySelector(selector);
  const fieldset = $(".pet-form fieldset");
  const species = $("#pet-species");
  const days = $("#pet-days");
  const status = $(".pet-status");
  const results = $(".pet-results");

  for (const pet of PETS) {
    species.append(new Option(capitalise(pet), pet));
  }
  const colourBoxes = COLOURS.map((colour) => {
    const wrap = document.createElement("div");
    wrap.className = "form-check";
    wrap.innerHTML = `<input class="form-check-input" type="checkbox" id="pet-colour-${colour}">
      <label class="form-check-label" for="pet-colour-${colour}"></label>`;
    const box = wrap.querySelector("input");
    box.value = colour;
    wrap.querySelector("label").textContent = capitalise(colour);
    $(".pet-colours").append(wrap);
    return box;
  });

  $(".pet-form").addEventListener("submit", (event) => {
    event.preventDefault();
    searchPets();
  });

  // A photo of the pet: detect its species and colours, put them in the form, search.
  $("#pet-photo").addEventListener("change", async (event) => {
    const file = event.target.files[0];
    event.target.value = "";
    if (!file) return;
    fieldset.disabled = true;
    status.textContent = "Looking at your photo...";
    try {
      const { tags, attributes } = await detectFile(file);
      const pet = PETS.find((p) => tags[p]);
      if (!pet) {
        const found = Object.keys(tags);
        status.textContent = found.length
          ? `No cat or dog in this photo (found: ${found.join(", ")}).`
          : "No animal found in this photo.";
        return;
      }
      species.value = pet;
      colourBoxes.forEach((box) => (box.checked = attributes.includes(box.value)));
      fieldset.disabled = false;
      await searchPets(`Found a ${pet}${attributes.length ? ` (${attributes.join(", ")})` : ""} in your photo. `);
    } catch (err) {
      status.textContent = "";
      showToast(`Could not check the photo: ${err.message}`, "error");
    } finally {
      fieldset.disabled = false;
    }
  });

  async function searchPets(prefix = "") {
    const colours = colourBoxes.filter((b) => b.checked).map((b) => b.value);
    const query = { species: species.value, colours };
    if (days.value) query.days = Number(days.value);

    fieldset.disabled = true;
    status.textContent = `${prefix}Searching...`;
    try {
      const body = await postSearch({ lost_pet: query });
      renderResults(body.results);
      const where = colours.length ? `${colours.join(" or ")} ${query.species}` : query.species;
      const n = body.results.length;
      status.textContent = `${prefix}${n} sighting${n === 1 ? "" : "s"} of ${where}` +
        (query.days ? ` in the last ${query.days} days.` : ".");
    } catch (err) {
      status.textContent = "";
      showToast(`Lost pet search failed: ${err.message}`, "error");
    } finally {
      fieldset.disabled = false;
    }
  }

  $(".thumb-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const out = $(".thumb-result");
    const url = $("#thumb-url").value.trim();
    if (!url) return;
    out.textContent = "Looking it up...";
    try {
      const body = await postSearch({ thumbnail_url: url });
      out.replaceChildren();
      const link = document.createElement("a");
      link.href = body.file_url;
      link.target = "_blank";
      link.rel = "noopener";
      link.textContent = `Open the original: ${body.filename || body.file_id.slice(0, 12)}`;
      const preview = body.file_type === "video" ? null : document.createElement("img");
      if (preview) {
        preview.src = body.file_url;
        preview.alt = body.filename || "Original file";
        preview.className = "img-fluid rounded d-block mt-2";
        preview.style.maxHeight = "320px";
      }
      out.append(link, ...(preview ? [preview] : []));
    } catch (err) {
      out.textContent = "";
      showToast(err.message, "error");
    }
  });

  async function postSearch(body) {
    const response = await apiFetch("/search", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.error || data.message || `POST /search returned ${response.status}`);
    return data;
  }

  // Cards like the Library's: the thumbnail opens the full-size file in a new tab.
  function renderResults(items) {
    $(".pet-results-title").hidden = false;
    $(".pet-count").textContent = items.length;
    results.replaceChildren();
    if (!items.length) {
      const empty = document.createElement("div");
      empty.className = "col-12 text-center py-4 text-body-secondary";
      empty.textContent = "No sightings match. Try fewer colours or a longer time.";
      results.append(empty);
      return;
    }
    for (const item of items) {
      const col = document.createElement("div");
      col.className = "col";
      const card = document.createElement("div");
      card.className = "card h-100 shadow-sm";

      const link = document.createElement("a");
      link.href = item.file_url;
      link.target = "_blank";
      link.rel = "noopener";
      link.title = "Open the full-size file";
      const preview = document.createElement(item.file_type === "video" && !item.thumbnail_url ? "video" : "img");
      preview.className = "card-img-top preview";
      preview.src = item.thumbnail_url || item.file_url;
      if (preview.tagName === "IMG") {
        preview.alt = item.filename;
        preview.addEventListener("error", () => { preview.src = item.file_url; }, { once: true });
      }
      link.append(preview);

      const body = document.createElement("div");
      body.className = "card-body";
      const title = document.createElement("h3");
      title.className = "card-title h6 text-truncate";
      title.textContent = item.filename;
      const meta = document.createElement("div");
      meta.className = "small text-body-secondary mb-2";
      meta.textContent = `Seen ${new Date(item.created_at).toLocaleString()}`;
      const badges = document.createElement("div");
      for (const [tag, count] of Object.entries(item.tags || {})) {
        badges.append(makeBadge(`${tag} × ${count}`, "text-bg-success"));
      }
      for (const colour of item.attributes || []) {
        badges.append(makeBadge(colour, "text-bg-light border"));
      }
      body.append(title, meta, badges);
      card.append(link, body);
      col.append(card);
      results.append(col);
    }
  }

  function makeBadge(text, classes) {
    const span = document.createElement("span");
    span.className = `badge ${classes} me-1`;
    span.textContent = text;
    return span;
  }

  function capitalise(text) {
    return text[0].toUpperCase() + text.slice(1);
  }
})();
