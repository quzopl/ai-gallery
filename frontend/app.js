// AI Gallery — frontend. Vanilla JS.

const state = {
  libraries: [],
  activeLibraryId: null,
  filters: { model: null, lora: null, q: "", favorite: false, tags: [] },
  images: [],
  nextCursor: null,
  selectedId: null,
};

// ---------- HTTP helpers ----------
async function api(path, opts = {}) {
  const r = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...opts,
  });
  if (!r.ok) {
    const text = await r.text();
    throw new Error(`${r.status}: ${text}`);
  }
  return r.json();
}

// ---------- toast ----------
const toastEl = document.getElementById("toast");
let toastTimer = null;
function toast(msg) {
  toastEl.textContent = msg;
  toastEl.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => toastEl.classList.remove("show"), 2500);
}

// ---------- libraries ----------
async function refreshLibraries() {
  state.libraries = await api("/api/libraries");
  renderLibraries();
}

function renderLibraries() {
  const ul = document.getElementById("libraries");
  ul.innerHTML = "";
  const allLi = document.createElement("li");
  allLi.textContent = "All";
  allLi.classList.toggle("active", state.activeLibraryId === null);
  allLi.onclick = () => selectLibrary(null);
  ul.appendChild(allLi);
  for (const L of state.libraries) {
    const li = document.createElement("li");
    li.classList.toggle("active", L.id === state.activeLibraryId);
    li.onclick = () => selectLibrary(L.id);
    const name = document.createElement("span");
    name.textContent = L.name;
    name.title = L.path;
    const count = document.createElement("span");
    count.className = "count";
    count.textContent = L.image_count;
    const del = document.createElement("button");
    del.className = "lib-del";
    del.textContent = "×";
    del.title = "Remove library (files untouched)";
    del.onclick = (e) => { e.stopPropagation(); deleteLibrary(L); };
    li.append(name, count, del);
    ul.appendChild(li);
  }
}

async function deleteLibrary(L) {
  if (!confirm(`Remove library "${L.name}" from AI Gallery?\n\nFiles on disk will NOT be touched.`)) return;
  try {
    await api(`/api/libraries/${L.id}`, { method: "DELETE" });
    if (state.activeLibraryId === L.id) {
      state.activeLibraryId = null;
      localStorage.setItem("activeLibraryId", "");
    }
    await refreshLibraries();
    await loadImages();
    toast(`Removed library "${L.name}"`);
  } catch (err) {
    toast("Error: " + err.message);
  }
}

function selectLibrary(id) {
  state.activeLibraryId = id;
  localStorage.setItem("activeLibraryId", id ?? "");
  renderLibraries();
  loadImages();
}

// ---------- folder picker modal ----------
const pickerEl = document.getElementById("picker");
const pickerPathEl = document.getElementById("picker-path");
const pickerListEl = document.getElementById("picker-list");
const pickerNameEl = document.getElementById("picker-name");
let pickerCurrentPath = null;

async function openPicker(startPath = null) {
  pickerEl.classList.remove("hidden");
  pickerNameEl.value = "";
  await loadPickerPath(startPath);
}

function closePicker() {
  pickerEl.classList.add("hidden");
}

async function loadPickerPath(path) {
  try {
    const url = path ? `/api/browse?path=${encodeURIComponent(path)}` : "/api/browse";
    const r = await api(url);
    pickerCurrentPath = r.path;
    const isRoots = r.is_roots === true;
    pickerPathEl.textContent = isRoots ? "(Drives)" : r.path;
    pickerPathEl.title = isRoots ? "Drives / filesystem roots" : r.path;
    document.getElementById("picker-up").disabled = !r.parent;
    document.getElementById("picker-up").dataset.parent = r.parent || "";
    document.getElementById("picker-add").disabled = isRoots;
    pickerListEl.innerHTML = "";
    for (const d of r.dirs) {
      const li = document.createElement("li");
      li.textContent = d.name;
      if (!d.readable) {
        li.classList.add("unreadable");
        li.title = "no read permission";
      } else {
        li.onclick = () => loadPickerPath(d.path);
      }
      pickerListEl.appendChild(li);
    }
    if (r.dirs.length === 0) {
      const li = document.createElement("li");
      li.style.color = "var(--fg-dim)";
      li.style.cursor = "default";
      li.textContent = "(no subdirectories)";
      li.style.pointerEvents = "none";
      pickerListEl.appendChild(li);
    }
  } catch (err) {
    toast("Error: " + err.message);
  }
}

