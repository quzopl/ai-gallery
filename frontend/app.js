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

function openDetail(_id) { /* Task 16 */ }

// ---------- init ----------
(async function init() {
  const saved = localStorage.getItem("activeLibraryId");
  state.activeLibraryId = saved ? Number(saved) || null : null;
  await refreshLibraries();
  await loadImages();
})();
