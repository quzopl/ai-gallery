// AI Gallery — frontend. Vanilla JS.

const state = {
  libraries: [],
  activeLibraryId: null,
  filters: { model: null, lora: null, q: "" },
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
  allLi.textContent = "Wszystkie";
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
    li.append(name, count);
    ul.appendChild(li);
  }
}

function selectLibrary(id) {
  state.activeLibraryId = id;
  localStorage.setItem("activeLibraryId", id ?? "");
  renderLibraries();
  loadImages();
}

document.getElementById("btn-add-library").onclick = async () => {
  const path = prompt("Ścieżka do folderu:");
  if (!path) return;
  const name = prompt("Nazwa biblioteki (puste = nazwa folderu):") || null;
  try {
    await api("/api/libraries", {
      method: "POST",
      body: JSON.stringify({ path, name }),
    });
    toast("Biblioteka dodana — skanowanie w toku…");
    await refreshLibraries();
  } catch (err) {
    toast("Błąd: " + err.message);
  }
};

document.getElementById("btn-rescan").onclick = async () => {
  if (state.activeLibraryId == null) {
    toast("Wybierz bibliotekę");
    return;
  }
  await api(`/api/libraries/${state.activeLibraryId}/rescan`, { method: "POST" });
  toast("Rescan rozpoczęty");
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
      img.src = `/api/images/${id}/thumb`;
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
  div.dataset.id = img.id;
  if (img.id === state.selectedId) div.classList.add("selected");
  const i = document.createElement("img");
  i.dataset.id = img.id;
  i.alt = img.rel_path;
  div.appendChild(i);
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
  detailImg.src = `/api/images/${id}/file`;
  detailMeta.innerHTML = "";
  const dl = document.createElement("dl");
  function add(k, v) {
    if (v == null || v === "") return;
    const dt = document.createElement("dt"); dt.textContent = k;
    const dd = document.createElement("dd"); dd.textContent = String(v);
    dl.append(dt, dd);
  }
  add("Plik", d.rel_path);
  add("Wymiary", `${d.width}×${d.height}`);
  add("Źródło", d.source_kind);
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
  detailMeta.appendChild(dl);
  detailEl.dataset.imageId = id;
  detailEl.classList.remove("hidden");
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

document.getElementById("btn-copy-prompt").onclick = async () => {
  const id = Number(detailEl.dataset.imageId);
  const d = await api(`/api/images/${id}`);
  await navigator.clipboard.writeText(d.prompt || "");
  toast("Prompt skopiowany");
};

// ---------- facets + search ----------
async function refreshFacets() {
  const f = await api("/api/facets");
  renderFacets("filter-models", f.models, "model");
  renderFacets("filter-loras", f.loras, "lora");
}

function renderFacets(elementId, items, filterKey) {
  const ul = document.getElementById(elementId);
  ul.innerHTML = "";
  const allLi = document.createElement("li");
  allLi.textContent = "— wszystkie —";
  allLi.classList.toggle("active", state.filters[filterKey] == null);
  allLi.onclick = () => { state.filters[filterKey] = null; refreshFacets(); loadImages(); };
  ul.appendChild(allLi);
  for (const it of items) {
    const li = document.createElement("li");
    li.classList.toggle("active", state.filters[filterKey] === it.name);
    const n = document.createElement("span");
    n.textContent = it.name; n.title = it.name;
    const c = document.createElement("span"); c.className = "count"; c.textContent = it.count;
    li.append(n, c);
    li.onclick = () => {
      state.filters[filterKey] = state.filters[filterKey] === it.name ? null : it.name;
      refreshFacets(); loadImages();
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
      toast(`Skanuję: ${msg.scanned}/${msg.total}`);
      break;
    case "scan_done":
      toast(`Skan ukończony: +${msg.added} ~${msg.updated} -${msg.removed}`);
      refreshLibraries();
      refreshFacets();
      break;
    case "image_added":
      if (galleryEl.scrollTop < 50) {
        loadImages();
      } else {
        pendingNewImages++;
        liveBadge.textContent = `${pendingNewImages} nowych — kliknij aby pokazać`;
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

// ---------- init ----------
(async function init() {
  const saved = localStorage.getItem("activeLibraryId");
  state.activeLibraryId = saved ? Number(saved) || null : null;
  await refreshLibraries();
  await refreshFacets();
  await loadImages();
  connectWS();
})();