document.getElementById("picker-up").onclick = (e) => {
  const parent = e.currentTarget.dataset.parent;
  if (parent) loadPickerPath(parent);
};
document.getElementById("picker-drives").onclick = () => loadPickerPath("__roots__");
document.getElementById("picker-close").onclick = closePicker;
document.getElementById("picker-cancel").onclick = closePicker;
document.getElementById("picker-add").onclick = async () => {
  if (!pickerCurrentPath || pickerCurrentPath === "__roots__") return;
  const name = pickerNameEl.value.trim() || null;
  try {
    await api("/api/libraries", {
      method: "POST",
      body: JSON.stringify({ path: pickerCurrentPath, name }),
    });
    toast("Library added — scanning…");
    closePicker();
    await refreshLibraries();
  } catch (err) {
    toast("Error: " + err.message);
  }
};

document.getElementById("btn-add-library").onclick = () => openPicker();

document.getElementById("btn-rescan").onclick = async () => {
  if (state.activeLibraryId == null) {
    toast("Select a library");
    return;
  }
  await api(`/api/libraries/${state.activeLibraryId}/rescan`, { method: "POST" });
  toast("Rescan started");
};

// ---------- gallery ----------
const galleryEl = document.getElementById("gallery");

let thumbObserver = null;
function ensureThumbObserver() {
  if (thumbObserver) return thumbObserver;
  thumbObserver = new IntersectionObserver((entries) => {
    for (const e of entries) {
      if (!e.isIntersecting) continue;
      const img = e.target;
      const id = img.dataset.id;
      img.src = window.__mockImg ? window.__mockImg(Number(id)) : `/api/images/${id}/thumb`;
      img.onload = () => img.classList.add("loaded");
      thumbObserver.unobserve(img);
    }
  }, { rootMargin: "200px" });
  return thumbObserver;
}

let scrollObserver = null;
function ensureScrollObserver(sentinel) {
  if (scrollObserver) scrollObserver.disconnect();
  scrollObserver = new IntersectionObserver((entries) => {
    for (const e of entries) {
      if (e.isIntersecting && state.nextCursor) {
        loadMore();
      }
    }
  }, { root: galleryEl, rootMargin: "400px" });
  scrollObserver.observe(sentinel);
}

function buildImagesQuery() {
  const params = new URLSearchParams();
  if (state.activeLibraryId != null) params.set("library_id", state.activeLibraryId);
  if (state.filters.model) params.set("model", state.filters.model);
  if (state.filters.lora) params.set("lora", state.filters.lora);
  if (state.filters.q) params.set("q", state.filters.q);
  if (state.filters.favorite) params.set("favorite", "true");
  for (const t of state.filters.tags) params.append("tag", t);
  params.set("limit", "200");
  if (state.nextCursor) params.set("cursor", state.nextCursor);
  return params.toString();
}

async function loadImages() {
  state.images = [];
  state.nextCursor = null;
  galleryEl.innerHTML = "";
  await loadMore();
}

async function loadMore() {
  const r = await api("/api/images?" + buildImagesQuery());
  for (const item of r.items) {
    state.images.push(item);
    galleryEl.appendChild(renderTile(item));
  }
  state.nextCursor = r.next_cursor;
  const old = galleryEl.querySelector(".sentinel");
  if (old) old.remove();
  if (state.nextCursor) {
    const s = document.createElement("div");
    s.className = "sentinel";
    s.style.gridColumn = "1 / -1";
    s.style.height = "1px";
    galleryEl.appendChild(s);
    ensureScrollObserver(s);
  }
}

function renderTile(img) {
  const div = document.createElement("div");
  div.className = "tile";
  if (img.is_favorite) div.classList.add("fav");
  div.dataset.id = img.id;
  if (img.id === state.selectedId) div.classList.add("selected");
  const i = document.createElement("img");
  i.dataset.id = img.id;
  i.alt = img.rel_path;
  div.appendChild(i);
  const star = document.createElement("span");
  star.className = "star-badge";
  star.textContent = "★";
  div.appendChild(star);
  div.onclick = () => selectImage(img.id);
  ensureThumbObserver().observe(i);
  return div;
}

