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

// ---------- gallery (placeholder w Task 15) ----------
async function loadImages() {
  // wypełni Task 15
}

// ---------- init ----------
(async function init() {
  const saved = localStorage.getItem("activeLibraryId");
  state.activeLibraryId = saved ? Number(saved) || null : null;
  await refreshLibraries();
  await loadImages();
})();