function selectImage(id) {
  state.selectedId = id;
  for (const t of galleryEl.querySelectorAll(".tile")) {
    t.classList.toggle("selected", Number(t.dataset.id) === id);
  }
  openDetail(id);
}

// ---------- detail panel + lightbox ----------
const detailEl = document.getElementById("detail");
const detailImg = document.getElementById("detail-img");
const detailMeta = document.getElementById("detail-meta");
const lightboxEl = document.getElementById("lightbox");
const lightboxImg = document.getElementById("lightbox-img");

async function openDetail(id) {
  const d = await api(`/api/images/${id}`);
  detailImg.src = window.__mockImg ? window.__mockImg(Number(id)) : `/api/images/${id}/file`;
  detailMeta.innerHTML = "";
  const dl = document.createElement("dl");
  function add(k, v) {
    if (v == null || v === "") return;
    const dt = document.createElement("dt"); dt.textContent = k;
    const dd = document.createElement("dd"); dd.textContent = String(v);
    dl.append(dt, dd);
  }
  add("File", d.rel_path);
  add("Dimensions", `${d.width}×${d.height}`);
  add("Source", d.source_kind);
  add("Model", d.model_name);
  add("Sampler", d.sampler);
  add("Steps", d.steps);
  add("CFG", d.cfg);
  add("Seed", d.seed);
  if (d.loras && d.loras.length) {
    const dt = document.createElement("dt"); dt.textContent = "LoRA";
    const dd = document.createElement("dd");
    dd.innerHTML = d.loras.map(L => `· ${L.name}${L.strength != null ? ` (${L.strength})` : ""}`).join("<br>");
    dl.append(dt, dd);
  }
  add("Prompt", d.prompt);
  add("Negative", d.negative);
  if (d.prompt_json) {
    const dt = document.createElement("dt"); dt.textContent = "JSON";
    const dd = document.createElement("dd");
    const det = document.createElement("details");
    det.className = "json-details";
    const sum = document.createElement("summary"); sum.textContent = "structured JSON";
    const pre = document.createElement("pre");
    pre.className = "json-block";
    pre.textContent = d.prompt_json;
    det.append(sum, pre);
    dd.appendChild(det);
    dl.append(dt, dd);
  }
  detailMeta.appendChild(dl);
  document.getElementById("btn-copy-json").classList.toggle("hidden", !d.prompt_json);
  detailEl.dataset.imageId = id;
  detailEl.dataset.isFavorite = d.is_favorite ? "1" : "0";
  updateFavoriteButton(!!d.is_favorite);
  renderDetailTags(d.tags || []);
  detailEl.classList.remove("hidden");
}

function renderDetailTags(tags) {
  const chipsEl = document.getElementById("detail-tags-chips");
  chipsEl.innerHTML = "";
  for (const t of tags) {
    const chip = document.createElement("span");
    chip.className = "tag-chip";
    chip.textContent = "#" + t;
    const x = document.createElement("span");
    x.className = "x"; x.textContent = "×"; x.title = "Remove tag";
    x.onclick = (e) => { e.stopPropagation(); removeDetailTag(t); };
    chip.appendChild(x);
    chipsEl.appendChild(chip);
  }
  detailEl.dataset.tags = JSON.stringify(tags);
}

async function commitDetailTags(tags) {
  const id = Number(detailEl.dataset.imageId);
  if (!id) return;
  try {
    await api(`/api/images/${id}/tags`, {
      method: "POST",
      body: JSON.stringify({ tags }),
    });
    renderDetailTags(tags);
    await refreshTagFacets();
  } catch (err) {
    toast("Error: " + err.message);
  }
}

async function removeDetailTag(name) {
  const tags = JSON.parse(detailEl.dataset.tags || "[]").filter(t => t !== name);
  await commitDetailTags(tags);
}

document.getElementById("detail-tag-form").onsubmit = async (e) => {
  e.preventDefault();
  const input = document.getElementById("detail-tag-input");
  const name = input.value.trim();
  if (!name) return;
  const tags = JSON.parse(detailEl.dataset.tags || "[]");
  if (tags.includes(name)) {
    toast("Already has this tag");
    return;
  }
  tags.push(name);
  input.value = "";
  await commitDetailTags(tags);
};

function updateFavoriteButton(isFav) {
  const btn = document.getElementById("btn-favorite");
  btn.textContent = isFav ? "★ Ulubione" : "☆ Ulubione";
  btn.classList.toggle("active", isFav);
}

function closeDetail() {
  detailEl.classList.add("hidden");
  state.selectedId = null;
  for (const t of galleryEl.querySelectorAll(".tile.selected")) {
    t.classList.remove("selected");
  }
}

document.getElementById("detail-close").onclick = closeDetail;

detailImg.onclick = () => {
  lightboxImg.src = detailImg.src;
  lightboxEl.classList.remove("hidden");
};
lightboxEl.onclick = () => lightboxEl.classList.add("hidden");

document.getElementById("btn-favorite").onclick = async () => {
  const id = Number(detailEl.dataset.imageId);
  if (!id) return;
  const newVal = detailEl.dataset.isFavorite !== "1";
  try {
    await api(`/api/images/${id}/favorite`, {
      method: "POST",
      body: JSON.stringify({ value: newVal }),
    });
    detailEl.dataset.isFavorite = newVal ? "1" : "0";
    updateFavoriteButton(newVal);
    // update local state + tile badge
    const img = state.images.find(x => x.id === id);
    if (img) img.is_favorite = newVal ? 1 : 0;
    const tile = galleryEl.querySelector(`.tile[data-id="${id}"]`);
    if (tile) tile.classList.toggle("fav", newVal);
    toast(newVal ? "Added to favorites" : "Removed from favorites");
  } catch (err) {
    toast("Error: " + err.message);
  }
};

document.getElementById("btn-copy-prompt").onclick = async () => {
  const id = Number(detailEl.dataset.imageId);
  const d = await api(`/api/images/${id}`);
  await navigator.clipboard.writeText(d.prompt || "");
  toast("Prompt copied");
};

document.getElementById("btn-copy-json").onclick = async () => {
  const id = Number(detailEl.dataset.imageId);
  const d = await api(`/api/images/${id}`);
  if (!d.prompt_json) { toast("No JSON for this image"); return; }
  await navigator.clipboard.writeText(d.prompt_json);
  toast("JSON copied");
};

// ---------- facets + search ----------
async function refreshFacets() {
  const f = await api("/api/facets");
  renderFacetCloud("filter-models", f.models, "model");
  renderFacetCloud("filter-loras", f.loras, "lora");
  await refreshTagFacets();
}

// Czyszczenie nazwy: ucinamy popularne sufiksy modeli/lor.
function cleanFacetName(s) {
  if (!s) return s;
  return s.replace(/\.(safetensors|ckpt|pt|sft|gguf|bin)$/i, "");
}

// Skala fontu wg liczności (12-15px).
function fontSizeForCount(count, cmin, cmax) {
  if (cmax === cmin) return 13;
  const t = (count - cmin) / (cmax - cmin);
  return Math.round(12 + t * 3);
}

function renderFacetCloud(elementId, items, filterKey) {
  const ul = document.getElementById(elementId);
  ul.className = "facets cloud";
  ul.innerHTML = "";

  const allLi = document.createElement("li");
  allLi.className = "reset";
  allLi.textContent = "all";
  allLi.classList.toggle("active", state.filters[filterKey] == null);
  allLi.onclick = () => { state.filters[filterKey] = null; refreshFacets(); loadImages(); };
  ul.appendChild(allLi);

  if (items.length === 0) return;
  const counts = items.map(it => it.count);
  const cmin = Math.min(...counts), cmax = Math.max(...counts);

  for (const it of items) {
    const li = document.createElement("li");
    li.classList.toggle("active", state.filters[filterKey] === it.name);
    li.style.fontSize = fontSizeForCount(it.count, cmin, cmax) + "px";
    const label = document.createElement("span");
    label.className = "label";
    label.textContent = cleanFacetName(it.name);
    label.title = it.name;
    const c = document.createElement("span"); c.className = "count"; c.textContent = it.count;
    li.append(label, c);
    li.onclick = () => {
      state.filters[filterKey] = state.filters[filterKey] === it.name ? null : it.name;
      refreshFacets(); loadImages();
    };
    ul.appendChild(li);
  }
}

async function refreshTagFacets() {
  const tags = await api("/api/tags");
  const ul = document.getElementById("filter-tags");
  ul.innerHTML = "";
  if (tags.length === 0) {
    const li = document.createElement("li");
    li.style.color = "var(--fg-dim)";
    li.style.cursor = "default";
    li.style.fontStyle = "italic";
    li.textContent = "(none — add tags in detail panel)";
    ul.appendChild(li);
    return;
  }
  for (const t of tags) {
    const li = document.createElement("li");
    li.className = "tag-facet";
    const active = state.filters.tags.includes(t.name);
    li.classList.toggle("active", active);
    const n = document.createElement("span");
    n.textContent = (active ? "✓ " : "") + "#" + t.name;
    n.title = t.name;
    const c = document.createElement("span"); c.className = "count"; c.textContent = t.count;
    li.append(n, c);
    li.onclick = () => {
      const idx = state.filters.tags.indexOf(t.name);
      if (idx >= 0) state.filters.tags.splice(idx, 1);
      else state.filters.tags.push(t.name);
      refreshTagFacets();
      loadImages();
    };
    ul.appendChild(li);
  }
}


const searchEl = document.getElementById("search");
searchEl.addEventListener("keydown", (e) => {
  if (e.key !== "Enter") return;
  state.filters.q = searchEl.value.trim();
  loadImages();
});

// ---------- WebSocket ----------
let ws = null;
let wsReconnectTimer = null;

function connectWS() {
  const proto = location.protocol === "https:" ? "wss:" : "ws:";
  ws = new WebSocket(`${proto}//${location.host}/ws`);
  ws.onopen = () => {
    clearTimeout(wsReconnectTimer);
    setInterval(() => { if (ws.readyState === WebSocket.OPEN) ws.send("ping"); }, 30000);
  };
  ws.onmessage = (ev) => {
    let msg; try { msg = JSON.parse(ev.data); } catch { return; }
    handleWSMessage(msg);
  };
  ws.onclose = () => {
    wsReconnectTimer = setTimeout(connectWS, 2000);
  };
  ws.onerror = () => ws.close();
}

let pendingNewImages = 0;
const liveBadge = document.createElement("div");
liveBadge.style.cssText = "position:absolute;top:8px;left:50%;transform:translateX(-50%);" +
  "background:var(--accent);color:white;padding:6px 12px;border-radius:4px;cursor:pointer;display:none;z-index:50;";
liveBadge.onclick = () => {
  pendingNewImages = 0;
  liveBadge.style.display = "none";
  galleryEl.scrollTo({ top: 0, behavior: "smooth" });
  loadImages();
};
galleryEl.parentElement.style.position = "relative";
galleryEl.parentElement.appendChild(liveBadge);

function handleWSMessage(msg) {
  switch (msg.type) {
    case "scan_progress":
      toast(`Scanning: ${msg.scanned}/${msg.total}`);
      break;
    case "scan_done":
      toast(`Scan complete: +${msg.added} ~${msg.updated} -${msg.removed}`);
      refreshLibraries();
      refreshFacets();
      break;
    case "image_added":
      if (galleryEl.scrollTop < 50) {
        loadImages();
      } else {
        pendingNewImages++;
        liveBadge.textContent = `${pendingNewImages} new — click to show`;
        liveBadge.style.display = "block";
      }
      break;
    case "image_removed": {
      const tile = galleryEl.querySelector(`.tile[data-id="${msg.image_id}"]`);
      if (tile) tile.remove();
      if (state.selectedId === msg.image_id) closeDetail();
      break;
    }
    case "image_changed":
      if (state.selectedId === msg.image_id) openDetail(msg.image_id);
      break;
  }
}

// ---------- file operations from UI ----------
document.getElementById("btn-delete").onclick = async () => {
  const id = Number(detailEl.dataset.imageId);
  if (!id) return;
  const d = state.images.find(x => x.id === id);
  if (!confirm(`Move to trash?\n${d?.rel_path || id}`)) return;
  try {
    await api(`/api/images/${id}`, { method: "DELETE" });
    toast("Moved to trash");
    closeDetail();
  } catch (err) {
    toast("Error: " + err.message);
  }
};

document.getElementById("btn-rename").onclick = async () => {
  const id = Number(detailEl.dataset.imageId);
  if (!id) return;
  const d = state.images.find(x => x.id === id);
  const currentName = (d?.rel_path || "").split("/").pop();
  const newName = prompt("New filename:", currentName);
  if (!newName || newName === currentName) return;
  try {
    await api(`/api/images/${id}/rename`, {
      method: "POST",
      body: JSON.stringify({ new_name: newName }),
    });
    toast("Renamed");
    await loadImages();
    openDetail(id);
  } catch (err) {
    toast("Error: " + err.message);
  }
};

// ---------- prefs ----------
function loadPrefs() {
  const t = Number(localStorage.getItem("tileSize") || "180");
  document.documentElement.style.setProperty("--tile", `${t}px`);
}
function setTileSize(px) {
  const clamped = Math.max(80, Math.min(400, px));
  localStorage.setItem("tileSize", String(clamped));
  document.documentElement.style.setProperty("--tile", `${clamped}px`);
}
document.getElementById("btn-tile-smaller").onclick = () => {
  setTileSize(parseFloat(getComputedStyle(document.documentElement).getPropertyValue("--tile")) - 30);
};
document.getElementById("btn-tile-bigger").onclick = () => {
  setTileSize(parseFloat(getComputedStyle(document.documentElement).getPropertyValue("--tile")) + 30);
};

// ---------- hotkeys ----------
document.addEventListener("keydown", (e) => {
  if (["INPUT", "TEXTAREA"].includes(document.activeElement?.tagName)) {
    if (e.key === "Escape") document.activeElement.blur();
    return;
  }
  if (e.key === "/") { e.preventDefault(); searchEl.focus(); return; }
  if (e.key === "Escape") {
    if (!pickerEl.classList.contains("hidden")) {
      closePicker();
    } else if (!lightboxEl.classList.contains("hidden")) {
      lightboxEl.classList.add("hidden");
    } else {
      closeDetail();
    }
    return;
  }
  if (e.key === "ArrowLeft" || e.key === "ArrowRight") {
    if (state.selectedId == null) return;
    const idx = state.images.findIndex(x => x.id === state.selectedId);
    const nextIdx = e.key === "ArrowLeft" ? idx - 1 : idx + 1;
    const next = state.images[nextIdx];
    if (next) selectImage(next.id);
    return;
  }
  if (e.key === "Delete" && state.selectedId != null) {
    document.getElementById("btn-delete").click();
    return;
  }
  if ((e.ctrlKey || e.metaKey) && e.key === "c" && state.selectedId != null) {
    document.getElementById("btn-copy-prompt").click();
    return;
  }
  if ((e.ctrlKey || e.metaKey) && (e.key === "-" || e.key === "=")) {
    e.preventDefault();
    if (e.key === "-") document.getElementById("btn-tile-smaller").click();
    else document.getElementById("btn-tile-bigger").click();
  }
});

// ---------- quick filters (Wszystkie / Ulubione) ----------
function renderQuickFilters() {
  document.getElementById("qf-all").classList.toggle("active", !state.filters.favorite);
  document.getElementById("qf-favorites").classList.toggle("active", state.filters.favorite);
}
document.getElementById("qf-all").onclick = () => {
  state.filters.favorite = false;
  renderQuickFilters();
  loadImages();
};
document.getElementById("qf-favorites").onclick = () => {
  state.filters.favorite = true;
  renderQuickFilters();
  loadImages();
};

// ---------- init ----------
(async function init() {
  loadPrefs();
  const saved = localStorage.getItem("activeLibraryId");
  state.activeLibraryId = saved ? Number(saved) || null : null;
  renderQuickFilters();
  await refreshLibraries();
  await refreshFacets();
  await loadImages();
  connectWS();
})();
